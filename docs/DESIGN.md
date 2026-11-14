# Spending Companion design and decisions

Status: initial scaffold and MVP plan. Updated November 13, 2026.

Build a phone-first spending companion: a user texts a question, agents inspect that user's Nessie v2 transactions, and Photon 2.0 carries a concise, evidence-backed answer back to their phone. A small web app shows the same spending information. The hackathon demo should make a spending change understandable and suggest an action the user can choose to take.

## Product idea

The core interaction is a question such as “Why did I spend more this month?” or “Which subscriptions am I paying for?” The answer should name the period, give exact totals, identify the main contributors, and distinguish an observed pattern from a guess. A useful follow-up could narrow the question to dining or show the transactions behind a recurring-charge candidate.

The first demo uses synthetic customers and transactions. Success means one enrolled user can ask from their phone, receive an answer grounded in the same numbers shown on the web page, and ask one contextual follow-up.

## Decisions

| Area | Decision | Status |
| --- | --- | --- |
| Backend | Python 3.12 and FastAPI; typed request and response models | Set up |
| Banking data | Capital One Nessie v2 through a backend-only adapter | Adapter ready; credentials needed |
| Project split | Application under `apps/`; independent package under `data-generator/` | Set up |
| Frontend | React, TypeScript, Vite; leave product UI to the frontend teammate | Minimal page ready |
| Agents | Provider-neutral tools with deterministic calculations; choose model runtime later | Tools ready; orchestration planned |
| Phone channel | Photon 2.0 Beta, with SMS as the intended demo channel | Boundary ready; provisioning and worker planned |
| Local development | Fixture mode by default; no external service needed | Working |
| Message persistence | SQLite durable inbox for the single-process demo | Receiver ready; consumer planned |
| Deployment | Local development first; choose hosting when the phone flow is ready | Open |

