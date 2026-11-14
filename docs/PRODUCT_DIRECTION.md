# Product direction and sponsor inspiration
Updated November 14, 2026. These are proposed extensions unless explicitly marked implemented.

## User direction
Keep developing both the participant collection app and analytics app in the separate checkout.
The user asks for creative, substantive Nessie, LangChain, and CoStar integration, not only basic analytics.
Do not preselect a hackathon track. Consider all useful angles. Photon is an option again, superseding the earlier instruction to ignore it.
Keep the original repository unchanged. No private .env inspection during verification.

## Core idea
Blockwise is a life-rehearsal experiment: try a week under money, time, housing, and transport constraints, then discover which changes make it work.
Personal finance, financial education, behavioral research, and housing choices are complementary applications.
The current fixed-basket shopping challenge is the first working slice.

## Implemented foundation
- Separate participant and analytics apps, one local FastAPI backend.
- Complete-basket calculations, total return-trip time, one/two-store alternatives, explicit simulated inputs.
- Versioned scenario snapshots, anonymous sessions and saved choices, demo isolation, durable retries.
- Descriptive matched initial/latest-revised comparisons only when constraints match.
- LangGraph conversational limit controls; optional LangChain provider interpretation, deterministic calculations.
- SQLite conversation persistence. No LangSmith tracing. Live AI provider calls have not been verified in this fork.

## Next substantive features
### Financial situation, not a random allowance
Read and freeze a synthetic Nessie financial snapshot. Use checking availability, dated obligations, explicit expected income, and a protected reserve to construct a modeled shopping allowance.
Never equate credit availability with cash or infer income from a house price. Do not double-count bank holds.
Current Nessie bill payment_amount is dollars while transaction amount_cents is cents; normalize deliberately.
Experimental decisions branch a local ledger. They must not mutate another participant's starting wallet or trigger sandbox writes automatically.
Offline fixtures remain labeled fictional; a fixture is not a live Nessie capture.

### Investigate the decision
A bounded deep agent should read the financial context, identify binding constraints, run counterfactuals, ask one consequential question, and revise its plan.
Example: 'Would you accept another stop, or can your time limit move?' The answer should change which alternatives it investigates.
The guide produces an inspectable decision card: proposed plan, evidence, assumptions, and unresolved tradeoffs.
Use deterministic tools for calculations and one controlled investigation workflow. Deep Agents planning and delegation should serve the task.
An aggregate research assistant may investigate why cheaper options were rejected and propose follow-up experiments; it must distinguish a hypothesis from causal evidence.

### Make location meaningful
The initial seed places identical fictional stores at the same offsets around each home. This is sufficient for testing shopping interactions but is not a meaningful location comparison.
Create a fixed shared store world, then move the same household between recorded CoStar rental options. Keep the basket, stores, assumptions, and household needs fixed.
Record rent ranges and unknown fees separately; sale values do not establish monthly housing costs.
Matterport and Vexcel have no convincing role in the current product and are not planned integrations. Earlier spatial-extension ideas are research possibilities only, not roadmap commitments.
An interior scan alone cannot prove entrance accessibility, appliance operation, storage capacity, or street-to-door travel time.
Vexcel may add visible spatial context; imagery alone does not establish safe pedestrian routes.

### Photon as another way to participate
Messaging/voice may collect a short decision, deliver a scenario change, or ask the next preference question.
Reuse the same session, wallet, and planner tools; record channel and recommendation exposure.
No outbound messages without user authorization. Photon is currently not implemented in this fork.

## Packet findings
Source: user-provided F26 Onsite Hacker Packet, 21 pages; text read throughout and relevant rules/sponsor pages visually inspected.
- General rubric (p4): creativity, utility, charity, avidity. Show an aha moment and polished useful behavior.
- CoStar (pp13-14): seeks at least two of CoStar, Matterport, Vexcel used meaningfully, agentic behavior and spatial/visual understanding. Weighting: data30%, agentic depth30%, usefulness25%, demo/polish15%.
- LangChain (p16): track specifically names deepagents. Plain LangGraph integration is useful but does not demonstrate that harness.
- Capital One (p18): creative Nessie endpoint use to improve financial lives.
- Amazon (pp14-15): practical adaptive agentic AI, with creativity/usefulness/implementation/UX each25%. The packet allows varied stacks, not only AWS.
- Peri (pp15-16): actual business or qualifying club consultation, named person/role and evidence. General playtesting alone is not equivalent.
- Photon (pp18-19): messaging-agent opportunity; consider it when the channel improves the experience.
- Arlequin (pp16-18): forensic investigation or content+connectivity analysis; do not claim these solely because the product has analytics.
- ChatOverflow (p19): coding-agent contribution track, separate from product functionality.

## Submission constraints to preserve
The packet states an 8am Sunday November15 deadline; exactly one main track plus optional special tracks; public GitHub repository created November13-15; mandatory in-person demo at most2minutes; optional video at most2minutes; judging9:30am-2:30pm Sunday; Discord usernames in submission.
This local checkout has not been published as a new public repository or submitted to Devpost.
Track selection remains open. Sponsor criteria inform ideas, not a promise of eligibility.

## Evidence references
- Nessie public schema: https://v2-prod-api.nessieisreal.com/openapi.json
- Deep Agents overview: https://docs.langchain.com/oss/python/deepagents/overview
- LangGraph workflows: https://docs.langchain.com/oss/python/langgraph/workflows-agents
- CoStar kit: local ignored costardata/index.md and model documents.
