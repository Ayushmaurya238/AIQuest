# Centene Provider Rate Curation — Tracks 1 and 2

This project builds an auditable California commercial-provider-rate pipeline from Centene transparency indexes and the selected Health Net in-network rate file. It adds bounded file-level parallel processing, a synthetic concurrency self-test, a coverage scorecard, a validation report, and SHA-256 reproducibility evidence.

The final handoff is intentionally partial where the supplied data cannot prove a relationship. The index links one physical Health Net file to PPO plans and to non-PPO plans; it does not identify a PPO plan for each rate. NPPES and CMS addresses are candidates, not payer-verified TIN/address links. The timed video outline is included. Per your outline-only request, no recording is included; the problem statement's recording item is therefore waived and remains unfulfilled in this handoff. The problem statement does not require a slide deck.

## Final results

- 11 Centene indexes parsed; 3,274 plan/file links and 33 unique rate-file URLs.
- 452 explicit California group PPO plan links identified; one physical rate file selected.
- 1,511,715 rate observations extracted; file-quality score 89.74 (B).
- Final workbook: 36,796 unique provider-rate keys; no duplicate keys.
- NPPES address candidates: 3,649 of 3,775 NPI/TIN pairs (96.66%); payer-verified pairs: 0.
- 1,023 numeric rate observations remain unresolved for address linkage; 599 workbook rows remain specialty-unmapped.
- Bounded file parallelism is implemented. The production scope has one selected file, so it uses one worker. A synthetic two-file self-test verified two overlapping workers and byte-identical output versus sequential processing.
- No answer key was supplied, so the Track 2 sample-rate comparison could not be performed.

## Main deliverables

- data/output_nppes_final/provider_rates.xlsx — provider-rate workbook with resolved candidate rows and a separate unresolved tab.
- data/output_nppes_final/summary.json — combined Track 1 and Track 2 run status.
- data/processed_nppes_final/track2_scorecard.csv — measurable coverage and scalability metrics.
- data/processed_nppes_final/track2_validation.json — validation checks and known gaps.
- data/processed_nppes_final/reproducibility_manifest.json — input, code, and output hashes.
- data/processed_nppes_final/parallel_processing_selftest.json — two-worker deterministic self-test evidence.
- docs/both_tracks_final_report.md — final results, criterion mapping, and remaining evidence gaps.
- docs/requirement_compliance_audit.md — requirement-by-requirement status.
- docs/logic_file.md and docs/quality_metrics.md — pipeline logic and quality metric definitions.
- docs/video_outline.md — timed outline for a video under five minutes. No recording is included per your request; the problem statement does not require a slide deck.

## Run a clean pipeline

Use Python 3.10 or newer and install requirements.txt. Place the October 2026 NPPES monthly full-replacement primary file and its Practice Location Reference File at the relative paths in config_both_tracks.yaml under data/input/nppes/. Do not append a weekly delta to the full replacement. The compact handoff does not contain the 11.8 GB primary NPPES file, its practice-location file, the 0.7 GB MRF source body, or the 602 MB extracted rate-observation intermediate. The pipeline can download the MRF and rebuild the intermediate if network access is available.

From the project folder, run:

    python -m pipeline.main --config config_both_tracks.yaml

The final T2 stage creates the scorecard, validation report, and hash manifest. To validate just the already-generated outputs, run:

    python -m pipeline.track2_audit --config config_both_tracks.yaml

The synthetic parallel-processing check can be rerun with:

    python -m pipeline.selftest_parallel_c4

## Data and scope limits

The CMS Transparency in Coverage format stores negotiated-rate entries with provider-reference identifiers that resolve to provider groups containing NPIs and TINs. The parser streams the large JSON and stores the reference lookup in SQLite instead of loading the whole file into memory. Bounded workers process different selected files independently, then merge their rows in deterministic source order. See the official [CMS in-network schema](https://github.com/CMSgov/price-transparency-guide/blob/master/schemas/in-network-rates/README.md).

NPPES practice locations and the supplemental CMS Doctors and Clinicians file do not establish the payer TIN-to-address relationship or commercial plan participation. To verify those links, add a current authorized payer directory with exact NPI, TIN, practice address, network/plan, active/effective dates, and a recent verification date. A plan-specific MRF or rate-to-plan mapping is also needed to claim PPO attribution for the selected rates. Rows without those links remain auditable and unresolved rather than being guessed.
