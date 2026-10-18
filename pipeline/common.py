"""Shared paths, identifiers, and CSV helpers for the Track 1 pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
try:
    import yaml
except ImportError:  # Allow the bundled offline run to parse this simple YAML config.
    yaml = None

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    with config_path.open("r", encoding="utf-8") as handle:
        raw = handle.read()
    if yaml is not None:
        config = yaml.safe_load(raw) or {}
    else:
        config = _simple_yaml(raw)
    if not isinstance(config, dict):
        raise ValueError("The configuration file must contain a YAML mapping.")
    config["_project_root"] = config_path.resolve().parent
    config["_config_path"] = config_path.resolve()
    return config


def _yaml_scalar(value: str) -> Any:
    text = value.strip()
    if text.lower() in {"null", "none", "~"}:
        return None
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _simple_yaml(raw: str) -> dict[str, Any]:
    """Parse the nested mappings/lists used by this project's small config."""
    lines = [(len(line) - len(line.lstrip()), line.strip()) for line in raw.splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    for index, (indent, text) in enumerate(lines):
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if text.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError("Fallback YAML parser expected a list item.")
            parent.append(_yaml_scalar(text[2:]))
            continue
        if ":" not in text or not isinstance(parent, dict):
            raise ValueError(f"Unsupported YAML config line: {text}")
        key, raw_value = text.split(":", 1)
        raw_value = raw_value.strip()
        if raw_value:
            parent[key.strip()] = _yaml_scalar(raw_value)
            continue
        next_line = lines[index + 1] if index + 1 < len(lines) else None
        child: Any = [] if next_line and next_line[0] > indent and next_line[1].startswith("- ") else {}
        parent[key.strip()] = child
        stack.append((indent, child))
    return root


def project_path(config: dict[str, Any], path: str | Path) -> Path:
    value = Path(path)
    if value.is_absolute():
        return value
    return Path(config.get("_project_root", ROOT)) / value


def paths(config: dict[str, Any]) -> dict[str, Path]:
    dirs = config.get("data_dirs", {})
    return {
        "indexes": project_path(config, dirs.get("indexes", "data/input/indexes")),
        "processed": project_path(config, dirs.get("processed", "data/processed")),
        "rates": project_path(config, dirs.get("rates", "data/raw/rates")),
        "output": project_path(config, dirs.get("output", "data/output")),
        "mapping": project_path(
            config,
            config.get("sources", {}).get(
                "mapping_workbook", "data/input/rate_benchmarking_weights.xlsx"
            ),
        ),
    }


def ensure_dirs(config: dict[str, Any]) -> dict[str, Path]:
    resolved = paths(config)
    for key in ("processed", "rates", "output"):
        resolved[key].mkdir(parents=True, exist_ok=True)
    return resolved


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Required pipeline output not found: {path}")
    return pd.read_csv(path, dtype=str, keep_default_na=False, **kwargs)


def stable_file_id(url: str) -> str:
    parts = urlsplit((url or "").strip())
    normalized = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def stable_key(parts: Iterable[Any]) -> str:
    payload = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def clean_identifier(value: Any, width: int) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return ""
    digits = re.sub(r"\D", "", text)
    return digits.zfill(width) if digits else ""


def clean_npi(value: Any) -> str:
    result = clean_identifier(value, 10)
    return result if len(result) == 10 else ""


def clean_tin(value: Any) -> str:
    result = clean_identifier(value, 9)
    return result if len(result) == 9 else ""


def numeric_rate(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text or "%" in text or text.lower() in {"nan", "none", "null", "n/a"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def normalize_address(value: Any) -> str:
    text = str(value or "").upper()
    text = re.sub(r"[.,#]", " ", text)
    text = re.sub(r"\b(STREET)\b", "ST", text)
    text = re.sub(r"\b(AVENUE)\b", "AVE", text)
    text = re.sub(r"\b(BOULEVARD)\b", "BLVD", text)
    text = re.sub(r"\b(ROAD)\b", "RD", text)
    text = re.sub(r"\b(DRIVE)\b", "DR", text)
    text = re.sub(r"\b(SUITE)\b", "STE", text)
    text = re.sub(r"\b(APARTMENT)\b", "APT", text)
    return " ".join(text.split())


def is_po_box(address: str) -> bool:
    return bool(re.search(r"\bP\s*\.?\s*O\s*\.?\s*BOX\b|\bPOST OFFICE BOX\b", address or "", re.I))


def iter_json_items(path: Path, prefix: str) -> Iterator[Any]:
    """Yield JSON items with ijson; use standard JSON only for small inputs."""
    try:
        import ijson
    except ImportError:
        if path.stat().st_size > 20 * 1024 * 1024:
            raise RuntimeError("Install the ijson dependency before parsing large JSON files.")
        with path.open("r", encoding="utf-8") as handle:
            obj = json.load(handle)
        if prefix == "reporting_entity_name":
            value = obj.get("reporting_entity_name")
            if value is not None:
                yield value
        elif prefix == "reporting_structure.item":
            yield from obj.get("reporting_structure", [])
        elif prefix == "provider_groups.item":
            yield from obj.get("provider_groups", [])
        elif prefix == "provider_references.item":
            yield from obj.get("provider_references", [])
        elif prefix == "in_network.item":
            yield from obj.get("in_network", [])
        return
    with path.open("rb") as handle:
        yield from ijson.items(handle, prefix)
