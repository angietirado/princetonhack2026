# Feasibility audit

The audit answers two reproducible questions about recorded initial choices:

- Did another complete-basket plan fit the same modeled constraints?
- Would an explicitly stated increase in money and/or time make a previously impossible basket feasible?

It does not infer participants' motives or estimate what people would actually purchase after an intervention. It uses the existing deterministic shopping and financial engines; no model or provider is involved.

## Endpoint

`GET /api/v1/analytics/audit` uses the application's existing analyst authorization dependency. The registrar is `register_audit_routes(app, store, require_admin)`.

Fixed query parameters:

| Parameter | Default | Bounds |
| --- | --- | --- |
| `scenario_id` | All scenarios | At most 80 characters; empty also means all |
| `data_mode` | `participants` | `participants`, `demo`, `all` |
| `budget_delta_cents` | `0` | Integer 0–10000 |
| `time_delta_minutes` | `0` | Integer 0–120 |

When both deltas are positive, they form one combined probe. The probe changes only the initial money and time limits, retaining the same fixed basket and quantities, travel mode, store limit, and verified wallet cash. Budget and time remain bounded by the app's maximum values; `probe_constraints` and `probe_clipped_by_app_bounds` disclose the effective settings. No saved choice, session constraint, wallet snapshot, or investigation report is modified.

## Selection and grouping

The audit selects at most 200 matching sessions, ordered by `created_at` descending and session ID ascending. This is a recent convenience sample, not a random sample. Total count, selected rows, and initial-choice reads share a database snapshot. Computation occurs after the read transaction closes.

Only the earliest recorded initial choice per selected session counts. Revisions and replayed choices do not add observations. Sessions lacking an initial choice are skipped. Malformed initial records are counted separately rather than silently entering a denominator. Arithmetic and feasibility are recomputed from assignments and immutable scenario data instead of trusting stored calculation fields.

A cohort key contains the exact frozen scenario hash, canonical initial constraints, and participant/demo status. This keeps source versions, world prices, baskets, travel assumptions, homes, wallet snapshots and projection inputs, travel modes, and stated limits separate. The report never pools savings across unlike cohorts.

## Response

Top-level fields:

- `data_mode`, `scenario_id`, and `probe` (`budget_delta_cents`, `time_delta_minutes`, `combined`).
- `total_sessions`, `selected_sessions`, `analyzed_sessions`.
- `skipped_no_initial_sessions`, `skipped_invalid_initial_sessions`.
- `truncated`, `max_sessions` (200), `cohorts`, and explanatory `notices`.

`selected_sessions = analyzed_sessions + skipped_no_initial_sessions + skipped_invalid_initial_sessions`. A truncated response includes the full matching session count, while all cohort metrics use only analyzed selected records.

Each cohort includes `id`, `scenario_id`, `scenario_name`, `scenario_version`, `scenario_hash`, `world_version`, `is_demo`, `initial_constraints`, `probe_constraints`, and `probe_clipped_by_app_bounds`. Its optional `financial_context` contains only `profile_name`, `source`, `status`, and verified `spendable_now_cents`; absent financial context is `null`. The display label comes from the frozen server-selected profile, and financial values come from recomputation. Raw wallet, account, and customer IDs are omitted.

`counts` includes:

| Count | Meaning |
| --- | --- |
| `initial_choices` | Valid analyzed initial choices in this cohort |
| `selected_plan_fits` | The recorded selection fits all shopping and resource limits |
| `other_plan_fits` | The recorded selection fails, but another complete basket fits |
| `no_plan_fits` | No complete basket fits the original shopping and resource limits |
| `needs_review` | Wallet uncertainty prevents a confident feasibility claim |
| `newly_feasible_under_probe` | A baseline no-plan-fits record gains a feasible basket under the displayed probe |
| `modeled_dominated` | An initially feasible selection has a feasible alternative costing no more and taking no longer, with at least one strict improvement |

The first four feasibility categories partition `initial_choices`. Dominance does not label an infeasible selection or a cheaper-but-slower alternative as dominated. Its matched cost/time query avoids relying on a sampled set of Pareto points. Zero-cost and zero-duration plans remain valid edge cases.

`denominators` explicitly names `classified_initial_choices` (all valid initial records, including review cases), `probe_eligible_initial_choices` (excluding review cases), `baseline_no_feasible_initial_choices`, and `dominance_eligible_initial_choices` (initially feasible selected plans). Use the baseline no-feasible denominator for the newly-feasible rate, and the dominance denominator for the dominated rate. Unknown wallet availability never becomes zero cash; a known zero-cash wallet still permits a genuinely free basket.

`example_ids` contains at most three session IDs for each outcome. Examples always belong to their cohort and can be opened through the existing authenticated analytics session detail route. They are selected in the same recent-session order, not as a representative sample.

## UI use and future AI

A compact interface needs three actions: choose a cohort, set a money/time probe, and inspect a recorded example next to calculated alternatives. Label probes as analyst comparison rules, not participants' observed preferences. Display source version and wallet context so similar-looking cohorts remain distinguishable.

A later optional agent could select these typed filters and probes and explain verified counts with evidence references. It would not need arbitrary SQL, execution tools, invented motives, or a new library for the deterministic foundation.

## Verification

Use Docker with the public example environment:

```powershell
docker compose --env-file .env.example --profile tools run --build --rm backend-tests /app/.venv/bin/pytest -q -p no:cacheprovider tests/test_lab_audit.py
```

Tests cover exact probe thresholds, matched dominance, zero and unknown wallets, zero-cost/time plans, mode/version/wallet/demo segregation, ignored revisions and retries, incomplete records, recomputed arithmetic, the stable 200-session limit, bounded cohort examples, unchanged source records, and analyst authorization.
