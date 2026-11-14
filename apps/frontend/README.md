# Frontend handoff

This is a minimal React + TypeScript + Vite app. Replace the UI freely while keeping provider secrets on FastAPI. The only screen currently loads a spending summary with loading, error, and refresh states.

```sh
npm ci
npm run dev
npm run build
```

Start FastAPI on `127.0.0.1:8765` using the root README. Vite proxies `/api` and `/health` to it. Production hosting will need the same reverse proxy or an explicitly configured API origin; a static build by itself does not start the backend.

## Contract

Use `http://127.0.0.1:8765/docs` or `/openapi.json` for the authoritative schema. The current typed client is `src/api.ts`.

| Route | Returns |
| --- | --- |
| `/api/v1/accounts` | Array of `{ id, customer_id, nickname, type, balance_cents }` |
| `/api/v1/transactions` | Array with account, merchant/category, amount, kind, status, and timestamp |
| `/api/v1/spending/summary` | `{ source, currency, start, end, net_spending_cents, included_count, excluded_count, categories }` |
| `/api/v1/spending/recurring` | Array of recurring candidates with evidence transaction IDs |

The three spending/transaction routes accept optional `start` and `end` in `YYYY-MM-DD` format. Dates are inclusive and use UTC. Invalid ranges return `422`; upstream failure returns `502`; a missing fixture returns `503`. Categories are `{ category, amount_cents }`, sorted by amount descending. Empty windows return zero totals and empty categories. Net amounts may be negative after refunds.

Format cents with `Intl.NumberFormat`, dividing by 100 at display time. Preserve `source` visibly so fixture data is identified as synthetic. All requests are scoped by the backend; there is no client-supplied customer selector in this single-user scaffold.

Fixture mode requires no authentication unless `APP_API_TOKEN` is set. Nessie mode requires a bearer token; this temporary gate is for backend smoke tests. The real login/enrollment flow and production API hosting remain team decisions. Never place a Nessie or Photon key in a `VITE_` variable.

## Suggested next UI work

- Period selection and category breakdown.
- Transaction evidence and recurring-charge review.
- Phone enrollment and connection status after the backend implements enrollment.
- Conversation view after a real agent endpoint exists.

Coordinate API changes in [the design doc](../../docs/DESIGN.md) before changing response fields. No UI framework or charting library has been chosen.
