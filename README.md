# Centene Provider Rate Curation --- Tracks 1 and 2

An auditable data pipeline for identifying Centene's California
commercial-group PPO plan links, grading in-network Price Transparency
Data (PTD) files, extracting negotiated rates, and linking provider
NPI/TIN pairs to practice-address candidates.

> **Status: implementation and internal validation complete, with known
> data-acceptance gaps.** This repository deliberately distinguishes
> extracted data and candidate matches from payer-verified facts. It
> does not claim that every extracted rate is attributable to a specific
> PPO plan or that NPPES-only addresses are payer-verified.

## Table of contents

-   [1. Business context](#1-business-context)
-   [2. Problem scope and
    deliverables](#2-problem-scope-and-deliverables)
-   [3. Results at a glance](#3-results-at-a-glance)
-   [4. Repository layout](#4-repository-layout)
-   [5. Pipeline architecture](#5-pipeline-architecture)
-   [6. Challenge-by-challenge
    implementation](#6-challenge-by-challenge-implementation)
-   [7. File-quality scoring
    methodology](#7-file-quality-scoring-methodology)
-   [8. Provider-rate selection and exception
    handling](#8-provider-rate-selection-and-exception-handling)
-   [9. Address matching and
    confidence](#9-address-matching-and-confidence)
-   [10. Setup](#10-setup)
-   [11. Input data required for a full
    rerun](#11-input-data-required-for-a-full-rerun)
-   [12. Run the pipeline](#12-run-the-pipeline)
-   [13. Validate generated outputs](#13-validate-generated-outputs)
-   [14. Output files and how to interpret
    them](#14-output-files-and-how-to-interpret-them)
-   [15. Track 2: scalability and
    reproducibility](#15-track-2-scalability-and-reproducibility)
-   [16. Known limitations and next
    steps](#16-known-limitations-and-next-steps)
-   [17. Data provenance and responsible
    use](#17-data-provenance-and-responsible-use)
-   [18. Evaluation-criterion mapping](#18-evaluation-criterion-mapping)

## 1. Business context

US health plans publish negotiated in-network rates under the
Transparency in Coverage requirements. These files can support market
analysis, but they are large and inconsistent: multiple plans can point
to the same physical file; provider references need to be resolved;
records can contain multiple rate options; and a rate file generally
does not provide a fully verified relationship between a payer TIN and
the provider's current practice address.

This project treats the task as an auditable data-curation pipeline
rather than a simple download or display exercise. Each major stage
produces structured outputs and records decisions, source lineage,
exceptions, and coverage metrics.

## 2. Problem scope and deliverables

**Target scope**

-   **Payer:** Centene
-   **Market:** California commercial group, including group,
    small-group, and large-group classifications
-   **Network:** PPO
-   **Provider identifiers:** Type 1 and Type 2 NPIs
-   **Core tasks:** network mapping, file-quality grading, rate
    extraction, provider/address linkage, and a provider-rate workbook

The pipeline covers the required C1--C6 workflow. Optional C7
cost-effectiveness ranking is not implemented because the selected
physical rate file is shared across PPO and non-PPO plan links and the
supplied procedure-weight mapping does not cover every selected
provider-rate row. Ranking rates under those conditions could imply
unsupported comparability.

**Submission artifacts**

-   Provider-rate Excel workbook, including an unresolved-rate worksheet
-   Network-mapping and provider/address-linkage logic documentation
-   File-quality metric definitions and per-file measurements
-   Summary and coverage metrics in JSON/CSV
-   Track 2 validation and scalability evidence
-   Reproducibility manifest with hashes for available inputs, code, and
    outputs
-   Runnable Python pipeline and a synthetic parallel-processing
    self-test

A separate demonstration video (maximum five minutes) accompanies the
submission and walks through the pipeline, key outputs, validation
evidence, and the interpretation of unresolved records.

## 3. Results at a glance

Results below describe the run evidence dated **18 October 2026**. They
are not claims of independently adjudicated ground-truth accuracy.

  Measure                                                                     Result
  ---------------------------------------------------------------- -----------------
  Centene index files parsed                                                      11
  Plan-to-file links                                                           3,274
  Exact duplicate plan/file links removed                                          1
  Unique rate-file URLs in the indexes                                            33
  Explicit California commercial-group PPO plan links identified                 452
  Physical rate files selected for extraction                                      1
  Rate observations extracted                                              1,511,715
  Selected-file quality score                                        89.74 (Grade B)
  Provider-rate workbook rows                                                 36,796
  Duplicate workbook keys                                                          0
  Rate-selection conflict flags                                                9,292
  NPI/TIN pairs observed                                                       3,775
  Pairs with NPPES address candidates                                 3,649 (96.66%)
  Payer-verified NPI/TIN/address pairs                                        0 (0%)
  Numeric rate observations unresolved for address linkage                     1,023
  Provider-rate rows with specialty unmapped                                     599

### How to interpret these results

-   **96.66% is candidate-address coverage, not verified-address
    coverage.** The available NPPES and supplemental CMS clinician data
    do not prove that a specific payer TIN is associated with a
    particular current practice address.
-   **The 452 PPO figure counts plan links, not 452 distinct rate
    files.** One selected physical Health Net California file is linked
    in the index to both PPO and non-PPO plans. The project does not
    claim every rate in that shared file is PPO-only.
-   **89.74 (B) is a file-quality screening score.** It is not the
    probability that a rate is correct and is not a substitute for
    rate-level ground-truth validation.
-   **Internal checks passed, but an answer-key comparison was not
    possible.** No official answer key or adjudicated sample rows were
    supplied.
-   **The production run selected one physical rate file.** The
    parallel-processing capability is demonstrated by a two-file
    synthetic test; the production run does not demonstrate a multi-file
    speedup.

## 4. Repository layout

The main project files are organized as follows:

``` text
AIQuest/
├── README.md
├── requirements.txt
├── config_both_tracks.yaml
├── pipeline/
│   ├── c1_parse_index.py
│   ├── c2_identify_ppo.py
│   ├── c3_grade_files.py
│   ├── c4_extract_rates.py
│   ├── c5_map_addresses.py
│   ├── c6_build_rate_file.py
│   ├── common.py
│   ├── main.py
│   ├── track2_audit.py
│   └── selftest_parallel_c4.py
├── data/
│   ├── input/
│   │   ├── indexes/
│   │   ├── rate_benchmarking_weights.xlsx
│   │   ├── nucc_taxonomy_261.csv
│   │   └── provider_directory_template.csv
│   ├── processed_nppes_final/
│   │   ├── plan_file_links.csv
│   │   ├── plan_classification.csv
│   │   ├── unique_rate_files.csv
│   │   ├── selected_ppo_files.csv
│   │   ├── file_quality_metrics.csv
│   │   ├── rate_selection_audit.csv
│   │   ├── provider_address_links.csv
│   │   ├── unresolved_rate_observations.csv
│   │   ├── track2_scorecard.csv
│   │   ├── track2_validation.json
│   │   ├── reproducibility_manifest.json
│   │   └── parallel_processing_selftest.json
│   ├── output_nppes_final/
│   │   ├── provider_rates.xlsx
│   │   ├── summary.csv
│   │   └── summary.json
│   └── input/nppes/                 # large NPPES files added locally for a full rerun
├── docs/
│   ├── logic_file.md
│   ├── quality_metrics.md
│   ├── both_tracks_final_report.md
│   ├── requirement_compliance_audit.md
│   ├── nppes_input_audit.md
│   └── cms_doctors_clinicians_review.md
└── .gitignore                       # recommended for the GitHub repository
```

Some generated files are included as evidence from the documented run.
The large NPPES extracts, selected MRF body, and extracted
rate-observation intermediate are not bundled. A full rerun requires the
NPPES files described below and network access when the MRF is not
cached.

## 5. Pipeline architecture

The pipeline is organized into stages, with relative paths controlled by
`config_both_tracks.yaml`.

``` text
Centene index JSON files
        |
        v
C1: Parse and deduplicate plan-to-file links
        |
        v
C2: Classify California commercial PPO plan links
        |
        v
C3: Grade selected in-network rate files
        |
        v
C4: Stream rate observations and resolve provider references
        |
        v
C5: Link NPI/TIN pairs to practice-address candidates
        |
        v
C6: Select representative rates and build the Excel workbook
        |
        v
Track 2 audit: scorecard, validation, hashes, self-test evidence
```

### Implementation principles

1.  **Evidence-preserving decisions:** retain source index, plan, file,
    provider-reference, taxonomy, and address provenance where
    available.
2.  **Streaming extraction:** use `ijson` to iterate over large JSON
    data rather than materializing the full MRF as a single in-memory
    object.
3.  **SQLite provider-reference lookup:** stage provider-reference
    groups in SQLite so references can be resolved without retaining the
    entire mapping in RAM.
4.  **Bounded file-level parallelism:** separate selected files can be
    processed in a bounded worker pool. Each worker uses isolated
    intermediate state, and output is merged in deterministic source
    order.
5.  **Explicit exceptions:** ambiguous plans, rate conflicts, unresolved
    address candidates, and unmapped specialties are recorded rather
    than silently guessed.
6.  **Reproducibility evidence:** validation reports compare expected
    row counts and key uniqueness; a manifest hashes available inputs,
    code, and generated outputs.

## 6. Challenge-by-challenge implementation

### C1 --- Parse and deduplicate index files

The parser reads 11 Centene index files and builds a normalized
plan-to-file table. It retains plan/file relationships and source
evidence. Exact duplicate links are removed, but distinct plans pointing
to the same physical URL remain separate associations.

**Output:** `data/processed_nppes_final/plan_file_links.csv`

### C2 --- Identify California commercial-group PPO plan links

The classifier uses market-type evidence, California evidence, and
explicit whole-word PPO signals. It avoids treating ambiguous network
names as confirmed PPO plans and routes uncertain or conflicting cases
to review. Explicit non-PPO signals and out-of-scope markets are
excluded.

The documented run identified 452 explicit California group PPO plan
links. The selected Health Net URL is also linked to non-PPO plans.
Therefore, the classification supports identifying PPO-linked plan
associations but does not establish that every rate in the shared
physical file belongs to a PPO plan.

**Outputs:** - `data/processed_nppes_final/plan_classification.csv` -
`data/processed_nppes_final/selected_ppo_files.csv` -
`data/processed_nppes_final/unique_rate_files.csv`

### C3 --- Grade in-network rate files

Each selected file is graded using observable completeness, provider
linkage, rate validity, duplicate control, active-rate share, and
extreme-rate diagnostics. Component metrics are retained so reviewers
can inspect the score rather than relying only on the grade.

The selected file scored **89.74 (B)** in the documented run. The grade
is a quality screen and should not be interpreted as ground truth or
proof that a rate is correctly attributed to a particular plan.

**Output:** `data/processed_nppes_final/file_quality_metrics.csv`

### C4 --- Extract and select negotiated rates

The rate parser streams the in-network JSON and resolves provider
references through a SQLite-backed map. It retains relevant context such
as billing-code type, negotiated type, rate expression, expiration,
billing class, service code, modifier, provider reference, NPI/TIN, and
network-attribution status.

For a provider-rate key with multiple options, the selection policy
prioritizes negotiated dollar rates and then later valid expirations.
When distinct amounts or service contexts remain tied, a median
representative rate is selected and the conflict is flagged. The
alternatives are preserved in the selection audit.

This is a deterministic representative-rate policy, not a claim that
every alternative is economically interchangeable. Review conflict flags
before using the selected value for a downstream contracting decision.

**Output:** `data/processed_nppes_final/rate_selection_audit.csv`

### C5 --- Map NPI/TIN pairs to practice-address candidates

The rate file supplies NPI/TIN pairs. NPPES provides NPI-level primary
and practice-location information, but it does not establish the
payer-specific relationship between a TIN and a practice address.

The pipeline uses the October 2026 monthly NPPES full-replacement
primary file and its Practice Location Reference File, filters to
relevant NPIs and California practice addresses, and excludes PO boxes
and incomplete address records. NPPES-only candidates are explicitly
marked unverified. A payer-directory row can be marked verified only
when the exact NPI, TIN, and practice address match and active/effective
status and recency requirements are met.

No authorized current payer directory was supplied for this run.
Consequently, payer-verified coverage is **0%**, even though 3,649 of
3,775 NPI/TIN pairs have address candidates.

**Outputs:** - `data/processed_nppes_final/provider_address_links.csv` -
`data/processed_nppes_final/unresolved_rate_observations.csv` -
`data/processed_nppes_final/c5_manifest.json`

### C6 --- Build the provider-rate workbook

The final workbook provides candidate-linked provider-rate rows and
preserves unresolved numeric rate observations on a separate worksheet.
It includes address confidence/source, update-date information,
taxonomy/specialty provenance, rate-selection metadata, and
network-attribution context.

**Workbook:** `data/output_nppes_final/provider_rates.xlsx`

The documented workbook has 36,796 unique provider-rate keys and no
duplicate keys under its generated key definition. It retains 1,023
unresolved observations and 599 specialty-unmapped rows. These are
visible limitations, not silently filled values.

### C7 --- Optional rate cost-effectiveness analysis

C7 is **not implemented**. The selected physical file is shared across
PPO and non-PPO plan links, and the supplied procedure-weight mapping
does not cover all selected provider-rate rows. Ranking rates now could
imply that unlike plans or provider/service contexts are directly
comparable. C7 should be reconsidered only after network attribution and
benchmark coverage are adequate.

## 7. File-quality scoring methodology

The score is a weighted screening grade calculated from observed rate
rows. If an entire component cannot be measured, the total is
renormalized over the available component weights rather than assigning
zero to a missing component.

  ------------------------------------------------------------------------
  Component                                   Weight Calculation / intent
  --------------------- ---------------------------- ---------------------
  Provider linkage                               30% Share of rate rows
                                                     with a valid 10-digit
                                                     NPI and 9-digit TIN

  Required-field                                 25% Mean nonblank share
  completeness                                       across billing code,
                                                     code type,
                                                     negotiation
                                                     arrangement,
                                                     negotiated type, and
                                                     rate expression

  Valid numeric rate                             20% Share of rows with a
                                                     nonnegative parseable
                                                     dollar amount;
                                                     percentage
                                                     expressions are
                                                     preserved but are not
                                                     counted as numeric
                                                     dollar rates

  Exact-duplicate                                10% One minus the
  control                                            duplicate share under
                                                     a contextual key
                                                     including NPI, TIN,
                                                     code, class, type,
                                                     amount, expiration,
                                                     service code, and
                                                     modifier

  Active-rate share                              10% Share of non-expired
                                                     rows among rows with
                                                     a parseable
                                                     expiration date;
                                                     unknown expiry dates
                                                     do not enter this
                                                     denominator

  Extreme-rate control                            5% One minus the outlier
                                                     share among
                                                     comparable code/class
                                                     groups with at least
                                                     10 rows; an outlier
                                                     is more than 10 times
                                                     or less than
                                                     one-tenth of the
                                                     group median
  ------------------------------------------------------------------------

**Grade bands:** A = 90--100; B = 80--89.99; C = 70--79.99; D =
60--69.99; E = below 60. A file whose body is unavailable is marked
`NOT_GRADED`.

Additional diagnostics include nonzero-rate share, unique billing codes,
coverage against the supplied specialty/procedure mapping,
negotiated-type distribution, index publication date, and file-level
PPO/HMO linkage status. These are diagnostics rather than components of
the score. Zero-dollar entries are retained because a zero can be
legitimate. Outlier flags prompt review; they do not automatically
delete records.

Full metric definitions and rationale are documented in
[`docs/quality_metrics.md`](docs/quality_metrics.md).

## 8. Provider-rate selection and exception handling

Negotiated rates may differ by rate type, billing class, service code,
modifier, expiration, and other source context. A duplicate-looking row
is not necessarily interchangeable with another row.

The pipeline therefore: - prioritizes negotiated dollar rates under the
documented selection rule; - considers valid expiration dates; - flags
tied distinct amounts or service contexts; - retains alternatives in the
selection audit; - preserves unresolved numeric observations in a
separate output; - avoids treating the representative rate as a
ground-truth answer when the source provides conflicting options.

The documented run reports 9,292 selection-conflict flags. Review these
flags before treating selected rates as directly comparable.

## 9. Address matching and confidence

The project distinguishes three concepts:

1.  **Candidate address:** a plausible address associated with an NPI,
    for example through NPPES.
2.  **Corroborated candidate:** an address supported by additional
    evidence, but without a source proving the exact payer
    TIN-to-address relationship.
3.  **Payer-verified NPI/TIN/address link:** an exact match from an
    authorized current payer source, with appropriate active/effective
    status and sufficiently recent verification/update information.

NPPES-only candidates are marked `nppes_only_unverified` with confidence
0.30 and `TIN Address Link Verified=false`. Address normalization can
improve string comparison but cannot prove that a provider practices at
an address or participates in a commercial network.

The supplemental CMS Doctors and Clinicians dataset was reviewed
separately. It can provide NPI-level clinician/address or
Medicare-specialty context, but it does not supply the payer TIN,
commercial plan/network participation, or the required per-address
verification evidence. Those records were not used to claim payer
verification.

## 10. Setup

### Requirements

-   Python 3.10 or newer
-   Git (optional, for repository version control)
-   Network access if the selected MRF has not already been cached
-   The large NPPES primary and practice-location files for a full fresh
    run

### Create a virtual environment

From the repository root:

**Windows PowerShell**

``` powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**macOS/Linux**

``` bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Dependencies include `ijson`, `pandas`, `openpyxl`, `requests`,
`PyYAML`, `tqdm`, `usaddress`, `numpy`, `aiohttp`, `aiofiles`, and
`jsonschema`. See `requirements.txt` for the authoritative list.

## 11. Input data required for a full rerun

The configuration uses relative paths. The full NPPES extracts are
intentionally not included because of their size.

Place the October 2026 NPPES **monthly full-replacement** primary file
and the corresponding Practice Location Reference File at these
configured paths:

``` text
data/input/nppes/npidata_pfile_20050523-20261011.csv
data/input/nppes/pl_pfile_20050523-20261011.csv
```

Use the exact official files corresponding to the configured snapshot.
Do **not** append a weekly delta file to the monthly full replacement.
Confirm the downloaded filenames and update the configuration if the
official filenames differ.

Other inputs included in the project are: - Centene index JSON files
under `data/input/indexes/` -
`data/input/rate_benchmarking_weights.xlsx` -
`data/input/nucc_taxonomy_261.csv`

The selected MRF source body and extracted rate-observation intermediate
are not included in the compact handoff. The pipeline can download an
uncached MRF when network access is available. The exact source URL and
hashes for available run inputs are recorded in the reproducibility
manifest.

### Optional payer directory

The configuration references `data/input/provider_directory.csv`. A
template is provided at `data/input/provider_directory_template.csv`. A
template is not a source of verified data. Only populate the provider
directory with an authorized source that supports exact NPI, TIN,
practice address, network/plan, active/effective status, and a recent
verification/update date. Configure the column map in
`config_both_tracks.yaml` if the source uses different field names.

Do not mark NPPES-only rows as payer-verified.

## 12. Run the pipeline

Run commands from the repository root, where `config_both_tracks.yaml`
is located.

### Full run

``` bash
python -m pipeline.main --config config_both_tracks.yaml
```

The pipeline runs the configured stages and writes generated artifacts
to the configured `data/processed_nppes_final/` and
`data/output_nppes_final/` locations.

### Validate existing generated outputs

``` bash
python -m pipeline.track2_audit --config config_both_tracks.yaml
```

This audits available outputs. It does not substitute for a fresh
extraction or external ground-truth verification.

### Run the parallel-processing self-test

``` bash
python -m pipeline.selftest_parallel_c4
```

The self-test uses a synthetic two-file fixture to check overlapping
workers and byte-identical output relative to sequential processing. It
demonstrates correctness of the tested parallel path, not a production
speedup for this run, which selected one physical rate file.

## 13. Validate generated outputs

After a run, inspect:

1.  The terminal log and process exit status.
2.  `data/output_nppes_final/summary.json` and `summary.csv`.
3.  `data/processed_nppes_final/track2_validation.json`.
4.  `data/processed_nppes_final/track2_scorecard.csv`.
5.  `data/processed_nppes_final/reproducibility_manifest.json`.
6.  `data/processed_nppes_final/parallel_processing_selftest.json`.
7.  The workbook sheets and unresolved-rate counts.

The archived validation records report that workbook row counts agree
with the C6 manifest, the generated provider-rate key is unique,
expected worksheets are present, and unresolved worksheet counts
reconcile with the corresponding manifest. These are internal
consistency checks. They do not prove that a selected rate matches an
unavailable official answer key.

When refreshing outputs, regenerate the workbook and all run reports
from the same code version and inputs. Do not combine artifacts from
different project versions.

## 14. Output files and how to interpret them

  ----------------------------------------------------------------------------------------------------
  Path                                                             Purpose
  ---------------------------------------------------------------- -----------------------------------
  `data/output_nppes_final/provider_rates.xlsx`                    Main deliverable; provider-rate
                                                                   rows and unresolved-rate worksheet

  `data/output_nppes_final/summary.json`                           Machine-readable run summary and
                                                                   completion status

  `data/output_nppes_final/summary.csv`                            Tabular summary metrics

  `data/processed_nppes_final/plan_file_links.csv`                 Normalized plan-to-file links

  `data/processed_nppes_final/plan_classification.csv`             PPO inclusion/exclusion/review
                                                                   decisions and evidence

  `data/processed_nppes_final/unique_rate_files.csv`               Unique physical rate-file inventory

  `data/processed_nppes_final/selected_ppo_files.csv`              Selected file inventory and
                                                                   selection evidence

  `data/processed_nppes_final/file_quality_metrics.csv`            File-level score and component
                                                                   metrics

  `data/processed_nppes_final/rate_selection_audit.csv`            Rate options, selection decisions,
                                                                   and conflict evidence

  `data/processed_nppes_final/provider_address_links.csv`          Candidate/verified address links
                                                                   and provenance

  `data/processed_nppes_final/unresolved_rate_observations.csv`    Numeric observations that could not
                                                                   be linked to an address candidate

  `data/processed_nppes_final/track2_scorecard.csv`                Coverage, linkage, and scalability
                                                                   measurements

  `data/processed_nppes_final/track2_validation.json`              Automated validation outcomes and
                                                                   known gaps

  `data/processed_nppes_final/reproducibility_manifest.json`       SHA-256 hashes for available
                                                                   inputs, code, and outputs

  `data/processed_nppes_final/parallel_processing_selftest.json`   Synthetic parallel test evidence

  `data/processed_nppes_final/c1_manifest.json` through            Stage-level counts and status
  `c6_manifest.json`                                               summaries

  `data/processed_nppes_final/cms_source_audit.json`               Supplemental CMS Doctors and
                                                                   Clinicians source review
  ----------------------------------------------------------------------------------------------------

## 15. Track 2: scalability and reproducibility

### Scalability

-   Large MRF JSON is streamed using `ijson`.
-   Provider-reference mappings are staged in SQLite.
-   NPPES processing uses batches.
-   Distinct rate files can be processed in a bounded worker pool.
-   Worker outputs are merged in deterministic source order.

The configured worker limit is in `config_both_tracks.yaml`. Since the
documented production selection contains one physical rate file, the
production run uses one worker. The synthetic two-file test verifies
that the parallel path can overlap work and match sequential output, but
no production speedup is claimed.

### Coverage and linkage

The scorecard distinguishes: - plan/file coverage; - extracted rate
observations; - NPI/TIN pairs with address candidates; - payer-verified
address links; - unresolved numeric rate observations; -
specialty-mapping coverage.

Candidate coverage must not be described as verified linkage. The
current run's payer-verified NPI/TIN/address share is 0%.

### Correctness and reproducibility

The pipeline checks internal invariants, including row counts, required
workbook sheets, unique provider-rate keys, and unresolved-row
reconciliation. The manifest records hashes for available input indexes,
mapping inputs, source bodies, pipeline code, and generated outputs.

An official answer key was not provided, so the answer-key sample-rate
comparison was not performed. Internal validation and reproducibility
hashes are not a substitute for independent correctness adjudication.

## 16. Known limitations and next steps

The following gaps remain explicit in the current handoff:

1.  **Plan-level rate attribution:** the selected physical Health Net
    MRF is linked to both PPO and non-PPO plans. Obtain a plan-specific
    in-network MRF or an authoritative mapping from each
    rate/provider-reference to the relevant PPO plan/network.
2.  **Payer-verified provider addresses:** no authorized current payer
    directory was supplied. Obtain a source with exact NPI, TIN,
    practice address, network/plan, active/effective dates, and recent
    verification/update date.
3.  **Unresolved rate observations:** 1,023 numeric observations across
    126 NPI/TIN pairs have no NPPES address candidate in the current C5
    linkage output. Keep them unresolved until suitable evidence is
    available.
4.  **Specialty mapping:** 599 provider-rate rows remain
    specialty-unmapped. Add a valid specialty source or retain explicit
    `Unmapped` values.
5.  **Answer-key validation:** obtain official expected sample rows and
    compare extracted rates, provider keys, and selection decisions.
6.  **Demonstration video:** a separate video of no more than five
    minutes presents the pipeline, representative outputs, validation
    evidence, and the interpretation of known data limitations.
7.  **Optional C7:** defer cost-effectiveness ranking until network
    attribution and procedure-weight coverage support valid comparisons.

The project is not presented as fully data-accepted where source
evidence is missing. Its value is an auditable, rerunnable pipeline with
quantified gaps rather than guessed relationships.

## 17. Data provenance and responsible use

-   **Centene index files:** plan names, IDs, market types, and
    rate-file links.
-   **CMS Transparency in Coverage in-network files:** negotiated-rate
    entries and provider-reference groups. See the [CMS in-network
    schema
    documentation](https://github.com/CMSgov/price-transparency-guide/blob/master/schemas/in-network-rates/README.md).
-   **NPPES monthly full replacement and Practice Location Reference
    File:** NPI details, taxonomy, primary and additional
    practice-location candidates, and update-date signals.
-   **NUCC taxonomy v26.1:** taxonomy display labels where the supplied
    crosswalk has no mapping.
-   **CMS Doctors and Clinicians:** supplemental NPI-level
    clinician/address and Medicare-specialty review; not treated as
    payer TIN/address or commercial-network verification.
-   **Payer provider directory:** not supplied in an authorized
    machine-readable form for this run; needed for exact payer
    verification.

Do not publish raw datasets or provider-level outputs without checking
applicable data-use terms, access restrictions, and the hackathon's
sharing rules. Do not bypass login, VPN, or access controls. A public
search page is not automatically authorization for bulk automated
extraction.

## 18. Evaluation-criterion mapping

  ------------------------------------------------------------------------------------------
  Problem-statement criterion   Project evidence                     Status / caveat
  ----------------------------- ------------------------------------ -----------------------
  C1 --- Parse index files      `plan_file_links.csv`, C1 manifest   Complete in the
                                                                     documented run

  C2 --- Identify PPO           `plan_classification.csv`,           Plan-link
  plans/files                   `selected_ppo_files.csv`, logic file classification
                                                                     implemented; rate-level
                                                                     attribution limited by
                                                                     shared mixed-plan file

  C3 --- Grade PTD files        `file_quality_metrics.csv`,          Implemented for
                                `docs/quality_metrics.md`            selected file; score is
                                                                     a screening grade

  C4 --- Extract rates          `pipeline/c4_extract_rates.py`,      Implemented with
                                `rate_selection_audit.csv`           explicit selection and
                                                                     conflict handling

  C5 --- Map NPI/TIN to         `provider_address_links.csv`,        Partial; payer-verified
  practice address              `unresolved_rate_observations.csv`   coverage is 0%

  C6 --- Build provider-rate    `provider_rates.xlsx`, C6 manifest   Workbook generated and
  file                                                               internally checked;
                                                                     unresolved and unmapped
                                                                     records remain

  C7 --- Cost effectiveness     Not implemented                      Deferred because plan
  (optional)                                                         attribution and weight
                                                                     coverage are
                                                                     insufficient

  Track 2 --- Scalability       Streaming parser, SQLite, bounded    Parallel path tested
                                worker pool, self-test               synthetically;
                                                                     production run has one
                                                                     selected file

  Track 2 --- Coverage/linkage  `track2_scorecard.csv` and           Quantified; candidate
                                validation report                    addresses are not
                                                                     payer-verified

  Track 2 ---                   Validation report and hash manifest  Internal checks passed;
  Correctness/reproducibility                                        answer-key comparison
                                                                     unavailable

  Demonstration video           Separate video, max 5 minutes        Demonstrates the solution,
                                                                     outputs, validation, and
                                                                     known data limitations
  ------------------------------------------------------------------------------------------

------------------------------------------------------------------------

## License and reuse

No explicit software license is declared in this repository. Do not
assume that the source datasets, rate files, or provider data may be
redistributed simply because they were publicly accessible. Add a
license only after confirming the rights and terms that apply to the
code and all included materials.
