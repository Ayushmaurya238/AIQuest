"""C5: connect MRF NPI/TIN pairs to practice-address evidence without fan-out."""

from __future__ import annotations

import csv
import logging
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.common import (
    clean_npi,
    clean_tin,
    ensure_dirs,
    is_po_box,
    normalize_address,
    paths,
    project_path,
    read_csv,
    stable_key,
    write_json,
)

LOG = logging.getLogger(__name__)
ADDRESS_COLUMNS = [
    "npi", "tin", "provider_name", "entity_type", "taxonomy_code", "spec_code",
    "specialty", "specialty_source", "address1", "address2", "city", "state", "zip", "normalized_address",
    "address_key", "address_sources", "source_count", "address_confidence",
    "address_link_status", "tin_address_link_verified", "source_updated_at",
]
ALIASES = {
    "npi": ("NPI", "npi"),
    "entity_type": ("Entity Type Code", "entity_type"),
    "provider_name": ("Provider Organization Name (Legal Business Name)", "provider_name"),
    "first_name": ("Provider First Name", "first_name"),
    "last_name": ("Provider Last Name (Legal Name)", "last_name"),
    "taxonomy_code": ("Healthcare Provider Taxonomy Code_1", "taxonomy_code", "primary_taxonomy"),
    "primary_switch": ("Healthcare Provider Primary Taxonomy Switch_1", "primary_switch"),
    "address1": ("Provider First Line Business Practice Location Address", "address1", "line1"),
    "address2": ("Provider Second Line Business Practice Location Address", "address2", "line2"),
    "city": ("Provider Business Practice Location Address City Name", "city"),
    "state": ("Provider Business Practice Location Address State Name", "state"),
    "zip": ("Provider Business Practice Location Address Postal Code", "zip", "postal_code"),
    # Optional alternate locations can be mapped explicitly in configuration.
    "address1_secondary": ("secondary_address1", "secondary_line1"),
    "address2_secondary": ("secondary_address2", "secondary_line2"),
    "city_secondary": ("secondary_city",),
    "state_secondary": ("secondary_state",),
    "zip_secondary": ("secondary_zip",),
    "tin": ("TIN", "tin"),
    "source": ("source", "source_name"),
    "source_type": ("source_type", "address_source"),
    "directory_status": ("directory_status", "provider_status", "participation_status"),
    "effective_start": ("effective_start", "effective_date", "start_date"),
    "effective_end": ("effective_end", "termination_date", "end_date"),
    "updated_at": ("updated_at", "source_updated_at", "last_verified_at", "location_verified_at"),
}


