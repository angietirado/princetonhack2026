# Controlled home-location comparison

This read-only report isolates how a different starting home changes one simulated shopping trip. It preserves the frozen basket, quantities, stores, prices, travel rules, wallet snapshot/projection, and explicit shopping limits. Only the home object is substituted. It does not recommend moving, infer income, calculate monthly housing affordability, create a new session, or change a saved choice.

## Endpoint and request

`POST /api/v1/sessions/{session_id}/locations` requires the session bearer token and a saved initial choice. The body can be `{}` or contain:

```json
{
  "constraints": {
    "budget_cents": 6500,
    "time_budget_minutes": 45,
    "travel_mode": "drive",
    "max_stores": 2
  },
  "assignments": {
    "milk": "store_id"
  }
}
```

The assignment example is abbreviated: a real request must assign every basket item exactly once to a known store. `constraints`, when provided, is the complete four-field object. Omitted constraints and assignments come from the latest saved choice. Clients cannot provide candidate homes, prices, wallet cash, or arbitrary comparison tools. Invalid fields/settings return 422; missing initial choice returns 409; session authentication uses the existing store's 401/404 behavior.

The registrar is `register_location_routes(app, store, scenarios)`, where `scenarios` is the server's validated scenario mapping. The pure function is `compare_locations(scenario, constraints, assignments, candidate_scenarios)`. It accepts a scenario mapping or list with at most 100 records and performs no I/O.

## Compatibility and current-home identity

A candidate requires equal, nonblank `world_id` and `world_version`, plus exact canonical equality of the actual `basket`, `stores`, and `travel` objects. This checks quantities, prices, store coordinates, shopping time, and transport assumptions independently of declared version labels. Object key order is irrelevant; array order remains part of the frozen world representation.

Legacy snapshots lacking world identifiers are unsupported. An older frozen world is never silently mapped to a newer catalog. Invalid candidates and candidates with conflicting content are skipped with explicit counts.

The baseline is always the session's frozen home. A reused current scenario ID cannot replace it. Physical identity uses matching source property, record, or listing IDs and known unit numbers; a refreshed listing for the same unit is not presented as a move. Exact coordinates/address provide a conservative fallback when source identity is absent. Distinct known units can be compared even within one building. Duplicate physical homes are removed before selecting at most three other homes in stable scenario-ID/home-hash order.

Candidate defaults, wallets, and other scenario metadata are never adopted. Calculation uses a deep copy of the frozen baseline with only `home` replaced. This means a candidate's catalog scenario hash and its controlled comparison scenario hash can intentionally differ.

## Response contract

The top-level report contains:

- `status`: `ok`, `no_compatible_homes`, `unsupported_world`, or `needs_review`.
- `anchor_choice_id` and `stale` from the endpoint. `stale` means a newer choice was saved during computation. The pure report omits these session fields.
- `constraints`, `assignments`, and `world` with `id`, `version`, `content_hash`, and `supported`.
- `wallet_summary`, a small verified summary or `null` when no financial context exists.
- `baseline`, the separate frozen current home entry.
- `locations`, only distinct other homes, maximum three; it never includes the baseline.
- `catalog_summary` and plain-language `assumptions`.

`catalog_summary` contains `supplied_count`, `compatible_distinct_home_count`, `returned_home_count`, `max_other_homes`, `truncated`, and `skipped` counts for `invalid`, `world_mismatch`, `content_mismatch`, `current_home`, and `duplicate_home`.

Each home entry contains:

| Field | Meaning |
| --- | --- |
| `id` | `current` for baseline; stable home-hash identifier for candidates |
| `scenario_id`, `scenario_version` | Source catalog identity; baseline uses frozen session identity |
| `home_hash`, `comparison_scenario_hash` | Exact home and calculated scenario lineage |
| `is_current` | True only for the frozen baseline |
| `home` | Complete validated home object, retaining source dates, unit, coordinates, bedrooms, size, and other supplied context |
| `same_assignment` | Evaluation of the exact same product-to-store assignment at this home, plus signed deltas against baseline |
| `optimized` | Separately calculated feasible plans under the same basket, wallet, and shopping limits |
| `rent` | Historical monthly asking-rent context; never combined with shopping totals |
| `provenance` | Catalog and frozen-baseline hashes, home source, calculation rule, and unverified current availability |

`same_assignment.evaluation` contains the core engine evaluation: basket/transport/total cents, travel/shopping/total minutes, distance, route, store IDs, assignments, constraints, feasible flag, and violations. `delta` contains signed `basket_cost_cents` (always zero), `transport_cost_cents`, `total_cost_cents`, and `total_minutes`. Baseline deltas are zero. Modeled cost/time deltas remain available even when a plan is infeasible; they are not an affordability claim.

`optimized` contains `status`, `plans`, `feasible_plan_count`, `evaluated_plan_count`, and `notice`. Each plan is a core evaluation with a label and a location-specific ID. These plans may assign items to different stores and must not be described as the same-assignment result. Wallet review or unavailable resources can leave the plan list empty. Limits are never relaxed.

`wallet_summary` contains verified `source`, `status`, `spendable_now_cents`, `reserve_cents`, `shopping_allowance_cents`, `shortfall_cents`, and `allowed_travel_modes`. Current spendable cash is the cap; the reserve is already accounted for, and future income cannot fund today's trip. Raw wallet/customer/account IDs are not included. Invalid wallet context produces `needs_review` and no feasible optimized plans.

## Historical rent and other unknowns

`rent.recorded_monthly_cents` is shown only when the home explicitly identifies `housing_price_basis: monthly_rent`, has integer `source_monetary_unit: 1` (USD in the supplied CoStar sample), and supplies a nonnegative integer `monthly_rent_cents`. Missing or unknown currency, sale prices, unlabeled housing prices, and malformed amounts remain `null`; no conversion or inference is attempted.

`rent.delta_monthly_cents` is candidate minus baseline rent only when both are known. Missing baseline rent is not treated as zero. `currency` is `USD` only for a known rent; otherwise it is `null`. `basis` is `historical_recorded_asking_rent` or `unknown`. `fees_monthly_cents` and `all_in_monthly_cents` remain `null` because this comparison does not establish them.

These monthly figures stay separate from per-trip shopping totals. Different sizes, bedroom counts, conditions, and unmodeled qualities prevent a like-for-like affordability conclusion. Listing dates are historical provenance; current availability is unverified regardless of other source metadata. The report's `provenance.current_availability_verified` is always false.

## UI boundary and verification

A compact read-only interface can show baseline and one candidate at a time, lead with the same-assignment cost/time difference, and place optimized plans in a separate disclosure. Show monthly rent as historical context on its own row. Capture the draft/settings used for the request and discard superseded responses; the server supplies the saved-choice anchor and stale-at-completion flag. There is no report persistence or request UUID requirement because the route records nothing.

Use the credential-free Docker workflow:

```powershell
docker compose --env-file .env.example --profile tools run --build --rm backend-tests /app/.venv/bin/pytest -q -p no:cacheprovider tests/test_lab_location.py
```

Tests cover fixed-world tampering despite matching version labels, frozen/legacy worlds, refreshed current-home identities, deduplication, three-home bounds, unchanged assignments versus optimization, hard wallet caps, unknown finances, trustworthy monthly USD metadata, missing baseline rent, malformed input, session authorization, initial-choice gating, latest-choice defaults, and stale-at-completion behavior.
