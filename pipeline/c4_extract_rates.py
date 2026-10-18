"""C4: stream selected in-network MRFs into auditable provider-rate rows."""

from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

import pandas as pd
try:
    import ijson
except ImportError:
    ijson = None
try:
    import requests
except ImportError:
    requests = None

from pipeline.common import clean_npi, clean_tin, ensure_dirs, iter_json_items, read_csv, stable_key

LOG = logging.getLogger(__name__)
_DOWNLOAD_CACHE_LOCK = threading.Lock()
RATE_COLUMNS = [
    "source_record_id", "file_id", "rate_file_url", "network_attribution_status",
    "billing_code", "billing_code_type", "billing_code_type_version",
    "billing_code_description", "negotiation_arrangement", "negotiated_type",
    "rate", "rate_text", "expiration_date", "billing_class", "service_code",
    "modifier", "provider_reference_id", "npi", "tin", "provider_link_status",
]


def _reader(path: Path):
    with path.open("rb") as probe:
        magic = probe.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else path.open("rb")


def _npi_values(group: dict[str, Any]) -> list[str]:
    values = group.get("npi") or []
    if not isinstance(values, list):
        values = [values]
    result = []
    for value in values:
        raw = value.get("value", "") if isinstance(value, dict) else value
        normalized = clean_npi(raw)
        if normalized:
            result.append(normalized)
    return sorted(set(result))


def _tin_values(group: dict[str, Any]) -> list[str]:
    value = group.get("tin") or group.get("tax_id")
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        raw = item.get("value", "") if isinstance(item, dict) else item
        normalized = clean_tin(raw)
        if normalized:
            result.append(normalized)
    return sorted(set(result))


def _group_id(group: dict[str, Any]) -> str:
    value = group.get("provider_group_id", group.get("id", ""))
    return str(value).strip()


def _insert_group(db: sqlite3.Connection, group_id: str, group: dict[str, Any], source: str) -> None:
    if not group_id:
        return
    npis = _npi_values(group)
    tins = _tin_values(group)
    if not npis:
        return
    for npi in npis:
        for tin in (tins or [""]):
            db.execute(
                "INSERT OR IGNORE INTO provider_map(ref_id,npi,tin,source) VALUES (?,?,?,?)",
                (group_id, npi, tin, source),
            )


def _parse_refs(path: Path, db: sqlite3.Connection) -> list[tuple[str, str]]:
    if ijson is None:
        raise RuntimeError("Install ijson before parsing an MRF rate-file body.")
    external: list[tuple[str, str]] = []
    with _reader(path) as handle:
        for group in ijson.items(handle, "provider_groups.item"):
            if isinstance(group, dict):
                _insert_group(db, _group_id(group), group, "inline_provider_groups")
    with _reader(path) as handle:
        for ref in ijson.items(handle, "provider_references.item"):
            if not isinstance(ref, dict):
                continue
            ref_id = _group_id(ref)
            groups = ref.get("provider_groups") or []
            if isinstance(groups, dict):
                groups = [groups]
            for group in groups:
                if isinstance(group, dict):
                    _insert_group(db, _group_id(group) or ref_id, group, "inline_provider_reference")
            if ref_id and (_npi_values(ref) or _tin_values(ref)):
                _insert_group(db, ref_id, ref, "direct_provider_reference")
            location = str(ref.get("location") or "").strip()
            if location:
                external.append((ref_id, location))
    db.commit()
    return external


def _download_to(url: str, path: Path, timeout: int = 90) -> Path:
    if requests is None:
        raise RuntimeError("Install requests before enabling online rate-file downloads.")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Serialize same-process cache writes and atomically publish completed files.
    # This also prevents two file workers from treating a partial download as a cache hit.
    with _DOWNLOAD_CACHE_LOCK:
        if path.is_file() and path.stat().st_size:
            return path
        fd, temporary_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".part", dir=path.parent)
        os.close(fd)
        temporary_path = Path(temporary_name)
        try:
            with requests.get(url, stream=True, timeout=timeout) as response:
                response.raise_for_status()
                with temporary_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
    return path


def _load_external_groups(
    db: sqlite3.Connection,
    external: Iterable[tuple[str, str]],
    cache_dir: Path,
    enabled: bool,
    logger: logging.Logger,
) -> None:
    if not enabled:
        return
    seen: set[str] = set()
    for fallback_ref_id, url in external:
        if url in seen:
            continue
        seen.add(url)
        cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        suffix = ".json.gz" if url.lower().endswith(".gz") else ".json"
        cached = cache_dir / f"provider_groups_{cache_key}{suffix}"
        try:
            _download_to(url, cached)
            inserted = 0
            with _reader(cached) as handle:
                for group in ijson.items(handle, "provider_groups.item"):
                    if isinstance(group, dict):
                        group_id = _group_id(group) or fallback_ref_id
                        before = db.total_changes
                        _insert_group(db, group_id, group, "external_provider_groups")
                        inserted += db.total_changes - before
            db.commit()
            logger.info("Loaded %d provider link rows from external reference %s", inserted, url)
        except Exception:
            logger.exception("Could not resolve external provider group file %s", url)


