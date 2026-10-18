"""C1: preserve every plan-to-file relationship from the Centene index JSON."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

from pipeline.common import ensure_dirs, iter_json_items, stable_file_id, stable_key, write_json

LOG = logging.getLogger(__name__)
LINK_COLUMNS = [
    "link_id", "file_id", "source_index_file", "published_date", "reporting_entity_name",
    "brand", "structure_index", "plan_name", "plan_id", "plan_id_type",
    "market_type", "state", "state_source", "network_description", "rate_file_url",
]


def _brand(filename: str, entity: str, plan_name: str) -> tuple[str, str]:
    text = filename.lower()
    for key, label in (
        ("ambetter", "Ambetter"),
        ("fidelis", "Fidelis"),
        ("qualchoice", "QualChoice"),
        ("healthnet", "Health Net"),
        ("health_net", "Health Net"),
    ):
        if key in text:
            return label, "index_filename"
    combined = f"{plan_name} {entity}".lower()
    for key, label in (
        ("ambetter", "Ambetter"),
        ("fidelis", "Fidelis"),
        ("qualchoice", "QualChoice"),
        ("health net", "Health Net"),
    ):
        if key in combined:
            return label, "plan_or_entity_name"
    return "Unknown", "unresolved"


def _state(plan_id: str, plan_id_type: str, plan_name: str, url: str) -> tuple[str, str]:
    if "hios" in (plan_id_type or "").lower():
        match = re.match(r"^\d{5}([A-Z]{2})", plan_id or "", re.I)
        if match:
            return match.group(1).upper(), "HIOS_plan_id"
    path = Path(urlsplit(url).path).name.lower()
    match = re.search(
        r"(?:ambetter|healthnet|qualchoice|fidelis)-([a-z]{2})_in[-_]network",
        path,
        re.I,
    )
    if match:
        return match.group(1).upper(), "rate_file_slug"
    match = re.search(r"\b(CA|AR|OR|NY|OK|IN|LA|NE|IL|MI|FL|AZ|TN|GA|KY|IA|TX|WA|MS|NC)\b", plan_name or "", re.I)
    if match:
        return match.group(1).upper(), "plan_name"
    return "", "unknown"


def _parse_index(filepath: Path) -> list[dict[str, Any]]:
    entity = next(iter_json_items(filepath, "reporting_entity_name"), "")
    published_match = re.search(r"(\d{4}-\d{2}-\d{2})", filepath.name)
    published = published_match.group(1) if published_match else ""
    rows: list[dict[str, Any]] = []
    for structure_no, structure in enumerate(iter_json_items(filepath, "reporting_structure.item")):
            plans = structure.get("reporting_plans") or []
            rate_files = structure.get("in_network_files") or []
            for plan in plans:
                plan_name = str(plan.get("plan_name") or "").strip()
                plan_id = str(plan.get("plan_id") or "").strip()
                plan_id_type = str(plan.get("plan_id_type") or "").strip()
                market_type = str(plan.get("plan_market_type") or "").strip().lower()
                for rate_file in rate_files:
                    url = str(rate_file.get("location") or "").strip()
                    if not url:
                        continue
                    brand, _ = _brand(filepath.name, str(entity), plan_name)
                    state, state_source = _state(plan_id, plan_id_type, plan_name, url)
                    file_id = stable_file_id(url)
                    link_id = stable_key((filepath.name, structure_no, plan_name, plan_id, url))
                    rows.append(
                        {
                            "link_id": link_id,
                            "file_id": file_id,
                            "source_index_file": filepath.name,
                            "published_date": published,
                            "reporting_entity_name": str(entity),
                            "brand": brand,
                            "structure_index": structure_no,
                            "plan_name": plan_name,
                            "plan_id": plan_id,
                            "plan_id_type": plan_id_type,
                            "market_type": market_type,
                            "state": state,
                            "state_source": state_source,
                            "network_description": str(rate_file.get("description") or ""),
                            "rate_file_url": url,
                        }
                    )
    return rows


def run(config: dict[str, Any], logger: logging.Logger | None = None) -> pd.DataFrame:
    logger = logger or LOG
    dirs = ensure_dirs(config)
    index_dir = dirs["indexes"]
    files = sorted(index_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"No index JSON files found under {index_dir}")

    rows: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for filepath in files:
        try:
            parsed = _parse_index(filepath)
            rows.extend(parsed)
            logger.info("C1 parsed %s: %d plan-file links", filepath.name, len(parsed))
        except Exception as exc:
            failed.append({"index_file": filepath.name, "error": str(exc)})
            logger.exception("C1 could not parse %s", filepath.name)

    links = pd.DataFrame(rows, columns=LINK_COLUMNS)
    if links.empty:
        raise ValueError("No plan-to-file links were parsed from the supplied index files.")
    raw_link_count = len(links)
    links = links.drop_duplicates(subset=["link_id"], keep="first").reset_index(drop=True)
    # Some supplied HIOS values contain only the issuer prefix. Resolve those
    # only when a full HIOS ID in this same attachment bundle gives one state.
    issuer_states: dict[str, set[str]] = {}
    for record in links.to_dict(orient="records"):
        if "hios" not in str(record.get("plan_id_type", "")).lower():
            continue
        match = re.match(r"^(\d{5})([A-Z]{2})", str(record.get("plan_id") or ""), re.I)
        if match:
            issuer_states.setdefault(match.group(1), set()).add(match.group(2).upper())
    for idx, record in links.iterrows():
        plan_id = str(record["plan_id"])
        if (
            not record["state"]
            and "hios" in str(record["plan_id_type"]).lower()
            and re.fullmatch(r"\d{5}", plan_id)
        ):
            states = issuer_states.get(plan_id, set())
            if len(states) == 1:
                links.at[idx, "state"] = next(iter(states))
                links.at[idx, "state_source"] = "HIOS_issuer_state_crosswalk_in_supplied_indexes"
    # Use official brand market context only when direct index and HIOS evidence
    # do not identify the state (for example, QualChoice group plans with EINs).
    brand_state = config.get("market_context", {}).get("brand_state", {})
    contextual_state_count = 0
    for idx, record in links.iterrows():
        if not record["state"] and record["brand"] in brand_state:
            links.at[idx, "state"] = str(brand_state[record["brand"]]).upper()
            links.at[idx, "state_source"] = "publisher_brand_market_context"
            contextual_state_count += 1
    out = dirs["processed"]
    links.to_csv(out / "plan_file_links.csv", index=False)

    files_df = (
        links.groupby(["file_id", "rate_file_url"], as_index=False)
        .agg(
            source_indexes=("source_index_file", lambda x: "|".join(sorted(set(x)))),
            brands=("brand", lambda x: "|".join(sorted(set(x)))),
            published_dates=("published_date", lambda x: "|".join(sorted(set(v for v in x if v)))),
            plan_file_links=("link_id", "count"),
            unique_plan_ids=("plan_id", lambda x: len(set(v for v in x if v))),
        )
        .sort_values(["brands", "rate_file_url"])
    )
    files_df.to_csv(out / "unique_rate_files.csv", index=False)

    project_root = Path(config.get("_project_root", Path.cwd())).resolve()
    output_paths = []
    for output_path in (out / "plan_file_links.csv", out / "unique_rate_files.csv"):
        try:
            output_paths.append(output_path.resolve().relative_to(project_root).as_posix())
        except ValueError:
            output_paths.append(output_path.name)
    manifest = {
        "stage": "C1",
        "status": "complete_with_errors" if failed else "complete",
        "index_files_found": len(files),
        "index_files_parsed": len(files) - len(failed),
        "parse_failures": failed,
        "reporting_structures": int(links["structure_index"].nunique() + sum(0 for _ in [])),
        "raw_plan_file_links": raw_link_count,
        "duplicate_plan_file_rows_removed": raw_link_count - len(links),
        "plan_file_links": int(len(links)),
        "unique_rate_file_urls": int(links["file_id"].nunique()),
        "brands": dict(Counter(links["brand"])),
        "states": dict(Counter(links["state"].replace("", "UNKNOWN"))),
        "state_source_counts": dict(Counter(links["state_source"])),
        "market_types": dict(Counter(links["market_type"].replace("", "UNKNOWN"))),
        "brand_market_context_rows": contextual_state_count,
        "market_context_evidence": config.get("market_context", {}).get("evidence", {}),
        "outputs": output_paths,
    }
    # Count reporting structures by file without retaining whole index documents.
    manifest["reporting_structures"] = 0
    for filepath in files:
        try:
                manifest["reporting_structures"] += sum(1 for _ in iter_json_items(filepath, "reporting_structure.item"))
        except Exception:
            pass
    write_json(out / "c1_manifest.json", manifest)
    logger.info(
        "C1 complete: %d indexes, %d plan-file links, %d unique URLs",
        len(files) - len(failed), len(links), links["file_id"].nunique(),
    )
    return links
