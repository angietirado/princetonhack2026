# Regulars development log

## Independent fork
- Location: `Documents/repo/2026princetonhacks-3`.
- Branch: `feature/customer-intelligence`, copied from the original Ledgerly project at `09a62d8`.
- Private `.env` and existing generated/downloaded data copied without opening private credentials.
- Original repository remains unchanged. Docker project `regulars-lab`, frontend5193/API8785, separate `owner-data` volume.

## Implemented
- Owner dashboard and responsive customer explorer, observed cohorts and rule-based segments.
- Integer-cent revenue/refund accounting, exact period-comparison contribution reconciliation.
- CSV preview, explicit reviewed commit, immutable SQLite datasets, safe exports, separate demo selection.
- Experiment drafts and shared deterministic audience/offer-budget calculator, with no assignments or outreach.
- Optional bounded Deep Agents investigator with aggregate-only tools and explicit completion/evidence status.
- Cross-tab source/revision checks for answers, comparisons, plans and exports.
- Docker-first regression and real-browser tooling, including synthetic import persistence checks.

## Verified milestones
- Complete merchant backend suite:142 tests passed, including analytics, CSV/store/API, comparison, experiment coverage, real agent graphs with scripted models, and mocked Nessie reads.
- Dashboard DOM suite:31 tests passed, including copied-pilot assumptions and invalidation.
- Six Chromium scenarios passed across desktop/mobile: dashboard/customer/question/export, drawer controls, reviewed short synthetic import and manual pilot, comparisons, pilot budgeting, and future-date coverage controls.
- Live Docker HTTP smoke passed. Initial desktop/mobile screenshots reviewed; no page errors or horizontal page overflow.
- Desktop CUA unavailable (GetCursorPos access denied); Docker Playwright provided actual rendered QA.

## Latest completed corrections
- Fixed the observed future-date recency bug: unobserved days must not manufacture inactivity.
- Distinguished gross average purchase from net revenue per order when prior-order refunds occur.
- Made within-period repeat buying visible for short imports and expose a manual pilot without suggested cards.
- Added optional read-only Nessie merchant sandbox import, preserving synthetic provenance and existing selection by default.
- Kept valid long date windows within AI evidence limits using explicit seven-day aggregation with exact totals.

## Verification boundaries
- No private `.env` reads or indirect loading during agent checks; all Compose verification passes `--env-file .env.example`.
- No live AI provider, Nessie import, outbound messages, deployment, public repository publication or hackathon submission is claimed.
- Sponsor criteria and outstanding eligibility evidence are recorded in `SPONSOR_FIT.md`.

Development remains continuous at the user's request until explicitly stopped.
## Current pass
Copied-draft assumptions and readability refinements are complete. The refreshed app passed all six browser scenarios and live HTTP smoke checks. Next: improve per-customer evidence so an owner can inspect why a customer belongs to a group, while continuing concrete workflow reviews.
