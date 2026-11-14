# Wallet UI review

Independent visual and source review, November 14, 2026.

## Outcome

The wallet feature is understandable on the inspected desktop setup and 375 px mobile screens. No wallet-specific functional blocker or clipped mobile control was found. The participant can distinguish the fictional wallet from a live bank connection, see checking cash and scheduled bills separately, and inspect the effect of one shopping decision.

## Evidence

Inspected current Docker Chromium artifacts:

- `.data/browser-artifacts/participant-desktop.png`: optional profile selector in setup.
- `.data/browser-artifacts/participant-mobile.png`: full-width selector and readable optional-context explanation.
- `.data/browser-artifacts/participant-wallet-mobile.png`: restored Tight week session after a revised choice, with the independent cash cap and one-trip preview.

The parent reported nine passing tests in the first ten-test browser run. The remaining failure was a test selector that omitted the accessible label's word “Optional”; after correcting the selector, the targeted tight-wallet browser flow passed. This review did not restart containers or rerun that suite.

## Observed behavior and presentation

- Setup defaults to “Choose your own budget (no wallet)”. Fictional data and the absence of a live bank connection are explicit.
- The active Tight week panel shows $140 checking available, $110 scheduled bills, and $30 current shopping capacity. Holds and unused credit are explained.
- A $49.93 shopping plan retains the separately chosen $70 budget, but is marked outside the limits because it exceeds the $30 cash cap. The violation identifies the actual constraint.
- The one-trip preview shows $0 remaining shopping capacity, negative $19.93 modeled ending cash, and a $19.93 shortfall. The text explicitly says saved revisions are alternatives and no money moves.
- The cash breakdown, warning, profile selector, basket controls and chat controls stay within the inspected mobile viewport. The error is expressed in text as well as color.
- The profile selector has a visible label and descriptive help. Wallet calculations are represented as labeled definition-list values; preview changes have a status region.

## Concrete copy improvements

| Priority | Finding | Recommended change |
|---|---|---|
| Low | The plan meter says “$20.07 left from $70.00” while the wallet is short of cash. The calculation is correct, but “left” can sound like remaining cash. | Name the selected limit explicitly: “$20.07 under your chosen $70.00 budget.” |
| Low | After the test saves a later choice and reloads, the chat history still discusses $500, while the card says “Last suggested limits: $70”. `loadChat()` renders the server's current conversation constraints, which synchronize to the latest saved choice; these are not necessarily the last suggestion. | Use a neutral restoration label such as “Restored guide limits”, or expose and label the state source explicitly. |

These are wording issues; the inspected wallet arithmetic and save/apply boundaries remain clear. App and test files were not changed by this review.

## Resolution

The parent applied both copy changes. All 37 interface tests passed afterward, along with the two targeted Chromium wallet and chat flows. The saved choice, wallet projection and chat behavior were unchanged.