def _find_rate_file(file_id: str, rate_dir: Path) -> Path | None:
    for suffix in (".json", ".json.gz", ".gz"):
        candidate = rate_dir / f"{file_id}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _download_source(url: str, file_id: str, rate_dir: Path) -> Path:
    basename = Path(urlsplit(url).path).name.lower()
    suffix = ".json.gz" if basename.endswith(".gz") else ".json"
    return _download_to(url, rate_dir / f"{file_id}{suffix}")


def _reference_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        value = [value]
    result: list[str] = []
    for item in value:
        if isinstance(item, dict):
            ref_id = item.get("provider_group_id", item.get("id", ""))
        else:
            ref_id = item
        if ref_id is not None and str(ref_id).strip():
            result.append(str(ref_id).strip())
    return result


def _list_text(value: Any) -> str:
    if isinstance(value, list):
        return "|".join(str(item) for item in value if item not in (None, ""))
    return "" if value is None else str(value)


def _rate_rows(
    path: Path,
    file_id: str,
    url: str,
    network_status: str,
    db: sqlite3.Connection,
    writer: csv.DictWriter,
) -> int:
    if ijson is None:
        raise RuntimeError("Install ijson before parsing an MRF rate-file body.")
    extracted = 0
    with _reader(path) as handle:
        for code_no, item in enumerate(ijson.items(handle, "in_network.item")):
            if not isinstance(item, dict):
                continue
            billing_code = str(item.get("billing_code") or "")
            code_type = str(item.get("billing_code_type") or "")
            for rate_no, rate_item in enumerate(item.get("negotiated_rates") or []):
                if not isinstance(rate_item, dict):
                    continue
                ref_ids = _reference_ids(rate_item.get("provider_references") or [])
                prices = rate_item.get("negotiated_prices") or []
                if not isinstance(prices, list):
                    prices = [prices]
                provider_rows: list[tuple[str, str, str, str]] = []
                for ref_id in ref_ids:
                    found = db.execute(
                        "SELECT npi,tin,source FROM provider_map WHERE ref_id=? ORDER BY npi,tin",
                        (ref_id,),
                    ).fetchall()
                    if found:
                        provider_rows.extend((ref_id, npi, tin, source) for npi, tin, source in found)
                    else:
                        provider_rows.append((ref_id, "", "", "unresolved_provider_reference"))
                if not provider_rows:
                    provider_rows.append(("", "", "", "missing_provider_reference"))
                for price_no, price in enumerate(prices):
                    if not isinstance(price, dict):
                        price = {}
                    raw_rate = price.get("negotiated_rate")
                    rate_text = "" if raw_rate is None else str(raw_rate)
                    numeric = None
                    try:
                        cleaned = rate_text.replace(",", "").replace("$", "").strip()
                        if cleaned and "%" not in cleaned:
                            numeric = float(cleaned)
                    except ValueError:
                        pass
                    for ref_id, npi, tin, source in provider_rows:
                        source_id = stable_key((file_id, code_no, rate_no, price_no, ref_id, npi, tin))
                        writer.writerow(
                            {
                                "source_record_id": source_id,
                                "file_id": file_id,
                                "rate_file_url": url,
                                "network_attribution_status": network_status,
                                "billing_code": billing_code,
                                "billing_code_type": code_type,
                                "billing_code_type_version": str(item.get("billing_code_type_version") or ""),
                                "billing_code_description": str(item.get("description") or ""),
                                "negotiation_arrangement": str(item.get("negotiation_arrangement") or ""),
                                "negotiated_type": str(price.get("negotiated_type") or ""),
                                "rate": "" if numeric is None else numeric,
                                "rate_text": rate_text,
                                "expiration_date": str(price.get("expiration_date") or ""),
                                "billing_class": str(price.get("billing_class") or ""),
                                "service_code": _list_text(price.get("service_code")),
                                "modifier": _list_text(price.get("billing_code_modifier")),
                                "provider_reference_id": ref_id,
                                "npi": npi,
                                "tin": tin,
                                "provider_link_status": source,
                            }
                        )
                        extracted += 1
    return extracted


