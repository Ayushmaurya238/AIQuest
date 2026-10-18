# Centene PPO Network and Provider-Address Logic

## Scope

This submission targets Track 1: logic (40 points), metrics (35 points), and structure (25 points). It focuses on Centene California commercial group plans and Type 1/Type 2 NPIs. Each inclusion rule, source, confidence level, and unresolved case remains auditable.

## C1: index parsing

The pipeline streams reporting structures from 11 Centene indexes. It records every plan-to-file relationship, source index, plan name/ID, market type, state evidence, URL, and stable file ID. Exact duplicate plan-file links are removed; distinct plans pointing to one physical URL remain separate. Since the reporting entity is often the generic Centene Management Company LLC, brand is derived from the source index filename with plan/entity names as fallback.

## C2: PPO classification

Inclusion requires explicit group/small-group/large-group market type, California evidence from a state-bearing HIOS ID, file URL, or labeled publisher context, and a whole-word PPO signal. PPO names with HMO/EPO/POS cues, out-of-area names, unknown state, and ambiguous network wording go to review. Explicit non-PPO signals are excluded.

The run found 452 explicit California group PPO plan links, with 132 ambiguous and 3 conflicting links routed to review. The publisher's Arkansas market page provides separately labeled context for QualChoice EIN-only records with no state segment.

## Shared-file limitation

The selected Health Net CA in-network URL is linked to 452 PPO plan links and 725 non-PPO links. The pipeline extracts the physical file once and labels every observation shared_mixed_plan_file. It does not claim each rate is PPO-only. Rate-level attribution requires a provider directory or MRF provider/network mapping.

## C3: file-quality scoring

Metrics are measured from extracted observations, not inferred from index publication date. The selected file scores 89.74 (B). This is a screening score, not a probability that a rate is correct. Component scores and duplicate/outlier diagnostics remain in file_quality_metrics.csv. Rows flagged for review are retained.

## C4: rate extraction and selection

Rate JSON is streamed; provider references are resolved with SQLite. Raw observations retain billing-code type, negotiated type, rate expression, expiration, billing class, service code, modifier, provider reference, NPI/TIN, and network attribution.

For each NPI/TIN/address/billing-code key, negotiated dollar rates are prioritized, then later valid expiration. Tied distinct amounts or service contexts use a median representative rate and are flagged. All options remain in rate_selection_audit.csv. Numeric observations without a resolvable NPI/TIN address candidate are reported as unresolved.

## C5: NPI, TIN, and address candidates

The MRF provides NPI/TIN pairs. NPPES is keyed by NPI and contains primary and non-primary practice locations, but it does not associate a location with a payer TIN. The pipeline uses the October 2026 monthly replacement primary file and Practice Location Reference File, filters to NPIs in the rate file, keeps California practice addresses, and excludes PO boxes and incomplete records.

The result is 5,436 candidate rows across 3,649 of 3,775 pairs. NPPES-only candidates are nppes_only_unverified, confidence 0.30, and TIN Address Link Verified=false. A payer-directory row is marked verified only when NPI, TIN, and practice address match; the listing is active or effective on the run date; and its update/verification date is within the configured 365-day window. Independent corroboration without an exact pair source can raise confidence but does not prove a TIN/location link.

Among dated candidates, 72.19% of parseable NPPES record-update dates predate 2025. The date is a freshness signal, not proof that the address was verified.

## Specialty mapping

The supplied Taxonomy-Spec Xwalk maps 213 taxonomy codes to the workbook's specialty categories. If it does not include the NPPES taxonomy code, the pipeline uses the official NUCC v26.1 Display Name. The workbook records Specialty Source. When both sources lack a description, the provider-rate row is labeled `Unmapped` with source `no_taxonomy_mapping`; the pipeline does not infer a specialty. Billing-code/specialty matching remains a separate measure based on the supplied Spec-Proc Weights sheet; that mapping is supplemental and not exhaustive.

## C6: final workbook

The workbook contains 36,796 candidate-linked provider-rate rows with address confidence, address source, source update date, taxonomy code, specialty source, rate-selection metadata, and network attribution. It includes an `Unresolved Rates` sheet and a row-level CSV audit for observations that cannot join to a C5 NPI/TIN address candidate. The latest rerun reports 1,023 unresolved observations and 9,292 conflicting selections. Zero TIN/address pairs are verified because no payer directory was supplied. A separate CMS review found NPI-only California address candidates for two unresolved NPIs; these were not added to C5 or C6 because the CMS file does not provide the rate-file TIN relationship.

## Reproducibility and source roles

- CMS NPPES monthly full replacement: NPI entity type, primary practice address, taxonomy, and update date.
- CMS Practice Location Reference File: non-primary practice locations.
- NUCC taxonomy v26.1: taxonomy display labels missing from the supplied workbook crosswalk.
- [CMS Doctors and Clinicians](https://data.cms.gov/provider-data/dataset/mj5m-pzi6): reviewed separately as possible address and Medicare-specialty corroboration for Medicare-enrolled clinicians. Its row grain is clinician/enrollment/group/address, and it does not establish commercial payer TIN or network participation. The matched-source counts and limitations are in `docs/cms_doctors_clinicians_review.md` and `data/processed_nppes_final/cms_source_audit.json`; candidates were not merged into the payer-linked rate workbook.
- [Health Net California Find a Provider](https://findaprovider.healthnetcalifornia.com/): an official public search for current plan participation and practice locations. It is not an approved bulk input for this run; the site's terms restrict automated access. Use a payer-provided or otherwise authorized export with NPI, TIN, practice address, active/effective status, and a recent verification date before marking exact links verified.
- A current payer provider directory with exact NPI, TIN, practice address, active/effective status, and a recent verification date is needed to verify C5. Plan/network fields are additionally needed to confirm provider-level PPO participation. The supplied files did not contain an authorized machine-readable export; even provider participation would not identify which plan generated a rate in the shared MRF.
- Address standardization: can normalize strings but cannot validate that a provider practices at an address.

The optional C7 cost-effectiveness ranking is not published. The selected physical MRF is shared across PPO and non-PPO plan links, and the supplied procedure-weight mapping covers only a small share of selected provider-rate rows. Ranking prices before resolving those two coverage issues would risk presenting a mixed-network comparison as a PPO cost-effectiveness result.

See docs/track1_run_report.md, docs/nppes_input_audit.md, and docs/public_source_gap_review.md for results and source links.
