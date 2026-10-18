# CMS Doctors and Clinicians Source Review

Review date: 2026-10-18  
Local source: `DAC_NationalDownloadableFile.csv` (899,709,162 bytes; local file timestamp 2026-10-18; SHA-256 `96a271ae51b79631f5ec5fe096b461c34fffbb3bd4cc1aca0a31023161a1890b`)  
Official dataset: [CMS Doctors and Clinicians National Downloadable File](https://data.cms.gov/provider-data/dataset/mj5m-pzi6)  
Field definitions: [CMS Doctors and Clinicians Data Dictionary](https://data.cms.gov/provider-data/sites/default/files/data_dictionaries/physician/DOC_Data_Dictionary.pdf)

## Download and release check

The file name and all 31 columns match the CMS National Downloadable File. CMS's current metastore API reports a 2026-10-02 modified date, 2026-10-15 release date, and points to the [current CSV resource](https://data.cms.gov/provider-data/sites/default/files/resources/52c3f098d7e56028a298fd297cb0b38d_1790972133/DAC_NationalDownloadableFile.csv). The local file was observed on 2026-10-18 and parses to 3,630,233 data rows, which is consistent with a new release.

The CMS dataset detail page still surfaced an older August 18 / September 10 snapshot and 3,388,628 rows during this review. That count conflicts with the current metastore metadata and local row count; it appears stale. The local file's SHA-256 is recorded above for reproducibility. CMS's current metadata and exact filename support that the correct dataset was downloaded; the stale landing-page count does not change the supplemental-only decision below.

## What was checked

The full CSV was streamed and matched on NPI to the 3,734 unique NPIs in the selected rate-file population. The file has 31 columns and 3,630,233 data rows. It contains 502 rows for 331 rate-file NPIs. Of those, 486 rows across 327 NPIs list a California location. A simple completeness screen found 449 distinct California address candidates (street, city, and five-digit ZIP; PO Boxes excluded).

For the 125 NPIs in the unresolved-rate audit, two NPIs have a complete California CMS address candidate. The file also has a primary Medicare specialty for two of the ten unique NPIs represented by `Unmapped` rate rows; one of those NPIs has multiple distinct primary-specialty values across CMS records. These are useful leads for review, but do not justify choosing a single taxonomy classification automatically.

## Appropriate use and limits

CMS describes this file at the clinician, Medicare enrollment, group, and address level, with a 12-month claims lookback. Its group identifier is a PECOS `org_pac_id`; the file does not provide the Centene rate-file TIN, commercial payer participation, or an address verification date. A CMS location can therefore be retained as an independent NPI-level candidate, but it cannot verify that a rate-file TIN contracted at that location or participated in a Centene PPO network.

The two California locations associated with unresolved NPIs were **not** appended to `provider_address_links.csv` or the final rate workbook. Doing so would attach a Medicare group/address to each rate-file TIN without evidence for that relationship. The existing `Unresolved Rates` rows and their counts remain unchanged. If a future reviewer wants these candidates in the workbook, add them only as a separately labeled CMS candidate source, keep `TIN Address Link Verified` false, and preserve the TIN-level exception until payer evidence is available.

The CMS specialty fields are Medicare specialty labels, not NUCC taxonomy codes and not the supplied taxonomy-to-specialty crosswalk. They are kept separate from the canonical specialty output. The optional C7 peer ranking remains unsupported by this source because it does not isolate PPO participation or assign a rate to a plan.

## Review of the proposed Centene MRF steps

| Proposed step | Verdict for this project |
|---|---|
| Search the Centene portal for a plan or EIN | Valid discovery method, but redundant here: the supplied 11 Centene index files already provide the Table of Contents links, and C1 parsed them. |
| Download each Table of Contents JSON | Valid. The supplied index JSONs are the inputs already used by C1. |
| Select `in_network_files[].location` from the ToC | Valid. Use the structured `in_network_files` field and preserve its description and source index; matching a substring in a filename alone is brittle. C1 already does this. |
| Download all selected rate files | Valid when disk, network, and source terms allow it. The selected 2026 file is already cached locally as a 701,615,514-byte JSON file; C4 streams it, so no new download is needed. If a future URL ends in `.gz`, the reader handles it as compressed input. |
| Parse `provider_references` and join rates through reference IDs | Valid in principle and already implemented for this file. The actual file declares schema `2.0.0`, with root `provider_references` objects holding `provider_group_id` and nested `provider_groups` (NPI/TIN); each negotiated-rate record refers to those IDs. CMS identifies schema 2.0 as the current format for files published from March 2026 onward. The external `location` provider-reference pattern is version-dependent and is not used by this v2 file. See the [CMS technical clarification](https://www.cms.gov/healthplan-price-transparency/resources/technical-clarification) and [CMS in-network schema](https://github.com/CMSgov/price-transparency-guide/blob/master/schemas/in-network-rates/in-network-rates.json). |
| Assign every provider/rate to a single Plan ID from file metadata | Not valid for the selected file. The physical URL is linked to both PPO and non-PPO ToC entries, and the provider-reference relationship is at the file/network scope. Do not copy one of the linked plan IDs onto every rate. Preserve the rate file ID and the separate file-to-plan link table; exact rate-to-plan attribution needs plan-specific files or a payer mapping. |
| Export NPI, TIN, and Plan ID as if they were one-to-one | Not valid for this shared file. Keep NPI, TIN, rate-file ID, provider-reference ID, rate, and the separate plan/file association. Mark plan attribution as shared/ambiguous unless the source establishes a unique plan. |

The user mentioned activating a VPN in Chrome. A browser VPN extension may route only browser traffic, so a fresh Python download might not use it. This does not affect the current deliverables because the rate file is already cached and parsed.

## Result

The CMS file adds a small number of review leads and confirms the earlier source assessment: it can corroborate some clinician locations and specialty labels, but does not close the project’s payer TIN/address, PPO participation, or rate-to-plan gaps. The exact counts are stored in `data/processed_nppes_final/cms_source_audit.json`.