def _process_selected_file(
    index: int,
    record: dict[str, Any],
    dirs: dict[str, Path],
    project_root: Path,
    download_enabled: bool,
    fetch_external: bool,
    logger: logging.Logger,
    work_dir: Path,
) -> tuple[int, dict[str, Any], Path | None]:
    file_id = str(record["file_id"])
    url = str(record["rate_file_url"])
    status = {
        "file_id": file_id,
        "rate_file_url": url,
        "selected_for_extraction": True,
        "mixed_plan_file": str(record.get("mixed_plan_file", "")).lower() in {"true", "1"},
        "network_attribution_status": str(record.get("rate_network_attribution") or ""),
        "local_path": "",
        "status": "",
        "rate_rows_extracted": 0,
        "provider_references_resolved": 0,
        "error": "",
    }
    part_path: Path | None = None
    try:
        source = _find_rate_file(file_id, dirs["rates"])
        if source is None and download_enabled:
            source = _download_source(url, file_id, dirs["rates"])
        if source is None:
            status["status"] = "missing_rate_file"
            status["error"] = "Rate-file body was not supplied locally; automatic downloads are disabled."
            return index, status, None
        try:
            status["local_path"] = source.resolve().relative_to(project_root).as_posix()
        except ValueError:
            status["local_path"] = source.name

        task_dir = work_dir / f"{index:06d}"
        task_dir.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(task_dir / "provider_refs.sqlite")
        try:
            db.execute(
                "CREATE TABLE provider_map(ref_id TEXT,npi TEXT,tin TEXT,source TEXT,"
                "PRIMARY KEY(ref_id,npi,tin,source))"
            )
            external = _parse_refs(source, db)
            _load_external_groups(db, external, dirs["rates"], fetch_external, logger)
            status["provider_references_resolved"] = int(
                db.execute("SELECT COUNT(DISTINCT ref_id) FROM provider_map").fetchone()[0]
            )
            part_path = task_dir / "rate_observations.csv"
            with part_path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=RATE_COLUMNS)
                writer.writeheader()
                count = _rate_rows(
                    source,
                    file_id,
                    url,
                    status["network_attribution_status"],
                    db,
                    writer,
                )
        finally:
            db.close()
        status["rate_rows_extracted"] = count
        status["status"] = "complete"
        logger.info("C4 extracted %d rate rows from %s", count, file_id)
    except Exception as exc:
        status["status"] = "error"
        status["error"] = str(exc)
        logger.exception("C4 failed for %s", file_id)
        part_path = None
    return index, status, part_path


def run(config: dict[str, Any], logger: logging.Logger | None = None) -> pd.DataFrame:
    logger = logger or LOG
    dirs = ensure_dirs(config)
    selected_path = dirs["processed"] / "selected_ppo_files.csv"
    selected = read_csv(selected_path)
    selected = selected[selected["selected_for_extraction"].str.lower().isin({"true", "1"})]
    records = selected.to_dict("records")
    observations_path = dirs["processed"] / "rate_observations.csv"
    status_path = dirs["processed"] / "c4_file_status.csv"
    settings = config.get("sources", {})
    project_root = Path(config.get("_project_root", Path.cwd())).resolve()
    download_enabled = bool(settings.get("download_rate_files", False))
    fetch_external = bool(settings.get("fetch_external_provider_groups", False))
    requested_workers = int(config.get("pipeline", {}).get("max_parallel_rate_files", 1))
    max_workers = max(1, min(requested_workers, 4, len(records) or 1))
    results: list[tuple[int, dict[str, Any], Path | None]] = []

    # Each worker owns its SQLite map and temporary CSV. The ordered merge keeps
    # byte-for-byte row ordering stable regardless of worker completion order.
    with tempfile.TemporaryDirectory(prefix="c4_parts_", dir=dirs["processed"]) as temp_root:
        work_dir = Path(temp_root)
        if max_workers == 1:
            results = [
                _process_selected_file(
                    index, record, dirs, project_root, download_enabled, fetch_external,
                    logger, work_dir,
                )
                for index, record in enumerate(records)
            ]
        else:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="c4-rate-file") as pool:
                futures = [
                    pool.submit(
                        _process_selected_file,
                        index, record, dirs, project_root, download_enabled,
                        fetch_external, logger, work_dir,
                    )
                    for index, record in enumerate(records)
                ]
                results = [future.result() for future in futures]
        results.sort(key=lambda item: item[0])
        with observations_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(RATE_COLUMNS)
            for _, _, part_path in results:
                if part_path is None:
                    continue
                with part_path.open("r", newline="", encoding="utf-8") as part:
                    reader = csv.reader(part)
                    next(reader, None)
                    writer.writerows(reader)

    chunk_file_status = [status for _, status, _ in results]
    for status in chunk_file_status:
        status["worker_pool_size"] = max_workers
        status["execution_mode"] = "bounded_file_parallel" if max_workers > 1 else "single_file_stream"
    extracted_total = sum(int(status.get("rate_rows_extracted", 0) or 0) for status in chunk_file_status)
    pd.DataFrame(chunk_file_status).to_csv(status_path, index=False)
    logger.info(
        "C4 complete: %d rows from %d selected files using %d worker(s)",
        extracted_total,
        len(records),
        max_workers,
    )
    return pd.DataFrame(chunk_file_status)
