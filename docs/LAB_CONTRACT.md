# Shopping Lab implementation contract
Status: core integration contract, 2026-11-14. Root owns this document. Product name: Blockwise. Active task assignments supersede the original ownership list below.
Development only in C:/Users/qcdre/Documents/repo/2026princetonhacks-2, branch feature/houston-shopping-lab.
Never edit the source sibling princetonhack2026. Never read private .env. It was copied as authorized.

## Ownership
Backend agent: apps/backend/src/spending_api/lab_*.py except lab_chat.py; tests/test_lab_*.py except chat/integration.
Participant agent: apps/participant/**; tests/participant.test.cjs.
Analytics agent: apps/analytics/**; tests/analytics.test.cjs.
Root: data/scenarios.json, scripts, Docker/Compose, manifests, docs, integration, chat, pruning.
Use vanilla HTML/CSS/JS, no bundler. Apps are distinct nginx containers, same backend via /api proxy.
Participant localhost:5183, analytics localhost:5184, backend localhost:8775. Compose project blockwise-lab.
All monetary amounts integer USD cents. Durations numeric minutes, display rounded but feasibility uses actual model.

## Scenario seed shape (illustrative original shape; current catalog in data/scenarios.json)
{"schema_version":1,"scenarios":[{"id":"houston-heights","version":"v1","name":"Heights weekday shop","description":"...","home":{"label":"...","latitude":29.8,"longitude":-95.4,"housing_context":"...","source":"costar","source_record_id":"..."},"defaults":{"budget_cents":7000,"time_budget_minutes":45,"travel_mode":"drive","max_stores":2},"basket":[{"id":"milk","name":"Milk","quantity":1,"unit":"1 gallon","category":"Dairy"}],"stores":[{"id":"corner","name":"Corner Market","latitude":29.81,"longitude":-95.41,"shopping_minutes":8,"prices":{"milk":420}}],"travel":{"method":"modeled_distance","road_factor":1.3,"walk_kmh":4.5,"drive_kmh":24,"drive_cost_cents_per_km":25,"drive_parking_minutes_per_stop":3},"provenance":{"housing":"Recorded CoStar location; not household financial data.","prices":"Simulated store prices, not live quotes.","travel":"Modeled travel estimates, not routing directions.","stores":"One fixed shared fictional store world; stores do not move with the home."}}]}
Per-item price is per stated unit times quantity. All stores supply basket initially. Max 5 stores, <=10 products, <=2 stops.
CoStar grounds home locations and recorded rents; fictional stores remain explicit. Current IDs are houston-rental-west, houston-rental-central and houston-rental-east. All share world_id/world_version, stores, basket and defaults; see WORLD.md. Housing fees and all-in costs are unknown, and rent does not set income or shopping budget.
Only local small scenario seed is shipped in image, never entire dataset/.env.
Scenario source version immutable fingerprint on session; store scenario snapshot in SQLite session to preserve old experiments.

## API
Backend factory spending_api.lab_api:create_app. GET /health => {"status":"ok","app":"blockwise"}.
GET /api/v1/scenarios => {"scenarios":[FULL_SCENARIO,...],"simulation_notice":"..."}.
POST /api/v1/sessions body {"scenario_id":"...","constraints":{"budget_cents":7000,"time_budget_minutes":45,"travel_mode":"drive","max_stores":2},"is_demo":false,"consent":true,"request_id":"client UUID"}
=> {"id":"uuid","token":"random secret","scenario":FULL_SNAPSHOT,"constraints":CONSTRAINTS,"is_demo":false,"created_at":"ISO"}
Session request_id idempotency: same body repeat returns same session and token; conflicting body 409.
Each session API requires Authorization: Bearer <session token>; never return tokens in analytics.
GET /api/v1/sessions/{id} => SESSION plus choices [] (no token needed in response).
POST /api/v1/sessions/{id}/evaluate body {"assignments":{"milk":"corner",...},"constraints":CONSTRAINTS(optional)}
=> EVALUATION; does not persist choice or reveal recommendations.
EVALUATION = {"basket_cost_cents":..., "transport_cost_cents":..., "total_cost_cents":..., "travel_minutes":..., "shopping_minutes":..., "total_minutes":..., "feasible":true,"violations":[],"store_ids":["corner"],"route":["home","corner","home"],"assignments":{...},"constraints":CONSTRAINTS}
Enforce complete basket, known IDs, valid cents, nonempty assignments, max2 stores. Impossible plan gives feasible false and readable violations, malformed input422.
POST /api/v1/sessions/{id}/choices body {"request_id":"client UUID","stage":"initial"|"revised","assignments":{...},"constraints":CONSTRAINTS(optional)}
=> {"id":"choice uuid","stage":"initial","evaluation":EVALUATION,"created_at":"ISO"}.
One initial choice per session; revised only after initial; duplicate request_id exact returns existing, conflict409. Invalid/incomplete assignments rejected. Infeasible but complete initial choices may be stored and marked infeasible (experimental observation); revised same.
POST /api/v1/sessions/{id}/recommendations body {"constraints":CONSTRAINTS(optional)}
=> {"status":"ok"|"no_feasible_plan","plans":[PLAN,...],"evaluated_plan_count":int,"feasible_plan_count":int,"notice":"..."};
requires initial choice409 until submitted. PLAN adds id,label ("Lowest cost","Least time","Balanced"), evaluation fields. Enumerate finite store subsets/order, assign cheapest available product in subset, deduplicate used stores, Pareto filter. Max8 plans. Do not silently relax constraints.

## Analytics
Optional env LAB_ADMIN_TOKEN: if nonempty all /api/v1/analytics routes require matching Bearer token; otherwise local-only mode, API docs honest. UI access-token input (not provider keys), only sessionStorage.
GET /api/v1/analytics/summary?scenario_id=...&data_mode=participants|demo|all (default participants)
=> {"data_mode":"participants","session_count":int,"completed_session_count":int,"initial_choice_count":int,"revised_choice_count":int,"matched_pair_count":int,"infeasible_choice_count":int,"mean_initial_cost_cents":number|null,"median_initial_cost_cents":number|null,"mean_initial_minutes":number|null,"mean_savings_cents":number|null,"mean_time_change_minutes":number|null,"scenario_breakdown":[{"scenario_id","scenario_name","sessions","initial_choices","revised_choices"}],"store_choices":[{"store_id","store_name","choice_count"}],"tradeoffs":[{"session_id","scenario_id","stage","cost_cents","minutes","feasible","is_demo"}],"notices":["..."]}
Savings and time deltas only matched initial/latest revised with identical constraints and scenario snapshot; matched_pair_count explicit. revised with changedconstraints not same comparison; per session summaries explain.
GET /api/v1/analytics/sessions?scenario_id=&data_mode=&limit=50&offset=0
=> {"sessions":[{"id","scenario_id","scenario_name","is_demo","created_at","constraints","initial":CHOICE|null,"latest_revised":CHOICE|null,"choice_count":int,"comparable":bool}],"total":int,"limit":50,"offset":0}
GET /api/v1/analytics/sessions/{id} => session snapshot and all choices, no token, no request_id.
GET /api/v1/analytics/export?scenario_id=&data_mode= returns CSV: one row per choice, no credentials, formula-safe cells.
Participant collection is anonymous with notice that choices are saved for this simulation, no PII requested.
No silent synthetic participant seeding; root seed demo CLI creates is_demo=true sessions only, separated in analytics.

## UX direction
Warm paper (#f4f1e8), forest (#173f35), lime (#d5ee87), ink (#172b24), restrained coral.
Participant: approachable neighborhood shopping challenge, numbered progress, clear budget/time meters, simple store/basket cards, optional schematic SVG map with home and stores. Persistent 'Simulated prices and travel' visible. Accessible keyboard, loading/error states, mobile first.
Analytics: calm research dashboard, strong typography, useful empty state, explicit participant/demo mode, charts with labels and accessible table alternative, scenario filters, paired comparison explanation, session detail, CSV.
Cross-app links use default respective localhost ports, configurable easily. No fake results or blank functions.
Do not show technical implementation/provider details in participant journey.

