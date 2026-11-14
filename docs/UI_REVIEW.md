# UI/UX and browser review
Independent review of the two Blockwise apps, 2026-11-14.

## Current milestone result
**Full Docker browser suite: 8 tests passed in 15.3 seconds.** This includes all earlier flows plus the deployed chat and mobile fixes. No functional blocker remains from this review.

## Verification performed
- First Docker Playwright run: **6 of 7 tests passed**. The failed check identified mobile analytics document overflow (375 px viewport, 634 px document).
- Additional targeted participant run, including a 375 px feasible-recommendation layout check: **passed**.
- After the analytics fixes were deployed, the targeted mobile analytics test **passed**. It verified 375 px document width, demo filtering, detail-dialog keyboard focus/Escape, and no JavaScript, asset, or CSP errors.
- Complete real-API demo flow verified: consent, session creation, whole-basket selection, initial submission, valid alternatives, revised submission, refresh/resume, analytics demo filtering, session detail, CSV export.
- Participant-only session count stayed unchanged by the browser flow. Every browser-created session is explicitly marked demo.
- Infeasible $1/1-minute constraints remain unchanged and show an explanation; no alternatives are invented.
- Three-store assignment blocks submission with an actionable message. A two-store choice under a one-store limit is marked infeasible without relaxing the limit.
- Loading failure retry, expired-token recovery, keyboard Escape, dialog focus restoration, and asset/JavaScript/CSP checks passed.
- Screenshots visually inspected at desktop 1440 px and mobile 375 px. No participant page overflow, including feasible alternatives.

## Findings and ownership
| Priority | Finding | Evidence | Resolution |
|---|---|---|---|
| High | Analytics mobile document overflow | Live Chromium document.scrollWidth 634 vs viewport 375; absolute .sr-only spans in rightmost table headers escaped scroll-container clipping | Fixed by analytics owner with .table-scroll { position: relative; }. Deployed fix verified by the passing mobile browser test; document width is 375 px. |
| Medium | Repeated store names in all-scenario chart lack scenario identity | Backend groups stores by scenario, but rendered bars showed only store_name | Fixed by analytics owner. Final mobile screenshot visually verifies scenario labels beneath store names in the all-scenario view. |
| Medium | Mobile basket selects clip selected store prices | participant-infeasible-mobile.png and participant-options-mobile.png show values such as Corner Market · $1… | Fixed and visually verified after deployment. The 375 px browser check confirms selected store text fits the full-width control. |
| Low | Mobile setup title merges words | Hidden br in “Make room for” + “what matters” renders “forwhat” | Fixed after deployment. Browser assertion and mobile screenshot verify the words remain separated. |
| Low | Mobile store-limit option label truncates | 375 px setup renders “Up to two store…” | Fixed after deployment. The mobile control shows the complete “2 stores” label. |

## Design observations
The paper/forest palette, clear cost-and-time meters, explicit simulation notice, strong first-choice/recommendation separation, and analytics empty/error states support the experiment well. The selected plan has an inspectable route and a complete cost breakdown. Analytics distinguishes descriptive paired changes from a causal claim, and includes a table alternative for the scatter chart.

The mobile setup is long: the Start action appears after the hero, map, three scenario cards, and constraints. A jump-to-setup action in the hero would reduce scrolling for hackathon participants. This is a usability improvement rather than a functional blocker.

## Artifacts
Generated files are intentionally ignored under .data/browser-artifacts:
- participant-desktop.png
- participant-basket-desktop.png
- participant-options-desktop.png
- participant-mobile.png
- participant-infeasible-mobile.png
- participant-options-mobile.png
- participant-chat-desktop.png
- participant-chat-mobile.png
- analytics-desktop.png
- analytics-mobile.png (verified deployed fixes)
- analytics-detail-mobile.png
- analytics-mobile-proposed-fix.png (diagnostic temporary CSS only)
- demo-export.csv
- Playwright HTML report and failure traces under report/ and results/

Full-page captures initially stitched offscreen fixed skip links into scrolled images. Live geometry confirmed the links remained above the viewport and unfocused; the screenshot helper now scrolls to the top before capture.

## Limits
Testing used Chromium in Docker because native Windows browser automation failed to initialize. It does not substitute for a real mobile-device pass or assistive-technology user testing. The optional external model provider was not called. The deployed rule-based chat, persistence, and proposal/apply/save boundaries were exercised through the real API.


## Chat follow-up
The eighth browser test creates a demo session and submits an initial choice, then sends “one store only” through the chat form. The returned proposal has a one-store constraint while the active form retains two stores and the database still contains exactly one choice. Applying the proposal changes only the draft; only the explicit revised Save creates a second choice. The original choice retains its original constraints. Reload restores both the two-message conversation and the applied one-store constraint. No JavaScript, asset, or CSP errors occurred.

Desktop and 375 px chat screenshots were inspected. Proposal text, plan buttons, quick requests, textarea, and explicit Apply/Save wording are legible and remain within the viewport. Element-level mobile chat capture may include the known offscreen skip-link stitching artifact; it does not appear in the active viewport.