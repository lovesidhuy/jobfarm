# Production farm proof (redacted)

Generated: `2026-08-12T02:45:13.861206+00:00`

Titles/companies preserved; personal identifiers, emails, profile IDs, and full query strings removed.

Source: live `jobbots.application_queue` on the production worker. Personal emails, account IDs, resume paths, and query-string job URLs are omitted. Each row has a opaque `proof_id` hash for audit without exposing internal keys.

## Totals

### Last 24h

- **Applied (new submits):** 62
- Queue outcomes in window: `{'applied': 62, 'bookmarked': 30, 'dead': 26, 'queued': 21, 'already_applied': 20, 'skipped': 17, 'leased': 3}`
- Applied by portal:
  - `workopolis`: **27**
  - `indeed`: **22**
  - `linkedin`: **8**
  - `greenhouse`: **4**
  - `google`: **1**

### Last 7d

- **Applied (new submits):** 121
- Queue outcomes in window: `{'already_applied': 140, 'applied': 121, 'skipped': 50, 'dead': 38, 'bookmarked': 31, 'queued': 21, 'leased': 3}`
- Applied by portal:
  - `indeed`: **45**
  - `workopolis`: **41**
  - `linkedin`: **18**
  - `google`: **9**
  - `greenhouse`: **5**
  - `bamboohr`: **3**

### Last 30d

- **Applied (new submits):** 121
- Queue outcomes in window: `{'already_applied': 140, 'applied': 121, 'skipped': 50, 'dead': 38, 'bookmarked': 31, 'queued': 21, 'leased': 3}`
- Applied by portal:
  - `indeed`: **45**
  - `workopolis`: **41**
  - `linkedin`: **18**
  - `google`: **9**
  - `greenhouse`: **5**
  - `bamboohr`: **3**

## Daily applied (14 days)

| Day (UTC) | Indeed | LinkedIn | Workopolis | Other | Total |
|---|---:|---:|---:|---:|---:|
| 2026-08-09 | 5 | 0 | 0 | 2 | 7 |
| 2026-08-10 | 15 | 10 | 9 | 10 | 44 |
| 2026-08-11 | 12 | 3 | 29 | 5 | 49 |
| 2026-08-12 | 13 | 5 | 3 | 0 | 21 |

## Charts & logs

- [`apply-log-excerpts.txt`](./apply-log-excerpts.txt) — journal lines (`Application submitted!` / LinkedIn success UI)

## Sample ledger

See [`applies-sample.csv`](./applies-sample.csv) — up to 60 most recent successful applies (7d).

## How to verify independently

1. Run the farm against your own accounts with `DRY_RUN=0`.
2. Compare confirmation emails from Indeed/LinkedIn/Workopolis to `proof_id` timestamps.
3. Replay unit/integration tests in CI for flow correctness (separate from live proof).
