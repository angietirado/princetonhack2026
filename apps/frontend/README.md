# Frontend handoff

This directory retains a minimal React + TypeScript + Vite source scaffold. The default Docker frontend serves the connected Ledgerly dashboard from the repository's root `index.html`, `app.js`, and `styles.css`. Its charts, balances, tables, and reports load FastAPI's unified dashboard response.

## Run with Docker

From the repository root:

```sh
docker compose --env-file .env.example up --build --detach --wait
```

Open Ledgerly at `http://127.0.0.1:5173`. Compose creates a missing fixture, starts FastAPI, and serves the root dashboard files through nginx. The dashboard uses the same-origin `/api` proxy. This command uses `.env.example` for offline fixture data. Start with `--env-file .env` for the configured Nessie customer and unlock with your existing app token.

Run the same command after source changes to rebuild the images. The frontend image serves static files without live reload. See the [root README](../../README.md) for logs and shutdown commands. Run the dashboard regression tests in Docker:

```sh
docker compose --env-file .env.example --profile tools run --build --rm dashboard-tests
```

## Dashboard API contract

Use `http://127.0.0.1:8765/docs` or `/openapi.json` for the authoritative schema. The React scaffold's typed client is `src/api.ts`; it is not currently used by the root dashboard.

`category_mapping` has version `source-categories-v1` and one entry for every exact raw category in the returned transactions. Each entry supplies `raw_category`, broad `category_id`/`category_label`, nullable `subcategory_id`/`subcategory_label`, and `origin` (`source_mapping` or `unclassified`). The dashboard validates complete, unique mapping entries before rendering and indexes raw keys with a `Map`. Unknown source categories are explicit Unclassified entries. No merchant-name inference or banking-data mutation is involved.

List filters operate on the already loaded date-scoped rows. USD bounds parse to exact safe integer cents, and CSV exports all matching pages with raw labels and mapping provenance. Invalid bounds disable export. A date refresh preserves unavailable selections as zero-match filters; lock/error clearing removes values and private options. The summary and comparison APIs continue using original source categories.

| Route | Returns |
| --- | --- |
| `/api/v1/dashboard` | One scoped snapshot: accounts, cash/card balances, summary, gross expenses, refunds, expense categories, monthly net spending, transactions, versioned source category mapping, recurring candidates, source, customer ID, and retrieval time |
| `/api/v1/accounts` | Array of `{ id, customer_id, nickname, type, balance_cents }` |
| `/api/v1/transactions` | Array with account, merchant/category, amount, kind, status, and timestamp |
| `/api/v1/spending/summary` | `{ source, currency, start, end, net_spending_cents, included_count, excluded_count, categories }` |
| `/api/v1/spending/recurring` | Array of recurring candidates with evidence transaction IDs |
| `POST /api/v1/spending/compare` | Deterministic comparison of two explicit UTC periods, with source-bound findings and bounded evidence |
| `GET /api/v1/agent/status` | Availability, selected provider/model, and enabled catalog capability |
| `POST /api/v1/agent/answer` | Independent question result: status, answer, bounded tool evidence, and limitations |
| `POST /api/v1/costar/search` | Direct local catalog results for the apartment form's explicit criteria |

The dashboard and spending/transaction routes accept optional `start` and `end` in `YYYY-MM-DD` format. Dates are inclusive and use UTC. Invalid ranges return `422`; upstream failure returns `502`; a missing fixture returns `503`. Categories are `{ category, amount_cents }`, sorted by amount descending. Empty windows return zero totals and empty categories. Net amounts may be negative after refunds. Account balances are current, independent of the selected transaction period.

Format cents with `Intl.NumberFormat`, dividing by 100 at display time. Preserve `source` visibly so fixture data is identified as synthetic. All requests are scoped by the backend; there is no client-supplied customer selector in this single-user scaffold.

**Insights → Compare two periods** works independently of AI. The date shortcut only fills adjacent equal-duration periods; it never submits. The form validates exact returned periods, durations, safe integer changes, and omitted-group reconciliation before displaying results. Zero/negative earlier totals retain undefined percentages. Individual evidence is bounded, while all qualifying records contribute to aggregates. Lock clears the inputs and evidence; refresh/cancellation invalidates pending results. Completion does not move focus away from another view.

**Ask your books** checks availability when opened and submits only an explicit question. It renders model output and exact tool evidence as text. The question body accepts only `question`; include dates and filters in that text because dashboard date controls and earlier questions are not inherited. Date controls appear on spending views, not Ask or account settings. A clarification lists readable input labels and offers **Edit this question**, which restores the original question without submitting it. Each edited question is a fresh request. Source context and evidence stay visible; provider/model, call counts, reserved output tokens (not billed usage), and the source revision appear under **Technical details**.

**Stop waiting** aborts the browser read, retains the question, and rejects a late response. It does not claim to cancel backend processing or send a retry. Locking or refreshing aborts pending browser reads and clears the question, answer, and evidence. Backend work may finish after a browser disconnects. Enable the optional assistant using the [integration setup](../../docs/INTEGRATIONS.md#ai-agents).

The apartment-filter form appears only when the catalog capability is enabled; otherwise a compact availability message remains. It is independent of AI availability and sends a complete criteria object to the catalog endpoint. It checks the returned criteria against the immutable submitted values, then shows up to three results with full rent ranges. Its rent control filters starting rent, not the upper endpoint or total housing costs. Search and question operations share busy-state guards; new result contexts, cancellation, locks, refreshes, and token changes invalidate old responses. The dashboard does not imply a live catalog or a connected Photon messaging service.

Fixture mode requires no authentication unless `APP_API_TOKEN` is set. The dashboard prompts for this token on a `401`, holds it only in page memory, and sends a bearer header. Lock/reload clears it; no token is put in localStorage, sessionStorage, or bundled code. Production user enrollment remains a later decision. Never place a Nessie or Photon key in a `VITE_` variable.

## Suggested next UI work

- Build on the existing date filters, category/monthly charts, transaction search/status filters, pagination, and CSV export.
- Add verified invoice/context evidence when backend support exists.
- Phone enrollment stays deferred until the MVP is complete.
- Expand the existing question/evidence view when new verified tools become available.

Coordinate API changes in [the design doc](../../docs/DESIGN.md) before changing response fields. No UI framework or charting library has been chosen.

## Optional work on the React scaffold

The [optional native setup](../../docs/LOCAL_DEVELOPMENT.md) is available for developing the React source with Vite. It is separate from the Docker-served Ledgerly dashboard and is not required to run or test that dashboard.
