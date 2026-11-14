# Sponsor fit and evidence

Updated November 14, 2026. Track choices remain open. This document separates actual behavior, useful next steps, and eligibility evidence.

## Product direction

Regulars helps a business owner investigate changes in their observed customer activity and design a small, measurable experiment. A useful demonstration is: "Which regulars have gone quiet, what does the evidence show, and how could I test a welcome-back idea within a fixed budget?"

Business + Enterprise is the clearest main-track candidate. It is one of the six tracks on the [official event site](https://www.hackprinceton.com/); the [official Fall 2026 Devpost page](https://hackprinceton-fall-26.devpost.com/) includes business operations and analytics within that track.

## Sponsor opportunities

The local F26 Onsite Hacker Packet supplied in the earlier project is the source for the detailed sponsor criteria below. Relevant pages 13-18 were visually reviewed, and the page 19 text was checked. The [official event site](https://www.hackprinceton.com/) confirms the named sponsors; it does not independently publish all packet criteria.

| Opportunity | Criteria from the packet | Honest fit and present status |
| --- | --- | --- |
| LangChain: best use of deepagents | Page 16 specifically names the Deep Agents harness and allows any use case. | Optional owner investigation uses actual create_deep_agent and a compiled analyst with aggregate summary, revenue comparison, cohorts, segments and experiment tools. Scripted offline tests exercise delegation. No live model success is claimed. |
| Amazon: practical AI innovation | Pages 14-15 emphasize practical usefulness, adaptive agentic behavior, implementation and UX. The packet permits varied stacks. | An investigator that gathers evidence, accepts owner constraints and returns a testable plan fits. AWS use is not listed as a prerequisite in the packet. |
| Peri: forward deployed challenge | Pages 15-16 require consultation with an actual business or qualifying student organization, its name, the consulted person's name and role, and supporting evidence. | Strong potential fit, but no such consultation is supplied for this fork. A fictional cafe or generic playtest does not satisfy this condition. |
| Capital One: best usage of Nessie | Page 18 asks for creative endpoint use to improve financial lives. | An optional read-only merchant importer now converts explicitly scoped Nessie sandbox purchases and refunds into synthetic owner datasets. Offline tests cover the adapter and preview/save/select workflow; no credentialed live capture or real POS integration is verified. |
| Photon: messaging agent using Photon 2.0 | Pages 18-19 provide the messaging-agent opportunity and separate voice prize. | An owner could ask for a grounded update by message, or voluntarily submitted feedback could inform an investigation. No owner messaging workflow or delivery is implemented here. |
| CoStar: Inside and Out | Pages 13-14 require meaningful combination of at least two of CoStar, Matterport and Vexcel, agentic behavior, and spatial/visual understanding. Weights are data 30%, agentic depth 30%, usefulness 25%, demo/polish 15%. | A later location decision could combine aggregate customer catchment information with real property and physical-site evidence. Ordinary customer charts or one property lookup do not establish this fit. |
| ChatOverflow | Page 19 concerns the coding agent's useful contributions. | Development research and generalized field notes are separate from the product's analytics. Never publish customer information or credentials. |

Arlequin's packet tracks concern forensic investigation or combined content-and-connectivity analysis. Ordinary retention analytics alone does not demonstrate either. The packet's physical-AI prize requires actual hardware.

The current [official Devpost prize listing](https://hackprinceton-fall-26.devpost.com/) also includes Gemini API, Tiger Data, Vultr and MongoDB Atlas opportunities. Gemini is an optional provider in this fork. Adding infrastructure only to claim another prize would not improve the core demonstration; those other services are not implemented.

## Optional AI implementation

The merchant_ai module reads only explicitly injected environment variables; it never loads .env or the inherited spending settings.

- OWNER_AI_ENABLED defaults to false.
- OWNER_AI_PROVIDER accepts gemini or nemotron.
- The selected provider requires GEMINI_API_KEY or NVIDIA_API_KEY in the backend process.
- Pinned model identifiers follow the existing project's configured provider choices: gemini-3.8-flash or nvidia/nemotron-3.5-lightning-30b-a3b.
- The run receives only typed aggregate metrics, selected dates, coverage, segments, cohorts and trend totals (daily through 90 days, seven-day buckets for longer windows). Customer rows, customer labels, business labels, arbitrary source descriptions and original definition text are omitted.
- Known dataset customer IDs and labels typed into the question are replaced before sending. The remaining free-text question is sent to the selected provider; this is not a general-purpose personal-information scrubber.
- An ephemeral Deep Agents advisor delegates to a compiled analyst. Five deterministic tools are offered to that analyst, including the same reconciled revenue-change evidence used by the dashboard. Comparisons are checked against the bound source revision and dates; comparison tools return no percentage for incomplete coverage or nonpositive baselines. Model prose still requires review against that evidence. The main model receives only the delegation tool. Filesystem scaffold tools are hidden and rejected if invented, with an additional deny rule on the virtual filesystem.
- One shared ledger permits at most six model calls and eight total tool calls, including delegation. The selected model has a 900-token output cap per call; a 40-second whole-run timeout and bounded context/tool payloads apply. LangSmith tracing is explicitly disabled even if inherited process flags enable it.
- Evidence, activity and completion status are returned separately. A failed or evidence-free answer is never marked answered. Provider error details are not returned.
- Experiment planning calculates an offer-cost ceiling from explicit audience, offer and duration inputs. It does not predict uplift, assign customers, send messages or save a campaign.

[Deep Agents documentation](https://docs.langchain.com/oss/python/deepagents/overview) describes the actual harness and delegation capabilities. Tests use the locked library with scripted models; they do not authenticate to providers.

## Nessie integration boundary

The [public Nessie v2 OpenAPI](https://v2-prod-api.nessieisreal.com/openapi.json) was inspected without credentials. It has merchant metadata endpoints and a cursor-paginated GET /transactions, but no GET /merchants/{id}/transactions route.

The implemented optional merchant adapter paginates accounts and transactions, restricts purchases to an explicit merchant and coverage window, maps payer accounts to merchant-scoped customer pseudonyms, and resolves refunds through valid parent purchases. It preserves integer cents and UTC dates, deduplicates identical records, and rejects conflicting records or incomplete pagination. The default CLI previews counts; explicit --save creates an immutable synthetic dataset, and only --select changes the active dataset. Source limitations, including excluded chargebacks and created_at date semantics, persist into the selected dashboard. See [Nessie owner import instructions](NESSIE_OWNER.md). Offline tests use documented shapes and mocked HTTP; live authentication, refund-account response behavior, useful merchant coverage and concurrent pagination consistency remain unverified. Unrelated account details and banking activity are not persisted or sent to the AI.

## Submission details to recheck

The supplied packet says November 15 at 8:00 a.m.; the current [official Devpost schedule](https://hackprinceton-fall-26.devpost.com/details/dates) lists 8:30 a.m. EST. Work against the earlier time until organizers resolve the discrepancy.

The [official Devpost page](https://hackprinceton-fall-26.devpost.com/) asks for a public GitHub repository created November 13-15, Discord usernames, and an in-person demonstration of at most two minutes; the video is optional. This local project has not been published or submitted by this work. Select one main track and only claim sponsor integrations and consultation evidence that can be demonstrated.


For a concise real-business validation conversation, see [owner interview](OWNER_INTERVIEW.md). No consultation is implied by that preparation guide.
