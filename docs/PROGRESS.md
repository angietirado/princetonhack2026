# Regulars development log

## Independent fork
- Location: `Documents/repo/2026princetonhacks-3`.
- Branch: `feature/customer-intelligence`, copied from the original Ledgerly project at `09a62d8`.
- Private `.env` and existing generated/downloaded data copied without opening private credentials.
- Original repository remains unchanged. Docker project `regulars-lab`, frontend 5193/API 8785, separate `owner-data` volume.

## Implemented
- Owner dashboard and responsive customer explorer, observed cohorts and rule-based segments.
- Integer-cent revenue/refund accounting, exact period-comparison contribution reconciliation.
- Per-customer purchase/refund evidence, observed-history versus selected-period totals, and stable source-bound pagination.
- CSV preview with completed/excluded orders and reconciled purchase/refund totals, explicit reviewed commit, immutable SQLite datasets, safe exports, separate demo selection.
- Experiment drafts and shared deterministic audience/offer-budget calculator, with no assignments or outreach.
- Durable reviewed pilot records, immutable source/calculation evidence and append-only owner observations in additive SQLite tables. Exact retries deduplicate, including after source changes.
- Deterministic revenue-change investigation plus optional bounded Deep Agents investigation with aggregate-only tools.
- Readable answer evidence cards/tables, expandable calculation details and deduplicated source limitations.
- Source/revision checks for answers, comparisons, plans, customer history and exports; unapplied date edits cancel obsolete work and remain clearly distinguished from the applied period.
- Docker-first regression and real-browser tooling, including synthetic import persistence checks.

## Verified milestones
- Initial checkpoint `b03c605`: 142 backend, 31 dashboard DOM and six browser scenarios passed.
- Customer-evidence checkpoint `ae9f147`: 197 backend and 45 dashboard DOM tests passed.
- Customer-evidence milestone: all ten Chromium scenarios passed across desktop/mobile: dashboard/customer/question/export, drawer controls, reviewed short synthetic import, comparisons, pilot budgeting, future-date controls, customer history reconciliation and keyboard focus, 60-purchase pagination, and offline revenue-change evidence.
- Durable-pilot milestone: 278 backend tests, 53 dashboard DOM tests and all twelve browser scenarios passed. New desktop/mobile scenarios save a reviewed draft, reload it, add a dated observation, and reopen its original evidence under a different active dataset; exact repeats reuse stored records.
- Live Docker HTTP smoke passed, including customer evidence, reconciled revenue comparisons and saved-draft reads.
- Customer drawer, revenue-change and saved-draft screenshots reviewed on desktop/mobile; no horizontal page overflow or browser page errors.
- Desktop CUA unavailable (GetCursorPos access denied); Docker Playwright provided actual rendered QA.

## Latest completed corrections
- Future dates cannot manufacture inactivity or enable an unsupported pilot draft.
- Gross average purchase is distinct from net revenue per order when prior-order refunds occur.
- Short imports expose within-period repeat buying and a manual pilot without automatic suggestions.
- Optional read-only Nessie merchant sandbox import preserves synthetic provenance and active selection by default.
- Long AI date windows use explicit seven-day aggregation with exact totals.
- SQLite connections close promptly instead of accumulating until garbage collection.
- Paging keeps keyboard focus inside customer evidence even when the final Load more button disappears.
- Late dashboard/question responses cannot overwrite subsequent date edits or restore obsolete answers.
- The demo script uses supported questions and precise segment definitions; sponsor prose distinguishes implementation from live/provider/consultation evidence.

## Verification boundaries
- No private `.env` reads or indirect loading during agent checks; all Compose verification passes `--env-file .env.example`.
- No live AI provider, Nessie import, outbound messages, deployment, newly created public repository or hackathon submission is claimed. The user requested a branch push to the existing repository.
- Sponsor criteria and outstanding eligibility evidence are recorded in `SPONSOR_FIT.md`; `OWNER_INTERVIEW.md` prepares a real consultation without implying one occurred.

Development paused at the user's explicit request on 2026-11-14. Agents were interrupted; only the requested branch handoff remains.

## Paused handoff
The durable-pilot workflow is complete and verified at `dbd2b95`. The final wording pass aligns group labels with observed evidence while retaining all group IDs and calculations. Push this branch to the existing GitHub repository and leave further development paused.
