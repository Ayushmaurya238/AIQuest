"""Command-line runner for the reproducible Track 1 pipeline."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.common import ensure_dirs, load_config, write_json

STAGES = {
    "C1": ("pipeline.c1_parse_index", "Parse plan-to-file index links"),
    "C2": ("pipeline.c2_identify_ppo", "Classify in-scope PPO plan links"),
    "C4": ("pipeline.c4_extract_rates", "Stream selected rate-file bodies"),
    "C3": ("pipeline.c3_grade_files", "Grade extracted file quality"),
    "C5": ("pipeline.c5_map_addresses", "Map NPI/TIN pairs to practice addresses"),
    "C6": ("pipeline.c6_build_rate_file", "Build the provider rate workbook"),
    "T2": ("pipeline.track2_audit", "Validate scalability, coverage, and reproducibility evidence"),
}
DEFAULT_ORDER = ("C1", "C2", "C4", "C3", "C5", "C6", "T2")


def _logging(config: dict[str, Any], verbose: bool) -> logging.Logger:
    root = logging.getLogger()
    root.handlers.clear()
    level = logging.DEBUG if verbose else getattr(
        logging, str(config.get("logging", {}).get("level", "INFO")).upper(), logging.INFO
    )
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root.addHandler(handler)
    return root


def _manifest(root: Path, name: str) -> dict[str, Any]:
    path = root / name
    if not path.is_file():
        return {"status": "not_run"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unreadable"}


def _mapping_summary(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    try:
        import openpyxl
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        taxonomy_sheet = book["Taxonomy-Spec Xwalk"]
        taxonomy_rows = list(taxonomy_sheet.iter_rows(min_row=2, values_only=True))
        procedure_sheet = book["Spec-Proc Weights"]
        procedure_rows = list(procedure_sheet.iter_rows(min_row=2, values_only=True))
        result = {
            "taxonomy_crosswalk_rows": sum(bool(row[3]) for row in taxonomy_rows),
            "unique_taxonomy_codes": len({str(row[3]).strip().upper() for row in taxonomy_rows if row[3]}),
            "weighted_specialty_procedure_rows": sum(bool(row[0] and row[2]) for row in procedure_rows),
            "unique_procedure_codes": len({str(row[2]).strip().upper() for row in procedure_rows if row[2]}),
        }
        book.close()
        return result
    except Exception:
        return {}


def build_summary(config: dict[str, Any]) -> dict[str, Any]:
    dirs = ensure_dirs(config)
    processed = dirs["processed"]
    c1 = _manifest(processed, "c1_manifest.json")
    c2 = _manifest(processed, "c2_manifest.json")
    c5 = _manifest(processed, "c5_manifest.json")
    c6 = _manifest(processed, "c6_manifest.json")
    cms_review = _manifest(processed, "cms_source_audit.json")
    track2_validation = _manifest(processed, "track2_validation.json")
    parallel_selftest = _manifest(processed, "parallel_processing_selftest.json")
    quality_path = processed / "file_quality_metrics.csv"
    quality = pd.read_csv(quality_path, dtype=str, keep_default_na=False) if quality_path.is_file() else pd.DataFrame()
    c4_path = processed / "c4_file_status.csv"
    c4 = pd.read_csv(c4_path, dtype=str, keep_default_na=False) if c4_path.is_file() else pd.DataFrame()
    output = dirs["output"]
    project_root = Path(config.get("_project_root", Path.cwd())).resolve()
    deliverables = config.get("deliverables", {})
    video_relative = deliverables.get("explainer_video", "video/Centene_Track1_Explainer_2026-10-18.mp4")
    video_path = Path(video_relative)
    if not video_path.is_absolute():
        video_path = project_root / video_path
    video_present = video_path.is_file()
    discovery_complete = c1.get("status") == "complete" and c2.get("status") == "complete"
    end_to_end_complete = (
        discovery_complete
        and int((c4.get("status", pd.Series(dtype=str)) == "complete").sum()) > 0
        and c5.get("status") == "complete"
        and c6.get("status") == "complete"
    )
    completion_blockers = []
    if c5.get("address_verification_status") == "payer_directory_missing":
        completion_blockers.append(
            "No payer directory with exact NPI, TIN, practice address, and network participation was supplied; NPPES-only matches remain unverified."
        )
    if c2.get("selected_mixed_plan_files", 0):
        completion_blockers.append(
            "The selected physical MRF URL is shared by PPO and non-PPO plan links, so rate-level PPO attribution is unresolved."
        )
    if not video_present:
        if deliverables.get("video_excluded_by_user_request", False):
            completion_blockers.append("The user requested an outline-only handoff, so no recorded video is included; the problem-statement video deliverable remains unfulfilled by request.")
        else:
            completion_blockers.append("The required five-minute explainer video is not included.")
    if c6.get("unmapped_specialty_rate_rows", 0):
        completion_blockers.append(
            f"{c6.get('unmapped_specialty_rate_rows')} provider-rate rows have no specialty description in the supplied taxonomy sources."
        )
    if c6.get("unresolved_or_unaddressed_numeric_rate_rows", 0):
        unresolved_count = c6.get("unresolved_or_unaddressed_numeric_rate_rows")
        cms_candidate_npis = cms_review.get("unresolved_npis_with_complete_california_address", 0)
        if cms_candidate_npis:
            completion_blockers.append(
                f"{unresolved_count} rate observations have no NPI/TIN address candidate in C5; the separate CMS review found NPI-only California address candidates for {cms_candidate_npis} unresolved NPIs, but no TIN link, so those observations remain unresolved."
            )
        else:
            completion_blockers.append(
                f"{unresolved_count} rate observations have no address candidate in C5 and are retained in the unresolved-observation audit."
            )
    summary = {
        "project": "Centene Price Transparency Data Pipeline",
        "track": "Tracks 1 and 2 — Logic, Metrics, Structure, Scalability, Coverage, Reproducibility",
        "as_of_date": config.get("sources", {}).get("as_of_date"),
        "run_status": "complete_with_requirement_gaps" if end_to_end_complete and completion_blockers else "complete" if end_to_end_complete else "network_discovery_complete_pending_sources" if discovery_complete else "partial",
        "pipeline_execution_status": "complete" if end_to_end_complete else "partial",
        "submission_readiness_status": "partial" if completion_blockers else "complete",
        "explainer_video_status": "complete_synthesized_narration" if video_present else "excluded_by_user_request_outline_only" if deliverables.get("video_excluded_by_user_request", False) else "missing",
        "explainer_video_path": video_path.resolve().relative_to(project_root).as_posix() if video_present else None,
        "explainer_slide_source_status": "not_required_by_problem_statement",
        "explainer_slide_source_path": None,
        "video_handoff_status": "outline_only_by_user_request" if deliverables.get("video_excluded_by_user_request", False) else "recording_included" if video_present else "missing",
        "network_discovery_status": "complete" if discovery_complete else "partial",
        "index_files_parsed": c1.get("index_files_parsed"),
        "reporting_structures": c1.get("reporting_structures"),
        "plan_file_links": c1.get("plan_file_links"),
        "duplicate_plan_file_rows_removed": c1.get("duplicate_plan_file_rows_removed"),
        "unique_rate_file_urls": c1.get("unique_rate_file_urls"),
        "plan_decisions": c2.get("decision_counts", {}),
        "unique_explicit_ppo_plans": c2.get("unique_explicit_ppo_plans"),
        "selected_rate_files": c2.get("selected_files", []),
        "brand_market_context_rows": c1.get("brand_market_context_rows"),
        "state_evidence_counts": c1.get("state_source_counts", {}),
        "market_context_evidence": c1.get("market_context_evidence", {}),
        "mapping_workbook_coverage": _mapping_summary(dirs["mapping"]),
        "ppo_linked_files_selected": c2.get("files_selected_for_extraction"),
        "mixed_plan_files_selected": c2.get("selected_mixed_plan_files"),
        "rate_files_available": int((c4.get("status", pd.Series(dtype=str)) == "complete").sum()) if not c4.empty else 0,
        "rate_rows_extracted": int(pd.to_numeric(c4.get("rate_rows_extracted", pd.Series(dtype=str)), errors="coerce").fillna(0).sum()) if not c4.empty else 0,
        "files_with_quality_scores": int((quality.get("quality_status", pd.Series(dtype=str)) == "graded").sum()) if not quality.empty else 0,
        "quality_metrics_status": "available" if not quality.empty and (quality.get("quality_status", pd.Series(dtype=str)) == "graded").any() else "pending_rate_file_bodies",
        "address_mapping_status": c5.get("status", "not_run"),
        "provider_npi_tin_pairs": c5.get("rate_npi_tin_pairs"),
        "unique_npis_in_selected_rates": c5.get("unique_npis_in_rate_file"),
        "unique_tins_in_selected_rates": c5.get("unique_tins_in_rate_file"),
        "pairs_with_address_candidates": c5.get("pairs_with_address_candidates"),
        "pairs_with_address_candidates_share": c5.get("pairs_with_address_candidates_share"),
        "address_candidate_rows": c5.get("address_rows"),
        "verified_pair_address_rows": c5.get("verified_pair_address_rows"),
        "verified_pair_address_share": c5.get("verified_pair_address_share"),
        "address_verification_status": c5.get("address_verification_status", "not_run"),
        "provider_directory_input_present": c5.get("provider_directory_input_present", False),
        "rate_file_status": c6.get("status", "not_run"),
        "provider_rate_rows": c6.get("provider_rate_rows"),
        "unresolved_or_unaddressed_numeric_rate_rows": c6.get("unresolved_or_unaddressed_numeric_rate_rows"),
        "unresolved_reason_counts": c6.get("unresolved_reason_counts", {}),
        "unresolved_npi_tin_pairs": c6.get("unresolved_npi_tin_pairs"),
        "unresolved_unique_npis": c6.get("unresolved_unique_npis"),
        "rate_selection_conflicts": c6.get("rate_selection_conflicts"),
        "billing_code_specialty_matches": c6.get("billing_code_specialty_matches"),
        "unmapped_specialty_rate_rows": c6.get("unmapped_specialty_rate_rows"),
        "requirement_completion_status": "partial",
        "track1_requirements": {
            "C1_index_parsing": "complete" if c1.get("status") == "complete" else "pending",
            "C2_PPO_identification": "complete_plan_classification_rate_attribution_limited" if c2.get("status") == "complete" and c2.get("selected_mixed_plan_files", 0) else "complete" if c2.get("status") == "complete" else "pending",
            "C3_file_quality_metrics": "complete" if not quality.empty and (quality.get("quality_status", pd.Series(dtype=str)) == "graded").any() else "pending",
            "C4_rate_extraction_and_selection": "complete" if not c4.empty and (c4.get("status", pd.Series(dtype=str)) == "complete").any() else "pending",
            "C5_practice_address_linkage": "partial_payer_TIN_verification_missing" if c5.get("status") == "complete" and c5.get("address_verification_status") == "payer_directory_missing" else "complete" if c5.get("status") == "complete" else "pending",
            "C6_provider_rate_workbook": "partial_address_or_specialty_exceptions" if c6.get("status") == "complete" and (c6.get("unmapped_specialty_rate_rows", 0) or c6.get("unresolved_or_unaddressed_numeric_rate_rows", 0)) else "complete" if c6.get("status") == "complete" else "pending",
            "recorded_video": "complete_synthesized_narration" if video_present else "excluded_by_user_request_outline_only" if deliverables.get("video_excluded_by_user_request", False) else "missing_script_outline_available",
            "optional_C7_cost_effectiveness": "not_implemented_mixed_network_and_incomplete_weight_coverage",
        },
        "track2_validation_status": track2_validation.get("validation_status", "not_run"),
        "track2_requirements": {
            "streaming_large_inputs": "implemented_and_used" if int(pd.to_numeric(c4.get("rate_rows_extracted", pd.Series(dtype=str)), errors="coerce").fillna(0).sum()) else "pending",
            "bounded_parallel_processing": "implemented_self_test_passed" if parallel_selftest.get("status") == "passed" else "implemented_production_pool_bounded_by_selected_files",
            "coverage_and_linkage": "measured_with_unverified_and_unresolved_rows" if c5.get("status") == "complete" else "pending",
            "answer_key_sample_comparison": "not_performed_answer_key_not_supplied",
            "reproducibility_manifest": "available" if (processed / "reproducibility_manifest.json").is_file() else "pending",
        },
        "completion_blockers": completion_blockers,
        "supplemental_cms_source_review": cms_review,
        "missing_inputs": [],
        "outputs": [],
    }
    if not list(dirs["rates"].glob("*.json*")) and not config.get("sources", {}).get("download_rate_files", False):
        summary["missing_inputs"].append("selected MRF rate-file bodies")
    source_cfg = config.get("sources", {})
    nppes_path = Path(source_cfg.get("nppes_csv", "data/input/nppes_practice_addresses.csv"))
    if not nppes_path.is_absolute():
        nppes_path = Path(config.get("_project_root", Path.cwd())) / nppes_path
    if not nppes_path.is_file():
        summary["missing_inputs"].append("NPPES practice-address extract")
    if not c2.get("files_selected_for_extraction"):
        summary["missing_inputs"].append("in-scope PPO-linked rate-file URL")
    for filename in ("plan_file_links.csv", "unique_rate_files.csv", "plan_classification.csv", "selected_ppo_files.csv", "file_quality_metrics.csv", "rate_selection_audit.csv", "provider_address_links.csv", "unresolved_rate_observations.csv", "c5_manifest.json", "c6_manifest.json", "cms_source_audit.json", "track2_scorecard.csv", "track2_validation.json", "reproducibility_manifest.json", "parallel_processing_selftest.json", "provider_rates.xlsx"):
        if filename == "provider_rates.xlsx":
            path = output / filename
        else:
            path = processed / filename
        if path.is_file():
            try:
                summary["outputs"].append(path.resolve().relative_to(project_root).as_posix())
            except ValueError:
                summary["outputs"].append(path.name)
    for path in (video_path, slides_path):
        if path.is_file():
            summary["outputs"].append(path.resolve().relative_to(project_root).as_posix())
    write_json(output / "summary.json", summary)
    pd.DataFrame([{"metric": key, "value": json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value} for key, value in summary.items()]).to_csv(output / "summary.csv", index=False)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Centene provider rate curation pipeline for Tracks 1 and 2")
    parser.add_argument("--config", default="config.yaml", help="YAML config path, relative to this project by default")
    parser.add_argument("--stage", choices=tuple(STAGES), help="Run one stage; prior stage outputs must exist")
    parser.add_argument("--verbose", action="store_true", help="Print detailed pipeline logs")
    args = parser.parse_args()
    config = load_config(args.config)
    logger = _logging(config, args.verbose)
    ensure_dirs(config)
    stages = [args.stage] if args.stage else list(DEFAULT_ORDER)
    for stage_id in stages:
        module_name, label = STAGES[stage_id]
        logger.info("Starting %s: %s", stage_id, label)
        module = importlib.import_module(module_name)
        module.run(config, logger)
    summary = build_summary(config)
    logger.info("Run summary: %s", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