Nessie v2 uses `https://v2-prod-api.nessieisreal.com`, bearer authentication, integer-cent money fields, and cursor pagination. The adapter follows its [official OpenAPI specification](https://v2.nessieisreal.com/openapi.yaml). Photon 2.0 uses the `https://api.photon.codes/v1` API and its Beta documentation. [Photon version guide](https://photon.codes/docs/llms.txt)

## Architecture

```mermaid
flowchart LR
    Phone[User phone] <--> Photon[Photon 2.0]
    Photon --> Receiver[FastAPI verified webhook]
    Receiver --> Inbox[(SQLite inbox)]
    Inbox -. planned .-> Worker[Agent worker]
    Worker -. reply transport .-> Photon
    Worker -. calls .-> Tools[Scoped spending tools]
    React[Minimal React app] --> API[FastAPI read endpoints]
    API --> Tools
    Tools --> Adapter[Data adapter]
    Adapter --> Nessie[Nessie v2]
    Adapter --> Fixtures[Local fixture JSON]
    Generator[Separate data generator] --> Fixtures
    Generator -->|explicit apply| Simulation[Nessie historical simulator]
    Simulation --> Nessie
```

The app never imports or runs the generator. Both agree on the fixture boundary: accounts, merchants, and transactions shaped like the portions of Nessie v2 the app reads. `schema_version: 1` identifies the local fixture format. The shared Python workspace simplifies development; each package has its own manifest and can be installed separately.

The backend is the sole boundary for provider keys and banking data. The browser calls only FastAPI. The initial server is bound to loopback and serves one configured customer; multi-user authentication is a later milestone.

## Request flow

1. An enrolled user sends a direct text to the project's Photon number.
2. FastAPI verifies the raw request body with the destination's Standard Webhooks secret. It accepts supported text messages only from the configured human sender to the configured agent.
3. The receiver atomically stores the event, message ID, and queued status in SQLite, then acknowledges receipt. Duplicate events or messages do not create another job.
4. **Planned:** a worker claims the queued message, resolves the user's customer and conversation context, and invokes the agent coordinator.
5. **Planned:** the agent chooses a read-only tool and validates its arguments. The tool calculates from the customer's scoped snapshot. A reply writer turns those results into a short answer.
6. **Planned:** the worker persists the exact reply and its stable idempotency key, calls the existing Photon send primitive, then records acceptance and delivery updates separately.

The receiver is implemented through step 3. `send_reply()` is available for the future worker but no route invokes it automatically. A Photon API acceptance is not a delivery confirmation. [Photon webhook contract](https://photon.codes/docs/beta/message-delivery/webhook)

## Agent responsibilities and tools

Start with one coordinator and one bounded tool loop. “Analyst” and “reply writer” are logical responsibilities; they do not require independent services or separate model calls for the first demo.

| Tool | Input | Result | Current behavior |
| --- | --- | --- | --- |
| `get_transactions` | Optional inclusive start/end dates | Normalized transactions | Implemented |
| `summarize_spending` | Optional inclusive start/end dates | Net spending and category totals | Implemented |
| `find_recurring_charges` | Optional inclusive start/end dates | Monthly recurring candidates with evidence transaction IDs | Implemented |
| Compare periods | Two explicit date ranges | Total and category changes | Planned; reuse summary calculations |
| Explain unusual spending | Date range and comparison window | Observations with evidence and confidence | Planned |

Bind `SpendingTools` to an authorized snapshot before exposing methods to a model. Customer and account scope must come from server context, never from an LLM-provided user ID. The runtime must validate tool arguments, cap execution time and tool rounds, and treat merchant descriptions and message text as data rather than instructions. The application exposes no banking write tool.

Keep arithmetic in Python. Model text should quote tool results and identify the date range. If data or a tool fails, say that the analysis is unavailable instead of inventing a total. There is no LLM dependency or model selected in the scaffold yet.

## Money and data semantics

- Store and calculate USD amounts as integer cents; format dollars only at the UI or reply boundary.
- Net spending includes completed purchases, bill payments, fees, and chargebacks, minus completed refunds. Transfers, deposits, interest credits, and cash withdrawals are excluded. This avoids counting a card payment or account transfer as another purchase.
- Pending, declined, and cancelled transactions do not contribute to completed spending.
- Dates are inclusive UTC dates derived from Nessie's `created_at`. Settlement-time reporting and the user's timezone are future decisions.
- A refund inherits its original merchant/category when its parent transaction is available. Otherwise it remains uncategorized. A refund may make a category or date window negative.
- Recurring candidates require at least three completed purchases with the same account, merchant ID, and amount, spaced 25–35 days apart. The label is a candidate, not proof of a subscription. Variable-price subscriptions and annual charges are outside this first detector.
- Read all paginated results before computing totals. Sort transactions by timestamp rather than provider write order. Exceeding the demo page cap fails the request instead of returning partial totals.
- Local fixture balances are illustrative, not a reconciled ledger. The current app analyzes spending and does not infer affordability from those balances.

The app reads customer accounts, merchants, and the tenant transaction list, then scopes the normalized result to the configured customer's account IDs. For larger datasets, add a customer-scoped cache or materialized read model so each request does not traverse the tenant history.

## API and frontend handoff

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Process health and configured data source |
| `GET /api/v1/accounts` | Configured customer's accounts |
| `GET /api/v1/transactions?start=YYYY-MM-DD&end=YYYY-MM-DD` | Scoped transaction evidence |
| `GET /api/v1/spending/summary?start=...&end=...` | Spending totals and categories |
| `GET /api/v1/spending/recurring?start=...&end=...` | Recurring-charge candidates |
| `POST /webhooks/photon` | Signed provider events; durable receipt only |
| `GET /docs` and `GET /openapi.json` | Executable API contract |

The React starter has one summary view, loading and failure states, and a refresh button. It contains no provider keys. It leaves navigation, charts, conversation UX, visual design, and authentication flows to the frontend teammate. [Frontend handoff](../apps/frontend/README.md)

## Data generator

The generator supports two independent targets:

- **Local:** deterministic fixtures from a seed, end date, and day count. Scenarios include baseline spending and a recent dining increase. The data includes monthly subscriptions, deposits, a transfer, a refund, and pending/declined purchases to exercise exclusions.
- **Nessie:** a dry-run request by default; `--apply` starts Nessie's native historical simulation and saves its run manifest. The manifest contains customer/account IDs for configuring the app. The service generates dates relative to the run time, so a seed alone does not fix calendar dates. Repeating a request may create another dataset. [Nessie simulation schema](https://v2.nessieisreal.com/openapi.yaml)

The generator owns sandbox creation. The application owns read-only analysis. The runtime does not create customers or transactions as a side effect of a request.

## Implementation milestones

| Milestone | Deliverable | Completion check |
| --- | --- | --- |
| 0 — Scaffold | Design, separate packages, local fixture, API, minimal React, automated checks | Complete: local API tests and frontend build |
| 1 — Nessie demo | Generate sandbox history and bind one customer | Account-scoped live totals match an independently checked sample |
| 2 — Agent analysis | Select model/runtime; tool loop; period comparison; evidence-bearing replies | Fixed question set produces correct figures and handles missing data |
| 3 — Phone loop | Provision SMS, connect receiver, implement persistent worker/outbox | A phone question receives one reply; duplicate delivery and restart do not send twice |
| 4 — Demo polish | Frontend teammate's UI; follow-up context; a short demo script | Phone and web show matching totals for the same customer and dates |

The offline automated checks cover exact money arithmetic, exclusions/refunds, customer scope, monthly candidates, pagination, API authorization/date validation, webhook signatures/replay age, duplicate persistence, sender filtering, and outbound idempotency headers. Provider behavior still needs credentialed smoke tests.

## Next decisions and backlog

- [ ] Choose the AI provider, model, and tool-calling runtime; set a per-question latency and cost budget.
- [ ] Choose the demo persona and final app name.
- [ ] Confirm a provisioned Photon **SMS** number. Shared iMessage service is a separate channel and must be presented as such if used for a fallback demo.
- [ ] Add the inbox worker, persisted reply/outbox, retry/backoff, and delivery-status handling.
- [ ] Add one conversation of bounded history per enrolled sender, plus opt-out handling before proactive messages.
- [ ] Replace the shared demo bearer token and single-customer settings with real user authentication and verified phone-to-customer enrollment before opening access.
- [ ] Add user timezone handling, caching, provider rate-limit backoff, and data freshness timestamps.
- [ ] Choose deployment for the FastAPI process and persistent database; the webhook requires public HTTPS.

Stretch ideas: weekly spending digests, category budgets, richer recurring-charge detection, unusual-spend comparisons, and a dashboard link from a reply. Keep money movement, automatic subscription cancellation, and unrelated banking operations outside the hackathon MVP.

## Decision log

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-11-13 | FastAPI backend, React frontend, separate generator | Team's chosen stack and division of work |
| 2026-11-13 | Keep local fixture mode and provider adapters | Let backend and frontend development proceed without credentials |
| 2026-11-13 | Use Nessie v2 historical simulations for sandbox seeding | Reuse the provider's supported generator and preserve its returned IDs |
| 2026-11-13 | Leave the LLM provider undecided; implement tools first | Stabilize calculations and scope before choosing the agent runtime |
| 2026-11-13 | Persist verified Photon events before acknowledgment | Make receiver retries safe and leave a clear boundary for the worker |
