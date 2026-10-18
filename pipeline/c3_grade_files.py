"""C3: score file quality from extracted evidence, leaving unavailable metrics blank."""

from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.common import ensure_dirs, paths, read_csv

LOG = logging.getLogger(__name__)
WEIGHTS = {
    "provider_linkage": 0.30,
    "required_field_completeness": 0.25,
    "valid_numeric_rate": 0.20,
    "duplicate_control": 0.10,
    "active_rate_share": 0.10,
    "outlier_control": 0.05,
}
REQUIRED_COLUMNS = ("billing_code", "billing_code_type", "negotiation_arrangement", "negotiated_type", "rate_text")


def _mapping_codes(workbook: Path) -> set[str]:
    if not workbook.is_file():
        return set()
    try:
        import openpyxl
        book = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
        sheet = book["Spec-Proc Weights"]
        rows = sheet.iter_rows(values_only=True)
        header = [str(x or "").strip().upper() for x in next(rows)]
        idx = header.index("PROC_CD")
        codes = {str(row[idx]).strip().upper() for row in rows if row[idx] not in (None, "")}
        book.close()
        return codes
    except Exception:
        return set()


def _score(components: dict[str, float | None]) -> float | None:
    available = [(WEIGHTS[key], value) for key, value in components.items() if value is not None]
    if not available:
        return None
    denom = sum(weight for weight, _ in available)
    return round(100 * sum(weight * float(value) for weight, value in available) / denom, 2)


def _grade(value: float | None) -> str:
    if value is None:
        return "NOT_GRADED"
    return "A" if value >= 90 else "B" if value >= 80 else "C" if value >= 70 else "D" if value >= 60 else "E"


