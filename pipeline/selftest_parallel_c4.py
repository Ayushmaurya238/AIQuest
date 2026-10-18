"""Exercise C4's bounded file-parallel mode on a tiny, synthetic MRF fixture."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import threading
import time
import tempfile
from pathlib import Path
from unittest.mock import patch

from pipeline import c4_extract_rates as c4
from pipeline.common import write_json


def run(output_path: Path | None = None) -> dict:
    active = 0
    peak_active = 0
    guard = threading.Lock()
    original = c4._process_selected_file

    project_root = Path(__file__).resolve().parents[1]
    temp_parent = project_root / "data" / "processed_nppes_final"
    temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="c4_parallel_selftest_", dir=temp_parent) as temporary:
        root = Path(temporary)
        rates = root / "data" / "raw" / "rates"
        processed = root / "data" / "processed"
        output = root / "data" / "output"
        rates.mkdir(parents=True)
        processed.mkdir(parents=True)
        output.mkdir(parents=True)

        records = []
        for position, file_id in enumerate(("fixture_a", "fixture_b"), start=1):
            body = {
                "provider_groups": [{
                    "provider_group_id": position,
                    "npi": ["1234567893"],
                    "tin": {"type": "ein", "value": "12-3456789"},
                }],
                "in_network": [{
                    "billing_code": f"9920{position}",
                    "billing_code_type": "CPT",
                    "billing_code_type_version": "2026",
                    "description": "Synthetic fixture service",
                    "negotiation_arrangement": "ffs",
                    "negotiated_rates": [{
                        "provider_references": [position],
                        "negotiated_prices": [{
                            "negotiated_type": "negotiated",
                            "negotiated_rate": 125.0 + position,
                            "billing_class": "professional",
                            "expiration_date": "2027-12-31",
                            "service_code": ["11"],
                        }],
                    }],
                }],
            }
            (rates / f"{file_id}.json").write_text(json.dumps(body), encoding="utf-8")
            records.append({
                "file_id": file_id,
                "rate_file_url": f"https://fixture.invalid/{file_id}.json",
                "selected_for_extraction": "True",
                "mixed_plan_file": "False",
                "rate_network_attribution": "fixture_only",
            })
        with (processed / "selected_ppo_files.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)

        config = {
            "_project_root": root,
            "data_dirs": {
                "indexes": "data/input/indexes",
                "processed": "data/processed",
                "rates": "data/raw/rates",
                "output": "data/output",
            },
            "sources": {"download_rate_files": False, "fetch_external_provider_groups": False},
            "pipeline": {"max_parallel_rate_files": 2},
        }

        def instrumented(*args, **kwargs):
            nonlocal active, peak_active
            with guard:
                active += 1
                peak_active = max(peak_active, active)
            try:
                time.sleep(0.08)
                return original(*args, **kwargs)
            finally:
                with guard:
                    active -= 1

        with patch.object(c4, "_process_selected_file", side_effect=instrumented):
            parallel_status = c4.run(config)
        parallel_bytes = (processed / "rate_observations.csv").read_bytes()
        parallel_hash = hashlib.sha256(parallel_bytes).hexdigest()
        config["pipeline"]["max_parallel_rate_files"] = 1
        sequential_status = c4.run(config)
        sequential_bytes = (processed / "rate_observations.csv").read_bytes()

        parallel_complete = int((parallel_status["status"] == "complete").sum())
        sequential_complete = int((sequential_status["status"] == "complete").sum())
        row_count = max(0, parallel_bytes.count(b"\n") - 1)
        passed = (
            parallel_complete == 2
            and sequential_complete == 2
            and row_count == 2
            and peak_active >= 2
            and parallel_bytes == sequential_bytes
        )
        result = {
            "status": "passed" if passed else "failed",
            "fixture_file_count": 2,
            "rate_rows": row_count,
            "peak_concurrent_file_workers": peak_active,
            "parallel_and_sequential_outputs_identical": parallel_bytes == sequential_bytes,
            "deterministic_output_sha256": parallel_hash,
            "scope": "Synthetic fixture only; does not claim a speedup on the single selected production file.",
        }

    if output_path is None:
        project_root = Path(__file__).resolve().parents[1]
        output_path = project_root / "data" / "processed_nppes_final" / "parallel_processing_selftest.json"
    write_json(output_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate C4 parallel processing and deterministic merge")
    parser.add_argument("--output", help="Optional JSON evidence file path")
    args = parser.parse_args()
    result = run(Path(args.output).resolve() if args.output else None)
    print(json.dumps(result, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
