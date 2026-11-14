# A two-minute Blockwise demonstration

Use the explicit demo URL so rehearsal choices stay separate from participant data:
http://localhost:5183/?demo=1. Analytics is at http://localhost:5184.

This script uses the current fixed Houston world. It demonstrates an observed choice inside a simulation, not actual consumer spending or real store prices.

## Story and setup

“I can spend $45 and have 15 minutes. Is there a way to get the same basket without pretending my constraints disappear?”

Choose West / Rosine Street, the Steady week fictional wallet, a $45 shopping budget, 15 minutes, driving, and one store. The wallet has $400 checking cash after holds and a $180 example bill, leaving a separate $220 modeled shopping capacity. The $45 limit is the participant's choice, not an estimate of their income.

Consent to the demo, start, and buy the whole basket at Corner Market. The modeled total is $61.62 and approximately 11.1 minutes. Save the initial choice: a complete but infeasible plan is a useful observation.

## Investigate the tradeoff

The investigation API has verified these results while protecting the one-store limit and driving mode:

| Change | Complete-basket cost | Modeled trip |
|---|---:|---:|
| Allow 37 minutes, keep $45 | $41.53 | 36.6 minutes |
| Allow $61.62, keep 15 minutes | $61.62 | 11.1 minutes |
| Allow $49.93 and 25 minutes | $49.93 | 24.4 minutes |

The consequential question is whether spending more or allowing more time is easier. Every proposal includes the same basket and the complete return trip. Applying changes the draft; saving records a revised choice.

Open “What would make it fit?”, keep Store limit and Travel mode fixed, and click “Check possible changes”. Choose “More time” to highlight the $41.53 option. Click “Use this draft”, then save the revised choice. This exact path passed the real Chromium browser check; the screenshot is in .data/browser-artifacts/participant-tradeoff-mobile.png.

## Close with the research view

Open analytics and select Demo data. Show the initial and revised decisions, then the feasibility audit. Explain that exact wallet, scenario and initial-constraint cohorts stay separate. If the participant changed their limits, the dashboard does not call that a like-for-like savings effect.

A short closing line: “The app makes the tradeoff visible, and the experiment records what people choose. The model's explanation is grounded in computed options; it does not invent a cheaper basket.”

## Boundaries to state naturally

- Home coordinates and asking rents come from historical CoStar records. The stores, prices and travel are simulated.
- The demo wallet is a labeled local fixture. A tested read-only Nessie adapter exists, but this rehearsal does not claim to be a live bank capture.
- The calculated investigation works offline. Optional LangChain prioritization has bounded tools and output validation; live provider calls remain unverified here.
- No public site, new public repository or hackathon submission has been created by this development session.
