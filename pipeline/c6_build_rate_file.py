"""C6: join rates to resolved addresses and export one auditable row per requested key."""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

from pipeline.common import ensure_dirs, numeric_rate, paths, project_path, read_csv, write_json

LOG = logging.getLogger(__name__)
OUTPUT_COLUMNS = [
    "NPI", "TIN", "Address", "Address 2", "City", "State", "ZIP",
    "Billing Code", "Specialty", "Rate", "Rate Type", "Rate Options",
    "Rate Selection Method", "Rate Conflict", "Address Confidence",
    "Address Link Status", "TIN Address Link Verified", "Taxonomy Code", "Specialty Source",
    "Billing Code Specialty Match", "Billing Class", "Service Code",
    "Billing Code Type", "Rate File ID", "Address Sources",
    "Address Source Updated At", "Network Attribution Status",
]
UNRESOLVED_OUTPUT_COLUMNS = [
    "NPI", "TIN", "Billing Code", "Rate", "Rate Type", "Billing Class",
    "Service Code", "Modifier", "Expiration Date", "Billing Code Type",
    "Rate File ID", "Unresolved Reason",
]
UNRESOLVED_SOURCE_FIELDS = {
    "NPI": "npi",
    "TIN": "tin",
    "Billing Code": "billing_code",
    "Rate": "rate",
    "Rate Type": "negotiated_type",
    "Billing Class": "billing_class",
    "Service Code": "service_code",
    "Modifier": "modifier",
    "Expiration Date": "expiration_date",
    "Billing Code Type": "billing_code_type",
    "Rate File ID": "file_id",
    "Unresolved Reason": "unresolved_reason",
}


def _rate_priority(rate_type: str) -> int:
    text = (rate_type or "").strip().lower()
    if text == "negotiated":
        return 1
    if text == "derived":
        return 2
    if "fee schedule" in text:
        return 3
    if "per diem" in text or "per day" in text:
        return 4
    return 8


def _expiry_sort(value: str, asof: date) -> tuple[int, str]:
    text = (value or "").strip()[:10]
    if not text:
        return (1, "")
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return (1, "")
    return (2 if parsed >= asof else 0, text)


def _specialty_procedure_pairs(workbook_path: Path) -> set[tuple[str, str]]:
    if not workbook_path.is_file():
        return set()
    try:
        import openpyxl
        book = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
        sheet = book["Spec-Proc Weights"]
        rows = sheet.iter_rows(values_only=True)
        headers = [str(value or "").strip().upper() for value in next(rows)]
        spec_idx, proc_idx = headers.index("SPEC_CD"), headers.index("PROC_CD")
        result = {
            (str(row[spec_idx]).strip(), str(row[proc_idx]).strip().upper())
            for row in rows
            if row[spec_idx] not in (None, "") and row[proc_idx] not in (None, "")
        }
        book.close()
        return result
    except Exception:
        return set()


def _style_header(sheet: Any) -> None:
    from openpyxl.styles import Font, PatternFill
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="17365D")


