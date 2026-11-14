# Blockwise development progress
Updated November14, 2026. Continuous development remains active until the user says to stop.

## Workspace
- Separate clone: Documents/repo/2026princetonhacks-2.
- Branch: feature/houston-shopping-lab; original base09a62d8.
- Original repository untouched by this task.
- Private .env copied directly without reading; copied CoStar and existing .data preserved.
- Distinct Docker project blockwise-lab, volume lab-data, ports5183/5184/8775.

## Working
- Participant app: scenarios, consent, money/time/transport/stop constraints, basket selection, evaluated totals, first choice, recommendations, revised choice, resume and demo mode.
- Analytics app: participant/demo/all filter, scenarios, paired metrics, charts, sessions/history, authenticated CSV export.
- Backend: validated integer-cents calculations, complete-trip estimates, exhaustive one/two-store plans, durable SQLite snapshots and idempotent choices.
- Conversational backend: four-node LangGraph, strict offline commands, optional provider interpretation, persistent history/constraints, safe retry/concurrency handling.
- Removed inherited old dashboard/React scaffold, Photon integration, legacy banking API and old generator from active fork; all remain in original/history.

## Verified
-71 backend/chat/scenario tests passed through Docker.
-25 dashboard regression tests passed, including participant chat and analytics label fixes.
-Both frontend HTTP services and complete demo API flow passed smoke after backend upgrade.
-Final independent browser suite:8of8 passed after fixing mobile analytics overflow; includes chat proposal/apply/save/reload and all earlier flows.
-Additional desktop/mobile recommendations browser flow passed.
-Native desktop browser tool failed with Windows GetCursorPos access denied; visual tests run in isolated Playwright Docker instead.
-18 explicit demo sessions seeded, plus clearly marked QA sessions. No automated tests create genuine participant observations.

## Active agents / work
- Participant chat UI and mobile improvements completed and verified.
- UI/UX and testing agent: independent real-browser checks and screenshots; findings in UI_REVIEW.md.
- Backend agent: read-only Nessie adapter and deterministic financial-context projection.
- Root: integration, documentation, source preservation, verification, scenario/world improvements.
- Separate creative-integration agent reviewed the app and full hacker packet; direction captured in PRODUCT_DIRECTION.md.

## Current priorities
1. Participant chat UI and final mobile fixes verified; committing the first milestone.
2. Checkpoint a working two-app MVP.
3. Add a synthetic Nessie financial context with explicit provenance and cashflow assumptions.
4. Replace home-relative fictional store patterns with a fixed shared world before housing/location comparisons.
5. Implement useful adaptive decision investigation; choose the harness for the task.
6. Consider Photon only where the channel improves participation. Matterport and Vexcel have no current product need and are outside the roadmap.

## Latest user steering
Consider all angles; no specific track is selected. Photon may be useful and is no longer explicitly excluded.
No live provider calls, sandbox-bank writes, external messages, public deployment, or submission have been performed.

User requires a commit after every verified milestone. Natural integration takes priority over sponsor coverage.
