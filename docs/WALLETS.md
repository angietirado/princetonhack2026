# Frozen wallets and financial context

Blockwise can attach a fictional wallet to a shopping session. The wallet adds an independent cash limit to the participant's money and time preferences. Choosing a larger shopping budget does not create more cash.

This milestone exposes **local fixture profiles only**. They are not live Nessie captures or real people's finances. There is no public bank-capture endpoint and no API accepts provider keys, a Nessie customer ID, an arbitrary wallet snapshot, or a client-supplied cash allowance.

## Fixture choices

All profiles begin at the fixed fictional scenario anchor, 2026-11-14 00:00 UTC. No expected income or reserve is assumed initially. The example bill is due two days after the anchor.

| Profile ID | Name | Posted checking balance | Holds already deducted | Available checking cash | Example bill | Shopping capacity now |
|---|---|---:|---:|---:|---:|---:|
| steady | Steady week | $420 | $20 | $400 | $180 | $220 |
| tight | Tight week | $155 | $15 | $140 | $110 | $30 |
| buffer | Building a buffer | $650 | $50 | $600 | $240 | $360 |

Each fixture also includes $1,700 of unused example card credit. That credit is excluded from shopping cash. The tight profile may make the required basket infeasible; the app must explain that result rather than inventing funds or dropping required items.

## API and frozen state

- GET /api/v1/wallets returns fixture summaries and an explicit fictional-data notice.
- POST /api/v1/sessions accepts an optional wallet_profile_id: steady, tight, buffer, or null. Omit it for the original shopping-only experience.
- The selected server-owned context is copied into scenario.financial_context before the session is saved. Existing sessions are not migrated.
- GET /api/v1/sessions/{id}/wallet requires the session Bearer token and returns the attached profile, verified projection summary, and source notice.
- Evaluate/save responses include wallet.summary and wallet.after_choice when a wallet is attached.
- Recommendations include wallet_summary and effective_spending_cap_cents. They retain the participant's stated constraints.

The context contains profile_id, profile_name, description, snapshot, projection_inputs, and a cached projection. The snapshot has a content fingerprint. The authoritative financial_summary(scenario) helper validates that fingerprint and recomputes the projection; it does not trust a cached allowance. A corrupt or unresolved context yields needs_review and no confident allowance.

For compatibility, session creation omits null optional fields from the idempotency hash. Retrying a pre-wallet request UUID must still recover its original session and token. Selecting a different wallet with that UUID is a conflict.

Scenario hashes include the frozen wallet context. When forming comparable participant cohorts, use the scenario hash and constraints, not scenario ID alone: one location can have several different wallet profiles.

## What the numbers mean

- baseline_available_cents is the sum of checking-account available balances, including negative balances. Holds are already deducted.
- scheduled_obligations_cents is the known unpaid and unheld remainder of checking-account bills due before the window ends. Unpaid overdue bills are reserved at capture.
- expected_income_cents includes only explicit, dated scenario inputs in the window. Historical deposits are never automatically classified as future income.
- shopping_allowance_cents is end-of-window cash after known bills, explicit income, local expenses, and reserve.
- spendable_now_cents is the minimum projected dated balance, less reserve, floored at zero. It is the appropriate cash cap for a shopping trip now; future income cannot finance a trip before it arrives.
- shortfall_cents measures the largest negative projected cash balance, even if later income makes the final balance positive.
- needs_review makes confident allowance, ending-balance, and shortfall fields null. known_events_ending_available_cents is explicitly partial and must not be presented as an affordable shopping budget.

Checking balances are pooled. This assumes funds can be redistributed between eligible checking accounts without fees or delays; it does not guarantee that every individual bill-paying account remains solvent. Savings/card-linked bills, loan schedules, and expenses absent from the recorded bills or explicit inputs are outside this checking-cash model.

The default window runs from the capture instant through midnight UTC after seven calendar dates, end exclusive. Date-only bill deadlines are interpreted as midnight UTC. At equal timestamps, outflows precede expected income. Recurring dates are not treated as a documented frequency: only the reported next bill occurrence is modeled.

## One decision, not accumulated revisions

wallet.after_choice adds exactly one local expense equal to the current plan's complete cost, including modeled transport. Re-evaluating or saving a revised choice does not spend money again. A second revision previews an alternative against the same frozen base.

No call changes a bank balance or the frozen snapshot. For a genuinely cumulative simulation later, use a separate, explicitly dated local decision ledger; do not sum alternative experimental revisions.

All shopping interfaces use the same independent boundaries:

1. Respect the user's stated money, time, and store limits.
2. Require the current trip cost to be within spendable_now_cents.
3. Require the selected travel mode to be available in the wallet scenario.
4. Block confident recommendations when the financial context needs review.

Over-budget or otherwise infeasible choices may still be saved as experimental observations. Their evaluation records the violation. Recommendations and chat proposals cannot bypass the wallet by increasing the user's preferred budget. A genuinely free complete basket remains feasible with zero available cash.

## Read-only Nessie adapter

lab_nessie.NessieReadClient can capture a pinned customer's sandbox context for later controlled import. It uses the fixed HTTPS v2 origin, Bearer authentication, GET requests only, bounded pages/records/response sizes, and deadlines. Accounts must belong to the pinned customer; per-account bill responses must match the requested account. Tenant-wide paginated transactions are filtered to verified owned accounts. Redirects are not followed, and capture errors never silently become fixture data.

The adapter reads accounts again after fetching bills and transactions and rejects changed balances. These HTTP reads are not an atomic bank snapshot; that limitation is retained in the snapshot assumptions.

The current public contract distinguishes:

- Account available_cents: balance less holds, or remaining credit for a credit card.
- Transaction amount_cents: integer cents.
- Bill payment_amount: dollars. Conversion uses decimal arithmetic; sub-cent values are rejected instead of guessing the server's rounding convention.

Linked one-off bill payments reduce only the remaining obligation, with held amounts reconciled against reported pending debits. Missing payment links, ambiguous recurring occurrences, reversals, unknown due dates, or inconsistent holds require review when they affect the projection. This prevents confidently counting a held bill payment twice.

Sources: [Nessie v2 OpenAPI](https://v2-prod-api.nessieisreal.com/openapi.json), reviewed 2026-11-14.

## Verification

Use the project's credential-free Docker workflow with --env-file .env.example. Regression coverage includes mocked Nessie reads, dollars/cents, credit and holds, time ordering, delayed income, missing bill links, immutable branches, wallet caps across interfaces, legacy request retries, and current-choice-only previews.

The adapter has not been verified against live credentials during this work. No private .env contents were read or loaded for verification.

