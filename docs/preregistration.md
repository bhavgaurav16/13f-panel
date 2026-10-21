# Pre-registration: do high-conviction 13F managers beat the market after the filing delay?

**Status:** registered on 21 October 2026, before any return was computed.
The commit that adds this file must come before the commit that adds backtest results. Any later change is logged under *Amendments* with its date and reason.

## 1. Hypothesis

**H1.** Stocks that high-conviction managers newly buy or add to, held for one quarter from the manager's first filing date, earn a positive return in excess of an equal-weight portfolio of all 13F-held stocks over the same dates.

**H0.** The mean excess return is zero or negative.

## 2. Universe and definitions

| Term | Definition |
|---|---|
| Quarter Q | The 13F period of report (quarter end). |
| Candidate manager | `is_candidate` in `data/features/Q.parquet`: AUM ≥ $50m, ≤ 200 positions, ETF weight ≤ 50%. |
| High-conviction manager | A candidate manager in the top quintile (≥ 80th percentile, within Q's candidates) for **both** `active_share` and `top10_wt`. About 13% of candidates (276 in 2013Q2, 533 in 2026Q1). |
| Signal position | A non-ETF holding of a high-conviction manager with status `new`, or status `added` (shares up versus Q−1). Positions with status `first_filing` are excluded because they have no prior quarter. |
| Filing date | The date of the manager's **original** 13F-HR for Q. Amendments (13F-HR/A) filed later are ignored for timing and content. |

Note: the current panel keeps the latest amendment. Step 3 builds an as-first-filed view before any return is computed.

## 3. Trades

- **Entry:** close of the first trading day after the filing date.
- **Exit:** close 63 trading days after entry (about one quarter).
- **Weighting:** each signal position gets equal weight in its quarter cohort. A stock bought by several managers appears once per manager.
- **Benchmark (primary):** for each signal position, the equal-weight return of all non-ETF stocks held by any 13F filer in Q, over the same entry and exit dates.
- **Benchmark (secondary):** SPY total return over the same dates.
- **Costs:** 20 basis points round trip per position.
- **Delistings:** a delisted stock earns its return up to its last available price. That return is then held flat to the exit date.

## 4. Sample

- **Cohorts:** 2013Q3 to 2026Q1. 2026Q2 is excluded because its holding window is not complete.
- **In-sample:** 2013Q3–2021Q4. **Out-of-sample:** 2022Q1–2026Q1.

## 5. Statistics and decision rule

- Series: one net excess return per cohort (mean over its signal positions).
- Report: mean quarterly net excess return, t-statistic with Newey-West standard errors (1 lag), annualised information ratio, hit rate, and the same figures before costs.
- **H1 is supported only if both conditions hold:**
  1. Full-sample mean net excess return > 0 with t ≥ 2.0.
  2. Out-of-sample mean net excess return > 0.
- Otherwise the result is reported as a null result. It is published either way.

## 6. Robustness checks (reported; not used for the decision)

1. Fixed entry on day 46 after quarter end for all managers.
2. Weight stocks by the number of high-conviction buyers instead of per manager.
3. Exclude IPO allocations (stocks with no 13F holders in Q−1).
4. Exclude the top-10 AUM tier.
5. Two-quarter holding period.
6. Vs SPY instead of the equal-weight 13F benchmark.

## 7. Data quality rules

- Price coverage is reported as the share of signal positions with a usable return. If coverage is below 90% in any cohort, that cohort is flagged in the results.
- Positions without a usable return are excluded and counted, not filled.
- The price source and the CUSIP-to-ticker mapping are fixed in step 3 and logged under *Amendments* before any return is computed.

## Amendments

_None yet._
