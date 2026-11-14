# Decision investigation

The participant can ask why a complete shopping basket does not fit and inspect the smallest permitted changes. The deterministic engine works without credentials. Optional LangChain tool use chooses which computed tradeoffs to highlight and which consequential question to ask; the model does not perform money arithmetic or modify saved choices.

## Request and response

`POST /api/v1/sessions/{session_id}/investigate` requires the session's bearer token and a saved initial choice. Example:

```json
{
  "request_id": "95568d08-b8e9-435c-91b3-dd5dc1ccba08",
  "message": "Why doesn't this plan work? I would rather spend a little more than walk longer.",
  "protected_constraints": ["max_stores"],
  "allowed_travel_modes": ["walk"]
}
```

Optional `constraints` is the complete four-field constraints object; optional `assignments` assigns every basket product to an existing store. If omitted, both come from the latest saved choice. `protected_constraints` uses `budget_cents`, `time_budget_minutes`, `max_stores`, and `travel_mode`. If allowed modes are omitted, only the current mode is available. An explicit empty list means no travel mode is currently available. The participant may explicitly permit walking and driving; a wallet event can still remove unavailable driving.

`message` is optional, nonblank, and at most 1,500 characters. It steers optional model presentation. Offline operation uses the explicit controls; it does not pretend to understand arbitrary prose. Wallet amounts, provider settings, and arbitrary tools cannot be supplied in the request.

The response includes:

- `mode`: `deterministic`, `langchain`, or `fallback`.
- `status`: `feasible`, `changes_available`, `no_feasible_plan`, or `needs_review`.
- `selected_plan`, `feasible_plans`, `minimum_requirements`, and evidence with stable IDs.
- Up to eight `alternatives`. Each states its complete `changes`, resulting `constraints`, evaluated `plan`, exact `required_minutes`, changes relative to current limits in `delta`, and supporting `evidence_ids`.
- `question` and verified `highlighted_alternative_ids`; the full calculated evidence remains available regardless of model output.
- `investigation_id`, `request_id`, `created_at`, `anchor_choice_id`, and `stale`.

Time apply values round upward to whole minutes and are evaluated again. Evidence retains the exact modeled minimum. Money stays in integer cents. One-limit fixes hold every other limit constant. Combined fixes state every changed limit. Protected limits, the fixed basket and quantities, explicit available modes, and the app bounds always remain enforced. If another basket assignment already fits, the response recommends considering that assignment without unnecessary limit changes.

The UI should display required changes before applying them to draft controls, then let the participant explicitly save a revised choice. The investigation endpoint never applies a proposal or records a choice. When `stale` is true, a newer choice was saved while the report was being calculated; request a fresh investigation before applying proposals. On later replays, compare `anchor_choice_id` with the latest saved choice as well.

## Wallet context

`financial_summary(session['scenario'])` reconstructs the wallet projection from the frozen `financial_context` snapshot and projection inputs. Cached projection claims are not trusted. The wallet's `spendable_now_cents` is an independent cap already net of the required reserve and dated obligations. Future expected income does not fund a trip now, and the reserve is not deducted twice. Unknown or invalid financial context produces `needs_review` with no affordability claim or apply proposal. No financial context means ordinary shopping limits only.

The model sees a small normalized summary and calculated report, not raw account IDs, bank transactions, credentials, or the full scenario address.

## Retries and concurrency

Each session can create at most 100 investigation request records. Authentication happens before looking up any stored result. The same UUID and request body replay the exact saved report, including its original timestamp and stale-at-completion flag. A different body with the same UUID returns HTTP 409. Body hashing and replay happen before recomputing the current choice or calling a provider. Reports survive process restarts.

A pending duplicate returns HTTP 409. An abandoned request lease expires after 60 seconds and can be retried. A new report always records the saved choice used as its anchor. Invalid or interrupted requests may retain a retry record and count toward the session cap; no saved choices change.

The service reuses the chat worker gate: at most two active workers and no waiting queue. The response deadline defaults to 12 seconds and is bounded to 30 seconds. Timeout does not cancel or release an underlying native call; its slot remains occupied until the call finishes. Busy, timed-out, unavailable, or invalid model output returns deterministic evidence with `mode: fallback` and is persisted as the retry result.

## Optional LangChain behavior

`LAB_AI_ENABLED=false` is the default. The integration reuses `LAB_AI_PROVIDER` (`gemini` or `nvidia`), `LAB_AI_MODEL`, and the corresponding configured provider key. Environment files are not loaded by the module. Provider construction occurs only inside a bounded worker, after authenticated user action. Tracing is disabled.

The agent has exactly three read-only tools:

1. `inspect_current_situation`: current limits, normalized wallet constraints, evidence, and possible questions.
2. `test_allowed_change`: already evaluated budget, time, stop, transport, or combined proposals.
3. `compare_plans`: compare up to three known proposal IDs.

It has no filesystem, shell, web, bank-write, or choice-submission tool. The harness permits at most four model calls and six tool calls. Structured output contains only evidence IDs, alternative IDs, and an available question ID. Every ID is checked against the canonical calculated report. Arbitrary generated prose and replacement monetary facts are never published. The API repeats this validation even for injected callbacks and supplies those callbacks with defensive copies.

`register_investigation_routes(app, store, *, interpreter=None, timeout_seconds=12.0)` installs the route. An injected synchronous callback receives `{report, question}` and returns only `{evidence_ids, alternative_ids, question_id}`. The application lifecycle must call `app.state.lab_investigation.close()`. `create_app` exposes `investigation_interpreter` and `investigation_timeout_seconds` for offline tests.

## Verification

Use the credential-free Docker workflow:

```powershell
docker compose --env-file .env.example --profile tools run --build --rm backend-tests /app/.venv/bin/pytest -q -p no:cacheprovider tests/test_lab_investigator.py tests/test_lab_investigation_api.py
```

Pure-engine tests compare minima against an independent exhaustive assignment oracle for walking and driving. API tests cover session isolation, exact replay after a newer choice and restart, concurrent requests, stale reports, protected limits, wallet caps, malicious callback output, worker saturation, and the 100-request cap. Scripted models exercise actual LangChain tool calls without live providers.