def _parse_source_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        text = text[:10]
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    for date_format in ("%m/%d/%Y", "%m-%d-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10], date_format).date()
        except ValueError:
            continue
    return None


def _is_current_payer_directory_record(
    record: dict[str, str], as_of: date, max_age_days: int
) -> bool:
    """Require an exact payer row with current participation and a recent verification date."""
    status = re.sub(r"[^a-z]", "", str(record.get("directory_status") or "").lower())
    inactive_statuses = {"inactive", "terminated", "cancelled", "canceled", "notparticipating", "outofnetwork"}
    if status in inactive_statuses:
        return False
    start = _parse_source_date(record.get("effective_start"))
    end = _parse_source_date(record.get("effective_end"))
    if start and start > as_of:
        return False
    if end and end < as_of:
        return False
    active_statuses = {"active", "current", "in", "innetwork", "participating", "par", "eligible"}
    has_active_status = status in active_statuses
    has_current_window = (start is not None or end is not None) and (start is None or start <= as_of) and (end is None or end >= as_of)
    if not (has_active_status or has_current_window):
        return False
    verified_at = _parse_source_date(record.get("updated_at"))
    if verified_at is None:
        return False
    age_days = (as_of - verified_at).days
    return 0 <= age_days <= max_age_days


def _choose_columns(actual: list[str], override: dict[str, str] | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    for canonical, candidates in ALIASES.items():
        requested = (override or {}).get(canonical)
        if requested and requested in actual:
            result[canonical] = requested
            continue
        match = next((candidate for candidate in candidates if candidate in actual), None)
        if match:
            result[canonical] = match
    return result


def _load_taxonomy_mapping(workbook_path: Path) -> dict[str, tuple[str, str]]:
    if not workbook_path.is_file():
        return {}
    try:
        import openpyxl
        book = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
        sheet = book["Taxonomy-Spec Xwalk"]
        rows = sheet.iter_rows(values_only=True)
        headers = [str(value or "").strip().upper() for value in next(rows)]
        tax_idx = headers.index("TAXONOMY CODE")
        code_idx = headers.index("SPEC_CD")
        descr_idx = headers.index("SPEC_DESCR")
        mapping: dict[str, tuple[str, str]] = {}
        for row in rows:
            if row[tax_idx]:
                mapping[str(row[tax_idx]).strip().upper()] = (
                    str(row[code_idx] or "").strip(),
                    str(row[descr_idx] or "").strip(),
                )
        book.close()
        return mapping
    except Exception:
        return {}


def _load_nucc_descriptions(csv_path: Path) -> dict[str, str]:
    """Load the official NUCC display label by taxonomy code."""
    if not csv_path.is_file():
        return {}
    mapping: dict[str, str] = {}
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                code = str(row.get("Code") or "").strip().upper()
                label = str(row.get("Display Name") or "").strip()
                if code and label:
                    mapping[code] = label
    except Exception as exc:
        LOG.warning("Could not read NUCC taxonomy CSV %s: %s", csv_path, exc)
    return mapping


def _address_record(
    row: dict[str, Any],
    source_name: str,
    source_type: str,
    updated_at: str,
) -> dict[str, str] | None:
    npi = clean_npi(row.get("npi"))
    if not npi:
        return None
    address1 = str(row.get("address1") or "").strip()
    address2 = str(row.get("address2") or "").strip()
    city = str(row.get("city") or "").strip()
    state = str(row.get("state") or "").strip().upper()
    postal = re.sub(r"\D", "", str(row.get("zip") or ""))[:5]
    full_address = " ".join(part for part in (address1, address2) if part)
    normalized = normalize_address(f"{full_address} {city} {state} {postal}")
    if not address1 or not city or not state or not postal or is_po_box(full_address):
        return None
    if state != "CA":
        return None
    return {
        "npi": npi,
        "tin": clean_tin(row.get("tin")),
    "provider_name": str(
        row.get("provider_name")
        or " ".join(part for part in (str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip()) if part)
    ).strip(),
        "entity_type": str(row.get("entity_type") or "").strip(),
        "taxonomy_code": str(row.get("taxonomy_code") or "").strip().upper(),
        "address1": address1,
        "address2": address2,
        "city": city,
        "state": state,
        "zip": postal,
        "normalized_address": normalized,
        "address_key": stable_key((normalized,)),
        "source": source_name,
        "source_type": source_type.lower().strip(),
        "directory_status": str(row.get("directory_status") or "").strip(),
        "effective_start": str(row.get("effective_start") or "").strip(),
        "effective_end": str(row.get("effective_end") or "").strip(),
        "updated_at": updated_at or str(row.get("updated_at") or ""),
    }


def _read_source(
    filepath: Path,
    source_kind: str,
    chunksize: int,
    npi_filter: set[str],
    overrides: dict[str, str] | None = None,
    additional_address_maps: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    if not filepath.is_file():
        return []
    header = list(pd.read_csv(filepath, dtype=str, keep_default_na=False, nrows=0).columns)
    mapping = _choose_columns(header, overrides)
    if "npi" not in mapping:
        raise ValueError(f"{source_kind} input has no NPI column: {filepath}")
    taxonomy_columns = [
        column for column in header
        if re.fullmatch(r"Healthcare Provider Taxonomy Code_\d+", column)
    ]
    switch_columns = [
        column for column in header
        if re.fullmatch(r"Healthcare Provider Primary Taxonomy Switch_\d+", column)
    ]
    usecols = set(mapping.values()) | set(taxonomy_columns) | set(switch_columns)
    for address_map in additional_address_maps or []:
        usecols.update(address_map.values())
    records: list[dict[str, str]] = []
    for chunk in pd.read_csv(
        filepath,
        dtype=str,
        keep_default_na=False,
        usecols=lambda column: column in usecols,
        chunksize=chunksize,
    ):
        if taxonomy_columns:
            resolved_taxonomy = pd.Series("", index=chunk.index, dtype=str)
            for taxonomy_column in taxonomy_columns:
                suffix = taxonomy_column.rsplit("_", 1)[-1]
                switch_column = f"Healthcare Provider Primary Taxonomy Switch_{suffix}"
                if switch_column in chunk.columns:
                    primary_mask = (
                        chunk[switch_column].astype(str).str.strip().str.upper().eq("Y")
                        & resolved_taxonomy.eq("")
                    )
                    resolved_taxonomy.loc[primary_mask] = chunk.loc[primary_mask, taxonomy_column].astype(str)
            fallback = chunk[taxonomy_columns[0]].astype(str)
            chunk["__resolved_taxonomy"] = resolved_taxonomy.where(resolved_taxonomy.ne(""), fallback)
        chunk = chunk.rename(columns={actual: canonical for canonical, actual in mapping.items()})
        for canonical in ALIASES:
            if canonical not in chunk.columns:
                chunk[canonical] = ""
        if "__resolved_taxonomy" in chunk.columns:
            chunk["taxonomy_code"] = chunk.pop("__resolved_taxonomy").astype(str).str.strip().str.upper()
        chunk["npi"] = chunk["npi"].map(clean_npi)
        entity_type = chunk["entity_type"].astype(str).str.strip()
        if entity_type.ne("").any():
            chunk = chunk[entity_type.isin({"1", "2"})]
        chunk = chunk[chunk["npi"].isin(npi_filter)]
        for row in chunk.to_dict(orient="records"):
            source_name = str(row.get("source") or source_kind)
            source_type = str(row.get("source_type") or source_kind)
            address_rows = [row]
            canonical_secondary = dict(row)
            for field in ("address1", "address2", "city", "state", "zip"):
                canonical_secondary[field] = row.get(f"{field}_secondary", "")
            if any(str(canonical_secondary.get(field) or "").strip() for field in ("address1", "city", "state", "zip")):
                address_rows.append(canonical_secondary)
            for address_map in additional_address_maps or []:
                alternate = dict(row)
                for canonical, actual in address_map.items():
                    if actual in row and canonical in {"address1", "address2", "city", "state", "zip"}:
                        alternate[canonical] = row[actual]
                if any(str(alternate.get(field) or "").strip() for field in ("address1", "city", "state", "zip")):
                    address_rows.append(alternate)
            for address_row in address_rows:
                record = _address_record(address_row, source_name, source_type, str(row.get("updated_at") or ""))
                if record:
                    records.append(record)
    return records


def _sources_for_npi(record: dict[str, str], candidate: dict[str, Any]) -> None:
    candidate["sources"].add(record["source"] or record["source_type"])
    candidate["source_types"].add(record["source_type"])
    if record["updated_at"]:
        candidate["updated"].add(record["updated_at"])
    if not candidate["provider_name"] and record["provider_name"]:
        candidate["provider_name"] = record["provider_name"]
    if not candidate["taxonomy_code"] and record["taxonomy_code"]:
        candidate["taxonomy_code"] = record["taxonomy_code"]
    if not candidate["entity_type"] and record["entity_type"]:
        candidate["entity_type"] = record["entity_type"]


def run(config: dict[str, Any], logger: logging.Logger | None = None) -> pd.DataFrame:
    logger = logger or LOG
    dirs = ensure_dirs(config)
    rate_input = config.get("sources", {}).get("rate_observations_csv")
    rate_path = project_path(config, rate_input) if rate_input else dirs["processed"] / "rate_observations.csv"
    address_path = dirs["processed"] / "provider_address_links.csv"
    settings = config.get("sources", {})
    as_of_value = settings.get("as_of_date")
    as_of = date.fromisoformat(str(as_of_value)) if as_of_value else date.today()
    directory_max_age_days = int(settings.get("provider_directory_max_age_days", 365))
    nppes_path = project_path(config, settings.get("nppes_csv", "data/input/nppes_practice_addresses.csv"))
    nppes_additional_paths = [
        project_path(config, path)
        for path in settings.get("nppes_additional_csvs", [])
    ]
    nppes_paths = [path for path in [nppes_path, *nppes_additional_paths] if path.is_file()]
    directory_path = project_path(config, settings.get("provider_directory_csv", "data/input/provider_directory.csv"))

    pair_set: set[tuple[str, str]] = set()
    npi_set: set[str] = set()
    tin_set: set[str] = set()
    if rate_path.is_file() and rate_path.stat().st_size:
        for chunk in pd.read_csv(rate_path, dtype=str, keep_default_na=False, usecols=["npi", "tin"], chunksize=100000):
            for npi, tin in zip(chunk["npi"], chunk["tin"]):
                normalized_npi, normalized_tin = clean_npi(npi), clean_tin(tin)
                if normalized_npi and normalized_tin:
                    pair_set.add((normalized_npi, normalized_tin))
                    npi_set.add(normalized_npi)
                    tin_set.add(normalized_tin)

    if not pair_set or (not nppes_paths and not directory_path.is_file()):
        pd.DataFrame(columns=ADDRESS_COLUMNS).to_csv(address_path, index=False)
        status = {
            "stage": "C5",
            "status": "pending_inputs",
            "provider_pairs_in_rate_file": len(pair_set),
            "unique_npis_in_rate_file": len(npi_set),
            "unique_tins_in_rate_file": len(tin_set),
            "nppes_input_present": bool(nppes_paths),
            "nppes_input_files_present": [path.name for path in nppes_paths],
            "provider_directory_input_present": directory_path.is_file(),
            "reason": "A local NPPES practice-address extract or provider-directory file is required; none was supplied.",
        }
        write_json(dirs["processed"] / "c5_manifest.json", status)
        logger.warning("C5 pending: no usable rate pairs or address source input")
        return pd.DataFrame(columns=ADDRESS_COLUMNS)

    batch_size = int(config.get("pipeline", {}).get("batch_size", 50000))
    sources: list[dict[str, str]] = []
    nppes_map = settings.get("nppes_column_map", {})
    nppes_additional_maps = settings.get("nppes_secondary_address_maps", [])
    if isinstance(nppes_additional_maps, dict):
        nppes_additional_maps = [nppes_additional_maps]
    for address_source_path in nppes_paths:
        sources.extend(_read_source(
            address_source_path, "NPPES", batch_size, npi_set, nppes_map,
            nppes_additional_maps,
        ))
    if directory_path.is_file():
        sources.extend(_read_source(
            directory_path, "provider_directory", batch_size, npi_set,
            settings.get("provider_directory_column_map", {}),
        ))

    by_npi: dict[str, list[dict[str, str]]] = defaultdict(list)
    metadata_by_npi: dict[str, dict[str, str]] = defaultdict(dict)
    for record in sources:
        by_npi[record["npi"]].append(record)
        for field in ("provider_name", "entity_type", "taxonomy_code"):
            if record.get(field) and not metadata_by_npi[record["npi"]].get(field):
                metadata_by_npi[record["npi"]][field] = record[field]
    taxonomy = _load_taxonomy_mapping(paths(config)["mapping"])
    nucc_path_value = settings.get("nucc_taxonomy_csv")
    nucc_descriptions = _load_nucc_descriptions(project_path(config, nucc_path_value)) if nucc_path_value else {}
    results: list[dict[str, Any]] = []
    for npi, tin in sorted(pair_set):
        candidates: dict[str, dict[str, Any]] = {}
        for record in by_npi.get(npi, []):
            if record["tin"] and record["tin"] != tin:
                continue
            key = record["address_key"]
            candidate = candidates.setdefault(
                key,
                {
                    **{field: record[field] for field in ("address1", "address2", "city", "state", "zip", "normalized_address")},
                    "sources": set(),
                    "source_types": set(),
                    "updated": set(),
                    "provider_name": "",
                    "entity_type": "",
                    "taxonomy_code": "",
                    "has_exact_pair_source": False,
                },
            )
            _sources_for_npi(record, candidate)
            if (
                record["tin"] == tin
                and record["source_type"] == "payer_directory"
                and _is_current_payer_directory_record(record, as_of, directory_max_age_days)
            ):
                candidate["has_exact_pair_source"] = True
        for candidate in candidates.values():
            source_types = candidate["source_types"]
            independent = source_types - {"nppes"}
            if candidate["has_exact_pair_source"]:
                confidence, status, verified = 0.95, "verified_payer_pair", True
            elif len(source_types) >= 2:
                confidence, status, verified = 0.75, "corroborated_npi_address", False
            elif independent:
                confidence, status, verified = 0.60, "single_non_nppes_source", False
            else:
                confidence, status, verified = 0.30, "nppes_only_unverified", False
            metadata = metadata_by_npi.get(npi, {})
            taxonomy_code = candidate["taxonomy_code"] or metadata.get("taxonomy_code", "")
            spec_code, specialty = taxonomy.get(taxonomy_code.upper(), ("", ""))
            specialty_source = "supplemented_taxonomy_crosswalk" if specialty else ""
            if not specialty and taxonomy_code.upper() in nucc_descriptions:
                specialty = nucc_descriptions[taxonomy_code.upper()]
                specialty_source = "NUCC taxonomy v26.1 (2026-07-01)"
            results.append(
                {
                    "npi": npi,
                    "tin": tin,
                    "provider_name": candidate["provider_name"] or metadata.get("provider_name", ""),
                    "entity_type": candidate["entity_type"] or metadata.get("entity_type", ""),
                    "taxonomy_code": taxonomy_code,
                    "spec_code": spec_code,
                    "specialty": specialty,
                    "specialty_source": specialty_source,
                    "address1": candidate["address1"],
                    "address2": candidate["address2"],
                    "city": candidate["city"],
                    "state": candidate["state"],
                    "zip": candidate["zip"],
                    "normalized_address": candidate["normalized_address"],
                    "address_key": stable_key((npi, tin, candidate["normalized_address"])),
                    "address_sources": "|".join(sorted(candidate["sources"])),
                    "source_count": len(candidate["sources"]),
                    "address_confidence": confidence,
                    "address_link_status": status,
                    "tin_address_link_verified": verified,
                    "source_updated_at": "|".join(sorted(candidate["updated"])),
                }
            )
    result_df = pd.DataFrame(results, columns=ADDRESS_COLUMNS)
    result_df.to_csv(address_path, index=False)
    verified = int(result_df["tin_address_link_verified"].astype(str).str.lower().eq("true").sum()) if not result_df.empty else 0
    pairs_with_candidates = int(result_df[["npi", "tin"]].drop_duplicates().shape[0]) if not result_df.empty else 0
    project_root = Path(config.get("_project_root", Path.cwd())).resolve()
    input_display_paths = []
    for path in nppes_paths:
        try:
            input_display_paths.append(path.resolve().relative_to(project_root).as_posix())
        except ValueError:
            input_display_paths.append(path.name)
    write_json(
        dirs["processed"] / "c5_manifest.json",
        {
            "stage": "C5",
            "status": "complete",
            "rate_npi_tin_pairs": len(pair_set),
            "unique_npis_in_rate_file": len(npi_set),
            "unique_tins_in_rate_file": len(tin_set),
            "pairs_with_address_candidates": pairs_with_candidates,
            "pairs_with_address_candidates_share": round(pairs_with_candidates / len(pair_set), 6) if pair_set else None,
            "address_rows": int(len(result_df)),
            "verified_pair_address_rows": verified,
            "verified_pair_address_share": round(verified / len(result_df), 6) if len(result_df) else None,
            "provider_directory_input_present": directory_path.is_file(),
            "address_verification_status": (
                "verified_pair_links_present" if verified == len(result_df) and verified > 0
                else "partially_verified" if verified > 0
                else "no_exact_pair_links" if directory_path.is_file()
                else "payer_directory_missing"
            ),
            "address_source_records": len(sources),
            "nppes_input_files": input_display_paths,
            "rule": "NPPES-only addresses remain candidates. A payer-directory link is verified only when NPI, TIN, and address match exactly, the directory record is active or effective on the run date, and its verification/update date is within the configured freshness window.",
            "payer_directory_max_age_days": directory_max_age_days,
        },
    )
    logger.info("C5 complete: %d address candidates, %d pair-verified", len(result_df), verified)
    return result_df