def run(config: dict[str, Any], logger: logging.Logger | None = None) -> pd.DataFrame:
    logger = logger or LOG
    dirs = ensure_dirs(config)
    rate_input = config.get("sources", {}).get("rate_observations_csv")
    rates_path = project_path(config, rate_input) if rate_input else dirs["processed"] / "rate_observations.csv"
    address_input = config.get("sources", {}).get("provider_address_links_csv")
    addresses_path = project_path(config, address_input) if address_input else dirs["processed"] / "provider_address_links.csv"
    audit_path = dirs["processed"] / "rate_selection_audit.csv"
    unresolved_path = dirs["processed"] / "unresolved_rate_observations.csv"
    final_path = dirs["output"] / "provider_rates.xlsx"
    workbook_path = paths(config)["mapping"]
    project_root = Path(config.get("_project_root", Path.cwd())).resolve()
    try:
        workbook_display_path = workbook_path.resolve().relative_to(project_root).as_posix()
    except ValueError:
        workbook_display_path = workbook_path.name
    c5_manifest = {}
    c5_manifest_path = dirs["processed"] / "c5_manifest.json"
    if c5_manifest_path.is_file():
        try:
            c5_manifest = json.loads(c5_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            c5_manifest = {}
    specialty_proc = _specialty_procedure_pairs(workbook_path)
    asof_value = config.get("sources", {}).get("as_of_date")
    asof = date.fromisoformat(str(asof_value)) if asof_value else date.today()

    if not rates_path.is_file() or rates_path.stat().st_size == 0 or not addresses_path.is_file():
        write_json(
            dirs["processed"] / "c6_manifest.json",
            {"stage": "C6", "status": "pending_upstream", "reason": "Extracted rates and address-link inputs are required."},
        )
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    addresses = read_csv(addresses_path)
    if addresses.empty:
        write_json(
            dirs["processed"] / "c6_manifest.json",
            {"stage": "C6", "status": "pending_address_sources", "reason": "No NPI/TIN address candidates were produced."},
        )
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    address_map: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in addresses.to_dict(orient="records"):
        address_map.setdefault((row["npi"], row["tin"]), []).append(row)

    db = sqlite3.connect(dirs["processed"] / "rate_selection.sqlite")
    db.execute("DROP TABLE IF EXISTS options")
    db.execute(
        """CREATE TABLE options(
        npi TEXT,tin TEXT,address_key TEXT,billing_code TEXT,record_hash TEXT UNIQUE,
        amount REAL,rate_text TEXT,rate_type TEXT,type_rank INTEGER,expiration_date TEXT,
        billing_class TEXT,service_code TEXT,modifier TEXT,billing_code_type TEXT,
        file_id TEXT,specialty TEXT,specialty_source TEXT,spec_code TEXT,address TEXT,address2 TEXT,city TEXT,
        state TEXT,zip TEXT,address_confidence TEXT,address_status TEXT,tin_verified TEXT,
        taxonomy_code TEXT,code_specialty_match TEXT,address_sources TEXT,
        address_updated_at TEXT,network_status TEXT)"""
    )
    db.execute("CREATE INDEX options_group_idx ON options(npi,tin,address_key,billing_code,type_rank,expiration_date)")
    unresolved_rows = 0
    unresolved_reason_counts: Counter[str] = Counter()
    unresolved_pair_keys: set[tuple[str, str]] = set()
    unresolved_npis: set[str] = set()
    inserted_rows = 0
    with rates_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        unresolved_fields = list(reader.fieldnames or [])
        if "unresolved_reason" not in unresolved_fields:
            unresolved_fields.append("unresolved_reason")
        with unresolved_path.open("w", newline="", encoding="utf-8") as unresolved_handle:
            unresolved_writer = csv.DictWriter(unresolved_handle, fieldnames=unresolved_fields)
            unresolved_writer.writeheader()
            for record in reader:
                npi, tin = record.get("npi", ""), record.get("tin", "")
                amount = numeric_rate(record.get("rate"))
                matches = address_map.get((npi, tin), []) if npi and tin else []
                reason = ""
                if not npi or not tin:
                    reason = "missing_npi_or_tin"
                elif amount is None:
                    reason = "missing_or_non_numeric_rate"
                elif not matches:
                    reason = "no_npi_tin_address_candidate"
                if reason:
                    unresolved_rows += 1
                    unresolved_reason_counts[reason] += 1
                    if npi:
                        unresolved_npis.add(npi)
                    if npi and tin:
                        unresolved_pair_keys.add((npi, tin))
                    unresolved_writer.writerow({**record, "unresolved_reason": reason})
                    continue
                rate_type = record.get("negotiated_type", "")
                rank = _rate_priority(rate_type)
                for address in matches:
                    spec_code = str(address.get("spec_code", ""))
                    code = str(record.get("billing_code", "")).strip().upper()
                    match = "true" if (spec_code, code) in specialty_proc else "false"
                    raw_key = (
                        npi, tin, address.get("address_key"), code, amount, rate_type,
                        record.get("expiration_date"), record.get("billing_class"),
                        record.get("service_code"), record.get("modifier"), record.get("file_id"),
                    )
                    import hashlib
                    record_hash = hashlib.sha256("|".join(str(v or "") for v in raw_key).encode("utf-8")).hexdigest()
                    db.execute(
                        """INSERT OR IGNORE INTO options VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            npi, tin, address.get("address_key", ""), code, record_hash,
                            amount, record.get("rate_text", ""), rate_type, rank,
                            record.get("expiration_date", ""), record.get("billing_class", ""),
                            record.get("service_code", ""), record.get("modifier", ""),
                            record.get("billing_code_type", ""), record.get("file_id", ""),
                            address.get("specialty", ""), address.get("specialty_source", ""), spec_code, address.get("address1", ""),
                            address.get("address2", ""), address.get("city", ""), address.get("state", ""),
                            address.get("zip", ""), address.get("address_confidence", ""),
                            address.get("address_link_status", ""), address.get("tin_address_link_verified", ""),
                            address.get("taxonomy_code", ""), match,
                            address.get("address_sources", ""), address.get("source_updated_at", ""),
                            record.get("network_attribution_status", ""),
                        ),
                    )
                    inserted_rows += db.execute("SELECT changes()").fetchone()[0]
    db.commit()

    audit_fields = [
        "NPI", "TIN", "Address Key", "Billing Code", "Selected Rate", "Selected Rate Type",
        "Rate Options", "Selection Method", "Rate Conflict", "Selected Expiration",
        "Address Sources", "Address Source Updated At", "Address Link Status",
        "TIN Address Link Verified", "Network Attribution Status",
    ]
    group_query = db.execute(
        "SELECT DISTINCT npi,tin,address_key,billing_code FROM options ORDER BY npi,tin,address_key,billing_code"
    )
    group_count = int(
        db.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM options GROUP BY npi,tin,address_key,billing_code)"
        ).fetchone()[0]
    )
    unmapped_specialty_rows = int(
        db.execute(
            """SELECT COUNT(*) FROM (
            SELECT npi,tin,address_key,billing_code FROM options
            GROUP BY npi,tin,address_key,billing_code
            HAVING MAX(CASE WHEN COALESCE(TRIM(specialty),'') <> '' THEN 1 ELSE 0 END) = 0
            )"""
        ).fetchone()[0]
    )
    workbook = None
    rate_sheet = None
    if group_count:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

        workbook = Workbook(write_only=True)
        summary_sheet = workbook.create_sheet("Run Summary")
        summary_sheet.freeze_panes = "A2"
        summary_sheet.column_dimensions["A"].width = 56
        summary_sheet.column_dimensions["B"].width = 100
        summary_header = []
        for title in ("Metric", "Value"):
            cell = WriteOnlyCell(summary_sheet, value=title)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="17365D")
            summary_header.append(cell)
        summary_sheet.append(summary_header)
        summary_sheet.append(["Provider rate rows", group_count])
        summary_sheet.append(["Unresolved or unaddressed numeric rate rows", unresolved_rows])
        summary_sheet.append(["Source observations joined to an address", inserted_rows])
        summary_sheet.append(["Address candidate rows", len(addresses)])
        summary_sheet.append(["TIN/address verified rows", int(addresses["tin_address_link_verified"].astype(str).str.lower().eq("true").sum()) if "tin_address_link_verified" in addresses else 0])
        summary_sheet.append(["Source workbook", workbook_display_path])
        summary_sheet.append(["Selection rule", "Direct negotiated rates first; then latest nonexpired expiration. Tied distinct values use median and are flagged; all options remain in the audit CSV."])
        summary_sheet.append(["Unique NPIs in selected rates", c5_manifest.get("unique_npis_in_rate_file", "")])
        summary_sheet.append(["Unique TINs in selected rates", c5_manifest.get("unique_tins_in_rate_file", "")])
        summary_sheet.append(["Rate NPI/TIN pairs with address candidate", c5_manifest.get("pairs_with_address_candidates", "")])
        summary_sheet.append(["Rate NPI/TIN pair candidate coverage", c5_manifest.get("pairs_with_address_candidates_share", "")])
        summary_sheet.append(["Rate NPI/TIN pairs", c5_manifest.get("rate_npi_tin_pairs", "")])
        summary_sheet.append(["Verified TIN/address share", c5_manifest.get("verified_pair_address_share", "")])
        summary_sheet.append(["Rate rows with unmapped specialty", unmapped_specialty_rows])
        summary_sheet.append(["Unresolved observation detail", unresolved_path.relative_to(project_root).as_posix()])
        summary_sheet.auto_filter.ref = "A1:B16"

        column_widths = [
            14, 14, 34, 20, 20, 8, 12, 16, 38, 14, 16, 14, 44, 14,
            20, 26, 24, 18, 40, 28, 16, 14, 20, 24, 20, 24, 32,
        ]

        def new_rate_sheet(sheet_number: int) -> Any:
            sheet = workbook.create_sheet(f"Provider Rates {sheet_number}")
            sheet.freeze_panes = "C2"
            for column_number, width in enumerate(column_widths, start=1):
                sheet.column_dimensions[get_column_letter(column_number)].width = width
            header_cells = []
            for title in OUTPUT_COLUMNS:
                cell = WriteOnlyCell(sheet, value=title)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="17365D")
                header_cells.append(cell)
            sheet.append(header_cells)
            return sheet

        rate_sheet_no = 1
        rate_sheet = new_rate_sheet(rate_sheet_no)
        unresolved_sheet = workbook.create_sheet("Unresolved Rates")
        unresolved_sheet.freeze_panes = "A2"
        unresolved_widths = [14, 14, 20, 14, 18, 18, 14, 14, 16, 20, 24, 36]
        for column_number, width in enumerate(unresolved_widths, start=1):
            unresolved_sheet.column_dimensions[get_column_letter(column_number)].width = width
        unresolved_header = []
        for title in UNRESOLVED_OUTPUT_COLUMNS:
            cell = WriteOnlyCell(unresolved_sheet, value=title)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="17365D")
            unresolved_header.append(cell)
        unresolved_sheet.append(unresolved_header)
        unresolved_sheet_rows = 0
        with unresolved_path.open("r", newline="", encoding="utf-8") as unresolved_handle:
            unresolved_reader = csv.DictReader(unresolved_handle)
            for record in unresolved_reader:
                unresolved_sheet.append([
                    record.get(UNRESOLVED_SOURCE_FIELDS[column], "")
                    for column in UNRESOLVED_OUTPUT_COLUMNS
                ])
                unresolved_sheet_rows += 1
        unresolved_sheet.auto_filter.ref = f"A1:L{unresolved_sheet_rows + 1}"
    sheet_rows = 0
    rate_selection_conflicts = 0
    mapped_output_rows = 0
    row_limit = 1_048_000
    with audit_path.open("w", newline="", encoding="utf-8") as audit_handle:
        audit_writer = csv.DictWriter(audit_handle, fieldnames=audit_fields)
        audit_writer.writeheader()
        for npi, tin, address_key, code in group_query:
            options = db.execute(
                """SELECT amount,rate_text,rate_type,type_rank,expiration_date,billing_class,
                service_code,modifier,billing_code_type,file_id,specialty,specialty_source,spec_code,address,
                address2,city,state,zip,address_confidence,address_status,tin_verified,
                taxonomy_code,code_specialty_match,record_hash,address_sources,address_updated_at,network_status
                FROM options WHERE npi=? AND tin=? AND address_key=? AND billing_code=?""",
                (npi, tin, address_key, code),
            ).fetchall()
            best_rank = min(row[3] for row in options)
            same_type = [row for row in options if row[3] == best_rank]
            expiry_ranks = [_expiry_sort(row[4], asof) for row in same_type]
            best_expiry_class = max(item[0] for item in expiry_ranks)
            same_expiry_class = [row for row, key in zip(same_type, expiry_ranks) if key[0] == best_expiry_class]
            if best_expiry_class == 2:
                latest_expiry = max(_expiry_sort(row[4], asof)[1] for row in same_expiry_class)
                chosen = [row for row in same_expiry_class if _expiry_sort(row[4], asof)[1] == latest_expiry]
            elif best_expiry_class == 0:
                latest_expiry = max(_expiry_sort(row[4], asof)[1] for row in same_expiry_class)
                chosen = [row for row in same_expiry_class if _expiry_sort(row[4], asof)[1] == latest_expiry]
            else:
                chosen = same_expiry_class
                latest_expiry = ""
            amounts = [float(row[0]) for row in chosen]
            selected_rate = median(amounts)
            types = sorted(set(row[2] for row in chosen))
            rate_type = types[0] if len(types) == 1 else "|".join(types)
            context_signatures = {(row[5], row[6], row[7]) for row in chosen}
            conflict = len(set(amounts)) > 1 or len(context_signatures) > 1
            if len(chosen) == 1:
                method = "highest_priority_type_then_latest_expiration"
            elif conflict:
                method = "median_of_tied_values_or_contexts; see audit"
            else:
                method = "collapsed_exact_duplicate_options"
            first = chosen[0]
            rate_options = json.dumps(
                [
                {
                    "rate": row[0], "rate_type": row[2], "expiration_date": row[4],
                    "billing_class": row[5], "service_code": row[6], "modifier": row[7],
                    "file_id": row[9], "selected": row in chosen,
                }
                for row in options
                ],
                ensure_ascii=False,
            )
            row_out = {
                "NPI": npi, "TIN": tin, "Address": first[13], "Address 2": first[14],
                "City": first[15], "State": first[16], "ZIP": first[17], "Billing Code": code,
                "Specialty": first[10] or "Unmapped", "Rate": selected_rate, "Rate Type": rate_type,
                "Rate Options": len(options), "Rate Selection Method": method,
                "Rate Conflict": conflict, "Address Confidence": first[18],
                "Address Link Status": first[19], "TIN Address Link Verified": first[20],
                "Taxonomy Code": first[21], "Specialty Source": first[11] or "no_taxonomy_mapping",
                "Billing Code Specialty Match": first[22],
                "Billing Class": first[5], "Service Code": first[6],
                "Billing Code Type": first[8], "Rate File ID": first[9],
                "Address Sources": first[24], "Address Source Updated At": first[25],
                "Network Attribution Status": first[26],
            }
            audit_writer.writerow(
                {
                    "NPI": npi, "TIN": tin, "Address Key": address_key,
                    "Billing Code": code, "Selected Rate": selected_rate,
                    "Selected Rate Type": rate_type, "Rate Options": rate_options,
                    "Selection Method": method, "Rate Conflict": conflict,
                    "Selected Expiration": latest_expiry,
                    "Address Sources": first[24], "Address Source Updated At": first[25],
                    "Address Link Status": first[19], "TIN Address Link Verified": first[20],
                    "Network Attribution Status": first[26],
                }
            )
            if workbook is not None and rate_sheet is not None:
                if sheet_rows >= row_limit:
                    rate_sheet.auto_filter.ref = (
                        f"A1:{get_column_letter(len(OUTPUT_COLUMNS))}{sheet_rows + 1}"
                    )
                    rate_sheet_no += 1
                    rate_sheet = new_rate_sheet(rate_sheet_no)
                    sheet_rows = 0
                rate_sheet.append([row_out.get(column, "") for column in OUTPUT_COLUMNS])
                sheet_rows += 1
            rate_selection_conflicts += int(conflict)
            mapped_output_rows += int(row_out["Billing Code Specialty Match"] == "true")
    db.close()

    if workbook is not None:
        if rate_sheet is not None:
            from openpyxl.utils import get_column_letter

            rate_sheet.auto_filter.ref = (
                f"A1:{get_column_letter(len(OUTPUT_COLUMNS))}{sheet_rows + 1}"
            )
        workbook.save(final_path)
    write_json(
        dirs["processed"] / "c6_manifest.json",
        {
            "stage": "C6",
            "status": "complete" if group_count else "no_joined_rates",
            "provider_rate_rows": group_count,
            "unresolved_or_unaddressed_numeric_rate_rows": unresolved_rows,
            "unresolved_reason_counts": dict(unresolved_reason_counts),
            "unresolved_npi_tin_pairs": len(unresolved_pair_keys),
            "unresolved_unique_npis": len(unresolved_npis),
            "unresolved_observations_csv": unresolved_path.resolve().relative_to(project_root).as_posix(),
            "rate_selection_conflicts": rate_selection_conflicts,
            "billing_code_specialty_matches": mapped_output_rows,
            "unmapped_specialty_rate_rows": unmapped_specialty_rows,
            "unique_npis_in_rate_file": c5_manifest.get("unique_npis_in_rate_file"),
            "unique_tins_in_rate_file": c5_manifest.get("unique_tins_in_rate_file"),
            "rate_npi_tin_pairs": c5_manifest.get("rate_npi_tin_pairs"),
            "pairs_with_address_candidates": c5_manifest.get("pairs_with_address_candidates"),
            "pairs_with_address_candidates_share": c5_manifest.get("pairs_with_address_candidates_share"),
            "verified_pair_address_share": c5_manifest.get("verified_pair_address_share"),
            "excel_output": final_path.resolve().relative_to(project_root).as_posix() if group_count else None,
            "selection_rule": "Prefer direct negotiated dollar rates, then later valid expiration. When tied distinct amounts remain, use their median as the representative value, flag the conflict, and preserve all raw options in the audit CSV.",
        },
    )
    logger.info("C6 complete: %d provider-rate rows; %d unresolved observations", group_count, unresolved_rows)
    return pd.DataFrame([{"metric": "provider_rate_rows", "value": group_count}], columns=["metric", "value"])
