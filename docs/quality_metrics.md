# PTD File Quality Metrics

The score uses observed rows from each selected in-network rate file. It is a transparent screening grade, not a statistical probability that a contract is correct. Every raw component remains visible in file_quality_metrics.csv.

| Component | Weight | Calculation | Why it matters |
|---|---:|---|---|
| Provider linkage | 30% | Rate rows with a 10-digit NPI and 9-digit TIN divided by all rate rows | The target product is provider-level and requires both identifiers. Unresolved provider references reduce usable coverage. |
| Required-field completeness | 25% | Mean nonblank share across billing code, code type, negotiation arrangement, negotiated type, and rate expression | Missing any of these limits interpretation or matching to a benchmark. |
| Valid numeric rate | 20% | Nonnegative parseable dollar rate rows divided by all rate rows | Negative or unparseable amounts cannot be compared as dollar rates. Percentage expressions remain preserved but are not counted as numeric dollars. |
| Exact-duplicate control | 10% | One minus the duplicate share for NPI, TIN, code, class, type, amount, expiration, service code, and modifier | Repeated records inflate counts and can distort file summaries. Duplicate rate rows remain available in the observation output. |
| Active-rate share | 10% | Nonexpired rows divided by rows with a parseable expiration date | Directly exposes stale entries. Rows without an expiration date are unknown and do not enter this denominator. |
| Extreme-rate control | 5% | One minus the outlier share among comparable groups with at least 10 rows; an outlier is over 10 times or below one-tenth of the same code/class median | Flags extreme values for review without assuming that a high negotiated rate is automatically wrong. |

The total is the weighted mean of available components. If an entire component cannot be measured, the score is renormalized over the remaining available weights; it is never set to zero. The output records component scores so reviewers can see when a total uses fewer than all six components.

Quality bands are A (90–100), B (80–89.99), C (70–79.99), D (60–69.99), and E (below 60). Files whose bodies are unavailable are marked NOT_GRADED.

Additional diagnostics are reported but do not affect the score:

- Nonzero-rate share. Zero-dollar records are retained because zero can be legitimate; they are not automatically treated as invalid.
- Unique billing codes and the share found in the supplied specialty/procedure workbook. That workbook covers weighted specialty/procedure combinations, not every valid CPT/HCPCS code, so this is a support indicator rather than a completeness denominator.
- Negotiated-type distribution.
- Index publication date and file-level PPO/HMO linkage status. Index age is not substituted for contract expiration.

The extreme-rate check is a screening rule. It groups only by billing code and billing class because the supplied attachments contain no Medicare benchmark, geographic allowables, modifier-specific fee schedule, or complete place-of-service reference. Flagged rows are retained for review and are not removed from the raw rate observations.
