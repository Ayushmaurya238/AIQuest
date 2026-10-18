# Final Project Report — Tracks 1 and 2

**Project:** Centene Provider Rate Curation  
**Scope:** California commercial group PPO; Type 1 and Type 2 NPIs  
**Run evidence date:** 2026-10-18

## Executive summary

The combined project adds Track 2 scalability and reproducibility evidence to the existing Track 1 curation pipeline. It streams large JSON/CSV inputs, uses SQLite for provider-reference and rate-selection work, runs distinct selected files in a bounded worker pool, and writes a scorecard and hash manifest.

The resulting candidate-linked workbook is complete as a generated artifact and is internally validated. Data acceptance remains partial for two important reasons: the selected Health Net MRF is shared across PPO and non-PPO plan links, and there is no current payer directory that verifies exact NPI/TIN/practice-address relationships. No Track 2 answer key was provided. The timed video outline is included. Per your outline-only request, the recording is omitted and the problem statement's video item remains unfulfilled; a slide deck is not required by the attached statement.

## What the pipeline does

1. **Discover and classify:** parses all 11 supplied Centene indexes, preserves plan-to-file evidence, deduplicates links, and classifies California group PPO plan links.
2. **Select and grade:** selects PPO-linked rate files and calculates documented completeness, validity, duplication, provider-linkage, active-rate, and outlier metrics.
3. **Extract rates:** iterates CMS Transparency in Coverage JSON entries with ijson; stages provider references in SQLite; expands negotiated prices into auditable rate observations.
4. **Link providers:** matches NPI/TIN pairs against NPPES primary and practice-location addresses, excludes mailing addresses and PO boxes, applies provenance/confidence rules, and keeps payer verification separate from candidate matching.
5. **Build deliverables:** selects representative rates using documented tie-breaking, preserves conflicting options in an audit, maps specialty using the supplied crosswalk then NUCC, and retains unresolved rows.
6. **Validate and reproduce:** checks workbook sheets, unique provider-rate keys, and agreement with C6 manifest counts; records hashes for available source indexes, source bodies, code, and generated outputs.

For Track 2, C4 now supports a configurable file-level worker pool capped at four workers. Each worker has a private SQLite map and temporary CSV; a deterministic ordered merge prevents output order from depending on task completion. A two-file synthetic self-test observed two concurrent workers and confirmed byte-identical output against sequential processing. The production scope has one selected physical file, so it uses one worker; no production speedup is claimed.

## Results

| Measure | Result |
|---|---:|
| Centene indexes parsed | 11 |
| Plan-to-file links | 3,274 |
| Unique rate-file URLs | 33 |
| Explicit California group PPO plan links | 452 |
| Selected physical rate files | 1 |
| Extracted rate observations | 1,511,715 |
| Selected-file quality score | 89.74 (B) |
| Workbook provider-rate rows | 36,796 |
| Duplicate provider-rate keys | 0 |
| Rate selection conflict flags | 9,292 |
| NPI/TIN pairs | 3,775 |
| Pairs with NPPES address candidates | 3,649 (96.66%) |
| Payer-verified NPI/TIN/address pairs | 0 (0%) |
| Unresolved numeric-rate observations | 1,023 |
| Specialty-unmapped provider-rate rows | 599 |

## Track 1 criteria

- **Logic:** decisions retain index, plan, rate-file, provider-reference, taxonomy, and address provenance. Non-unique rates are resolved with a disclosed rule and all conflicting options remain in the audit.
- **Metrics:** file-quality metrics and denominators are documented; unresolved, conflict, and mapping counts are visible.
- **Structure:** output is a stable one-row-per-NPI/TIN/code/address candidate workbook plus a separate unresolved worksheet and CSV. The workbook has no duplicate keys.

## Track 2 criteria

### Scalability

The production parser streams the selected JSON body instead of materializing the full file. Provider references are indexed in SQLite; the pipeline can process distinct rate files concurrently with a configurable bound and merge deterministically. The synthetic test verifies the parallel path. Because only one production rate file is selected, actual production concurrency is one.

The CMS format explicitly models negotiated rate provider references as links to provider-group objects, with NPI and TIN fields in the provider group; the parser follows that structure. See the official [CMS in-network schema guide](https://github.com/CMSgov/price-transparency-guide/blob/master/schemas/in-network-rates/README.md) and [CMS public price-transparency data overview](https://www.cms.gov/initiatives/healthplan-price-transparency/overview/public-data).

### Coverage and linkage

The report distinguishes candidate coverage from verified payer linkage. Candidate coverage is 96.66% across NPI/TIN pairs, but payer-verified coverage is zero. The 1,023 unmatched rate observations are retained rather than assigned speculative locations. The 452 PPO-linked plan links do not prove that every rate in the shared physical file belongs to a PPO plan.

The CMS Doctors and Clinicians national file was reviewed as supplemental NPI-level evidence. It does not provide Centene payer TIN, commercial plan participation, or per-address verification dates, so it is not treated as a payer link.

### Correctness and reproducibility

The combined validation checks the workbook row count against the C6 manifest, verifies the required worksheets, checks that provider-rate keys are unique, and reconciles unresolved worksheet rows with the C6 manifest. It also hashes available indexes, mapping assets, the selected rate-file body, pipeline source code, and generated output files. The full-size NPPES extracts are not in the compact archive and must be placed at the configured paths for a fresh full run.

There is no answer key in the supplied project materials. Therefore the answer-key sample-rate comparison is explicitly marked unavailable; internal consistency checks are not presented as a substitute.

## Inputs and provenance

- Centene indexes supplied by the user: Ambetter, Health Net, QualChoice variants, and Fidelis.
- NPPES: October 2026 monthly full replacement plus Practice Location Reference File used for addresses. The weekly delta and deactivation report were reviewed but not appended to the monthly snapshot.
- Specialty mapping: supplied rate-benchmarking workbook, followed by official NUCC taxonomy v26.1 where the supplied mapping has no taxonomy match.
- Supplemental CMS Doctors and Clinicians file: used only for a separately documented NPI-level review.
- Selected MRF source: URL and local source-body hash are stored in the run evidence. CMS notes that the MRF files can be very large and may require technology to download and read; see the [CMS overview](https://www.cms.gov/initiatives/healthplan-price-transparency/overview/public-data).

## Known gaps and what would close them

1. **Plan-level attribution:** obtain plan-specific in-network files or a payer-provided rate-to-plan/network mapping.
2. **Verified addresses:** obtain an authorized current payer directory containing exact NPI, TIN, practice address, plan/network, active/effective dates, and a recent verification date.
3. **Unresolved rates:** provide exact current address evidence for the 126 NPI/TIN pairs behind 1,023 numeric rate observations.
4. **Specialty:** supply a validated specialty source for the 599 unmapped rows, or keep them explicitly unmapped.
5. **Answer-key comparison:** provide the official sample rate rows and expected values.
6. **Video:** the requested timed outline is included. The recording is omitted per your outline-only request, so the problem-statement video item remains unfulfilled in this handoff. The attached statement does not require a slide deck.

This package improves measurable readiness across both tracks, but no project can guarantee first place; the remaining gaps depend on evidence that was not included in the supplied data.
