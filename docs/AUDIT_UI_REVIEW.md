# Feasibility audit UI review

Independent visual and source review, November 14, 2026.

## Outcome

The audit communicates a modeled comparison without presenting it as observed behavior. The inspected mobile layout has readable controls, separate cohort counts, explicit denominators, and a visible distinction between the analyst's budget/time increase and the wallet's unchanged cash limit. No functional blocker was identified.

## Evidence

Reviewed `docs/AUDIT.md`, the analytics rendering code, and these current Chromium artifacts:

- `.data/browser-artifacts/analytics-audit-mobile.png`: expanded audit at 375 px, with a $2 and five-minute combined probe and the Tight week cohort selected.
- `.data/browser-artifacts/analytics-mobile.png`: full analytics page, including the initial audit controls.

The parent reported 24 analytics JSDOM tests and two Chromium flows passing. This review did not rerun tests, change app files, or restart containers.

## Findings

| Area | Observed result |
|---|---|
| Cohort separation | The view selects one exact cohort and identifies demo status, source snapshot, initial limits and wallet. Counts are explicitly limited to that cohort. |
| Denominators | Four baseline categories show counts out of two valid initial choices. Newly feasible is explicitly 0 of the two baseline no-plan-fits choices. Skipped and selected-session counts are separate. |
| Analyst comparison | The UI says the $2 and five-minute increases apply together, shows effective $72 / 95-minute limits, and states that wallet cash, transport and stops stay fixed. |
| Wallet context | Tight week remains a synthetic fixture with $30 current spending cash. The probe does not present its larger stated budget as new cash. |
| Outcome wording | “Another plan fits” explains that the recorded selection fails while an alternative fits. “No plan fits” and “Needs review” remain distinct. The method disclosure separates modeled results from motives and causal effects. |
| Mobile controls | Inputs, action button, category cards and disclosure controls fit the viewport. The long native select value is truncated, but the selected cohort context appears beneath it. |

## Polish requested by parent

1. **Shorten the visible source identifiers.** Full scenario and world hashes consume several mobile lines before the initial limits and wallet identity. Lead with readable cohort context and put complete identifiers in a disclosure. The native select should prioritize a concise home/limits/wallet label where possible.
2. **Replace the zero-denominator headline.** The dominance card shows `0 / 0`; its explanation correctly says no feasible selected plans qualify. “Not applicable” is clearer as the headline. The same presentation rule is appropriate whenever an eligible denominator is zero.

The parent has already requested these changes from the frontend owner. They are clarity improvements, not evidence of incorrect calculations.

## Screenshot artifact

The element-level audit capture includes a “Skip to results” link over the panel. This matches the fixed skip-link stitching artifact already documented in `UI_REVIEW.md`; it does not appear in the full-page analytics screenshot. The browser flow also checks page width. This review does not recommend hiding or changing the legitimate keyboard-accessibility link. A normal viewport capture can distinguish the artifact if further confirmation is needed.

## Resolution

Both polish items are fixed. The parent reviewed a fresh normal-viewport mobile capture: source identifiers are shortened, empty comparisons read Not applicable, and the skip-link stitch artifact is absent. The final integrated checks passed 284 backend tests, 65 interface tests and all 14 Chromium browser flows.
