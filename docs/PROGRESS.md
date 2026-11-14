# Blockwise development progress

Updated November 14, 2026. Continuous development remains active until the user says to stop.

## Workspace and checkpoints

- Separate clone: `Documents/repo/2026princetonhacks-2`, branch `feature/houston-shopping-lab`.
- Original base: `09a62d8`. The original repository is untouched by this task.
- Private `.env` copied directly without reading. The copied CoStar kit and existing `.data` are preserved.
- Docker project `blockwise-lab`, persistent volume `lab-data`, ports 5183 / 5184 / 8775.
- `a8fa198`: working participant and analytics MVP, with persistent choices, chat and regression coverage.
- `cf9ae50`: one fixed Houston store world across three recorded rental homes.
- `7bfe37d`: mobile analytics session access from the first table column.
- `ea9500d`: deterministic decision investigation and optional bounded LangChain evidence selection.
- `5b5afa5`: frozen synthetic wallets, dated cash projections and a read-only Nessie adapter.
- `1123f6e`: recorded home facts in the participant interface.
- `9c2a81a`: wallet guards across all shopping paths and persistent authenticated investigations.
- `b897283`: optional wallet selection, cash breakdown and one-trip preview with independent UI review.

## Working in the apps

- Participant: consent, home selection, money/time/transport/stop constraints, complete basket selection, canonical totals, first choice, alternatives, revisions, resume and explicit demo mode.
- Analytics: participant/demo/all filters, scenario filters, paired metrics, charts, session history and CSV export. Authentication is available for analytics.
- Backend: integer-cents arithmetic, complete round trips, exhaustive one/two-store plans and durable SQLite snapshots with idempotent writes.
- Chat: four-node LangGraph, strict offline commands, optional model interpretation, persistent history and explicit proposal/apply/save steps.
- Three rental homes share identical fictional store locations and prices. Recorded rents remain historical context; they do not imply household income.

## Additional verified capabilities

- Participants can optionally select a fictional wallet. Wallets distinguish checking cash, pending holds, scheduled bills and unused credit. Unknown bill timing or ambiguous payment links produce `needs_review`, not an invented allowance.
- The read-only Nessie adapter pins one customer, validates account scope, bounds pagination and verifies cash consistency. Tests use mocked HTTP; no live credentials or bank writes were used.
- Investigation computes complete-basket evidence, exact minimum changes and bounded alternatives. Protected limits, current cash and available transport restrict all proposals.
- Optional LangChain tools inspect evidence, test allowed changes and compare verified plans. The model can select evidence and questions, not invent amounts or save choices.

## Verification

- Original checkpoint: 71 backend/chat/scenario tests, 25 interface tests and 8 real-browser tests passed.
- Shared-world checkpoint: 96 backend/chat/scenario tests passed; full HTTP demo flow passed after deployment.
- Wallet/Nessie foundation: 89 tests passed through Docker.
- Full wallet/investigation backend integration: 266 tests passed through Docker, including the expanded 35-case investigation engine suite.
- Audit foundation and existing backend routes: 44 targeted tests passed. The live demo-data audit analyzed 41 initial choices in 22 separate cohorts; counts may grow as demo tests run.
- Latest participant/analytics interface suite: 37 tests passed. All 10 real-browser cases passed across the full run and targeted reruns after a selector correction; wallet and chat copy fixes were rechecked.
- Independent wallet visual review found no clipping or functional blocker; both identified copy issues were fixed. See WALLET_UI_REVIEW.md.
- Native desktop browser initialization failed with Windows access denied; actual Chromium testing and screenshots use isolated Playwright Docker.
- Automated checks create only clearly marked demo sessions, never genuine participant observations. Existing data volumes are preserved.

## Active work

1. Expose recorded home facts clearly in the participant interface.
2. Connect optional synthetic wallet profiles to session creation, evaluation, recommendations and chat.
3. Add authenticated, retry-safe investigation reports and an explicit participant interaction.
4. Repeat independent UI/UX and browser review as each visible feature becomes usable.
5. Continue improving the experiment and analytics based on verified behavior; commit every milestone.

## Product direction

Natural integration takes priority over sponsor coverage. No track has been selected. Photon remains an option when messaging improves participation. Matterport and Vexcel have no current product need and are outside the roadmap.

Inherited dashboard, React scaffold, Photon backend and legacy banking code were pruned from this fork; they remain in the original repository and Git history. No public deployment, repository publication, external messages or hackathon submission has been performed.
