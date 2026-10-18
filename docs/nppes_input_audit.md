# NPPES Input Audit

Audit date: 2026-10-17  
Folder reviewed: user-provided NPPES download folder

## Verdict

All three downloaded packages are valid CMS NPPES artifacts. The monthly full replacement contains the files needed for the address-candidate stage. The weekly package is an incremental update and should not be appended to the replacement snapshot without applying update semantics. The deactivated report is supplementary, not an address source.

## Files checked

| Download | Contents checked | Verdict | Pipeline use |
|---|---|---|---|
| NPPES_Data_Dissemination_October_2026_V2 2 | npidata_pfile_20050523-20261011.csv (11,758,264,877 bytes); pl_pfile_20050523-20261011.csv (121,926,581 bytes); other-name/reference files, headers, and code-value documentation | Correct October 2026 monthly full replacement v2. The primary file contains NPI, entity type, primary practice address, taxonomy, and update date. The practice-location reference file contains non-primary locations keyed by NPI. | Used primary file and Practice Location Reference File. |
| NPPES_Data_Dissemination_100526_101126_Weekly_V2 | Weekly main file and reference files dated 2026-10-05 through 2026-10-11 | Correct weekly incremental v2 package. | Not merged with the monthly replacement snapshot to avoid duplicate or conflicting updates. |
| NPPES_Deactivated_NPI_Report_101226_V2 | NPPES Deactivated NPI Report 20261012.xlsx | Correct October 12 deactivation report. | Supplementary only; not an address source. |

The CMS NPI Files page identifies the monthly replacement and separate non-primary Practice Location Reference File. The CMS data-dissemination page describes monthly and weekly release families: [CMS NPI Files](https://download.cms.gov/nppes/NPI_Files.html), [CMS Data Dissemination](https://www.cms.gov/medicare/regulations-guidance/administrative-simplification/data-dissemination).

## Address-stage results

- 3,775 distinct NPI/TIN pairs appeared in the selected rate file.
- 3,649 pairs (96.66%) had at least one California NPPES practice-address candidate.
- 5,436 candidate rows were produced. Mailing addresses, PO boxes, non-California addresses, and invalid/incomplete addresses were excluded.
- 0 candidate rows are verified to the MRF TIN. NPPES is keyed by NPI and has no payer-TIN association.
- Of 3,639 candidate rows with a parseable NPPES last-update date, 2,627 (72.19%) were last updated before 2025; 1,193 were updated before 2020. Another 1,797 rows had no parseable update date in the source rows used. A record-update date is only a freshness signal, not proof that the address was physically verified.

## Specialty mapping

The supplied workbook contains 213 taxonomy codes in its Taxonomy-Spec Xwalk. Official NUCC v26.1 (July 1, 2026) was used as a fallback for codes not represented there. The output records the mapping source. Source: [NUCC taxonomy CSV](https://nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40/csv-mainmenu-57). Taxonomy describes a provider-reported classification; it does not establish services rendered, payer participation, or network status.

## Remaining evidence

A current payer directory with NPI, TIN, practice address, and plan/network participation is required to verify TIN/location relationships. CMS Doctors and Clinicians data can corroborate clinician address and specialty for Medicare-enrolled providers, but it does not prove commercial payer TIN or network participation.
