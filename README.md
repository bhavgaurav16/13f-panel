# 13F manager panel

Builds a quarterly, per-manager dataset from the SEC Form 13F bulk data sets (2013Q2 onward) using GitHub Actions,
because the analysis environment cannot reach sec.gov directly. Outputs live in `data/`:

| Path | Contents |
|---|---|
| `data/features/<Q>.parquet` | One row per filer: AUM, positions, effective N, top-10 weight, ETF weight, active share, option notionals, flows, turnover, new/exit weight, trim breadth and dispersion (redemption signature), candidate flag |
| `data/holdings/<Q>.parquet` | Position-level books (with quarter-on-quarter change and status) for candidate managers: AUM >= $50m, <= 200 positions, ETF weight <= 50% |
| `data/prices/<Q>.parquet` | Per CUSIP: issuer, median implied quarter-end price, number of holders, total held |
| `data/checks/<Q>.json` | Pass/fail against figures transcribed from third-party sites |
| `data/manifest.json` | Quarters built, SEC source files with SHA-256, row counts, cover-page reconciliation |
| `data/dashboard/history.json` | Tier flows for the 13F Flow Monitor dashboard |

Run manually: Actions > build-13f > Run workflow (set `start` to `2013Q2` for the one-off backfill).
Values are in US dollars (pre-2023 filings, reported in $000s, are rescaled).