def _profile_file(
    observations: Path,
    file_id: str,
    mapped_codes: set[str],
    chunksize: int,
    temp_root: Path,
    asof: date,
    minimum_peer_count: int,
    outlier_multiple: float,
) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    rates_by_file: list[pd.DataFrame] = []
    code_set: set[str] = set()
    rate_types: Counter[str] = Counter()
    duplicate_db_path = temp_root / f"dedup_{file_id}.sqlite"
    db = sqlite3.connect(duplicate_db_path)
    db.execute("CREATE TABLE seen(k TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 1)")
    db.execute("CREATE TABLE amounts(code TEXT, cls TEXT, amount REAL)")
    try:
        for chunk in pd.read_csv(observations, dtype=str, keep_default_na=False, chunksize=chunksize):
            subset = chunk[chunk["file_id"] == file_id]
            if subset.empty:
                continue
            totals["rows"] += len(subset)
            for column in REQUIRED_COLUMNS:
                totals[f"filled_{column}"] += int(subset[column].astype(str).str.strip().ne("").sum())
            totals["linked_rows"] += int((subset["npi"].str.len() == 10).mul(subset["tin"].str.len() == 9).sum())
            totals["numeric_rows"] += int(subset["rate"].apply(lambda x: str(x).strip() not in {"", "nan", "None"}).sum())
            totals["nonnegative_rows"] += int(subset["rate"].apply(lambda x: str(x).strip() not in {"", "nan", "None"} and float(x) >= 0).sum())
            totals["nonzero_rows"] += int(subset["rate"].apply(lambda x: str(x).strip() not in {"", "nan", "None"} and float(x) != 0).sum())
            active = 0
            for expiry in subset["expiration_date"]:
                text = str(expiry).strip()[:10]
                if text:
                    try:
                        totals["expiry_present"] += 1
                        active += date.fromisoformat(text) >= asof
                    except ValueError:
                        totals["expiry_unparseable"] += 1
            totals["expiry_active"] += active
            codes = [str(v).strip().upper() for v in subset["billing_code"] if str(v).strip()]
            code_set.update(codes)
            rate_types.update(v.strip().lower() or "missing" for v in subset["negotiated_type"])

            for row in subset.itertuples(index=False):
                key = "|".join(
                    str(getattr(row, column, "")) for column in
                    ("npi", "tin", "billing_code", "billing_class", "negotiated_type", "rate", "expiration_date", "service_code", "modifier")
                )
                db.execute(
                    "INSERT INTO seen(k,n) VALUES (?,1) ON CONFLICT(k) DO UPDATE SET n=n+1",
                    (key,),
                )
                text = str(row.rate).strip()
                if text and text not in {"nan", "None"}:
                    try:
                        amount = float(text)
                        if amount >= 0 and str(row.billing_code).strip():
                            db.execute("INSERT INTO amounts(code,cls,amount) VALUES (?,?,?)", (str(row.billing_code), str(row.billing_class), amount))
                    except ValueError:
                        pass
        db.commit()
        total = int(totals["rows"])
        if not total:
            return {"file_id": file_id, "quality_status": "no_rate_rows", "quality_score": "", "grade": "NOT_GRADED"}

        duplicate_rows = int(db.execute("SELECT COALESCE(SUM(n-1),0) FROM seen").fetchone()[0])
        outlier_count = 0
        eligible_outlier_rows = 0
        groups = db.execute(
            "SELECT code,cls,COUNT(*) FROM amounts GROUP BY code,cls HAVING COUNT(*)>=?",
            (minimum_peer_count,),
        ).fetchall()
        db.execute("CREATE INDEX IF NOT EXISTS amounts_group_idx ON amounts(code,cls,amount)")
        for code, cls, count in groups:
            med = db.execute(
                "SELECT amount FROM amounts WHERE code=? AND cls=? ORDER BY amount LIMIT 1 OFFSET ?",
                (code, cls, (count - 1) // 2),
            ).fetchone()[0]
            if med <= 0:
                continue
            low, high = med / outlier_multiple, med * outlier_multiple
            outlier_count += int(
                db.execute(
                    "SELECT COUNT(*) FROM amounts WHERE code=? AND cls=? AND (amount<? OR amount>?)",
                    (code, cls, low, high),
                ).fetchone()[0]
            )
            eligible_outlier_rows += int(count)

        provider_link = totals["linked_rows"] / total
        field_completeness = sum(totals[f"filled_{c}"] / total for c in REQUIRED_COLUMNS) / len(REQUIRED_COLUMNS)
        numeric_valid = totals["nonnegative_rows"] / total
        duplicate_control = max(0.0, 1.0 - duplicate_rows / total)
        active_share = totals["expiry_active"] / totals["expiry_present"] if totals["expiry_present"] else None
        outlier_control = max(0.0, 1.0 - outlier_count / eligible_outlier_rows) if eligible_outlier_rows else None
        components = {
            "provider_linkage": provider_link,
            "required_field_completeness": field_completeness,
            "valid_numeric_rate": numeric_valid,
            "duplicate_control": duplicate_control,
            "active_rate_share": active_share,
            "outlier_control": outlier_control,
        }
        score = _score(components)
        return {
            "file_id": file_id,
            "quality_status": "graded",
            "quality_score": score if score is not None else "",
            "grade": _grade(score),
            "rate_rows": total,
            "unique_billing_codes": len(code_set),
            "mapped_code_share": sum(code in mapped_codes for code in code_set) / len(code_set) if code_set else "",
            "provider_linkage_share": round(provider_link, 6),
            "required_field_completeness": round(field_completeness, 6),
            "valid_nonnegative_rate_share": round(numeric_valid, 6),
            "nonzero_rate_share": round(totals["nonzero_rows"] / total, 6),
            "exact_duplicate_share": round(duplicate_rows / total, 6),
            "active_rate_share": "" if active_share is None else round(active_share, 6),
            "unparseable_expiration_count": totals["expiry_unparseable"],
            "outlier_share_among_comparable_rows": "" if not eligible_outlier_rows else round(outlier_count / eligible_outlier_rows, 6),
            "rate_type_distribution": json.dumps(rate_types, sort_keys=True),
            "component_scores": json.dumps(components, sort_keys=True),
        }
    finally:
        db.close()


def run(config: dict[str, Any], logger: logging.Logger | None = None) -> pd.DataFrame:
    logger = logger or LOG
    dirs = ensure_dirs(config)
    selected = read_csv(dirs["processed"] / "selected_ppo_files.csv")
    statuses_path = dirs["processed"] / "c4_file_status.csv"
    statuses = read_csv(statuses_path) if statuses_path.is_file() else pd.DataFrame()
    observations = dirs["processed"] / "rate_observations.csv"
    mapping_path = paths(config)["mapping"]
    mapped_codes = _mapping_codes(mapping_path)
    profile = config.get("pipeline", {})
    chunksize = int(profile.get("batch_size", 50000))
    asof_value = config.get("sources", {}).get("as_of_date")
    asof = date.fromisoformat(str(asof_value)) if asof_value else date.today()
    quality_config = profile.get("quality", {})
    minimum_peer_count = int(quality_config.get("minimum_peer_count_for_outlier_check", 10))
    outlier_multiple = float(quality_config.get("outlier_multiple", 10.0))
    result: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="quality_", dir=dirs["processed"]) as temporary:
        temp_root = Path(temporary)
        for record in selected[selected["selected_for_extraction"].str.lower().isin({"true", "1"})].to_dict(orient="records"):
            file_id = record["file_id"]
            status_match = statuses[statuses["file_id"] == file_id] if not statuses.empty else pd.DataFrame()
            status = str(status_match.iloc[0]["status"]) if not status_match.empty else "missing_rate_file"
            if status != "complete" or not observations.is_file() or observations.stat().st_size == 0:
                result.append(
                    {
                        "file_id": file_id,
                        "quality_status": "pending_rate_file",
                        "quality_score": "",
                        "grade": "NOT_GRADED",
                        "reason": "The selected index points to a rate-file URL, but its body was not available for profiling.",
                    }
                )
                continue
            row = _profile_file(
                observations,
                file_id,
                mapped_codes,
                chunksize,
                temp_root,
                asof,
                minimum_peer_count,
                outlier_multiple,
            )
            row["rate_file_url"] = record["rate_file_url"]
            row["mixed_plan_file"] = record.get("mixed_plan_file", "")
            row["network_attribution_status"] = record.get("rate_network_attribution", "")
            result.append(row)
    output = pd.DataFrame(result)
    output.to_csv(dirs["processed"] / "file_quality_metrics.csv", index=False)
    logger.info("C3 complete: %d selected files; %d graded", len(output), int((output.get("quality_status", pd.Series(dtype=str)) == "graded").sum()))
    return output
