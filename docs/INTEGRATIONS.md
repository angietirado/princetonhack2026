# Integration setup

Run Docker Compose commands from the repository root. The credential-free quickstart uses `settings.example.toml` and `.env.example`; the local catalog recipe uses `settings.catalog.toml`, and the opt-in AI recipe uses `settings.ai.toml`. Keep real credentials in the ignored `.env`, never in a public template, browser code, or Git. Host Python is not required.

Keep credentials in the root `.env` and public options in the selected TOML file. The backend reads public options from TOML; those values take priority over obsolete public options left in `.env`. Secrets are loaded from process environment or `.env`, with process environment taking precedence. The public settings file rejects credential fields. Docker mounts it read-only and passes only the relevant credentials to each Python service.

`.env.example` sets `APP_SETTINGS_FILE=./settings.example.toml` for offline checks. Omit that selector from your private `.env` to use `settings.toml`. Native commands also accept `APP_SETTINGS_FILE` in the process environment; they default to `settings.toml`.

Validate shared configuration with `docker compose --env-file .env.example config --quiet`; this uses the public template instead of your private `.env`. Compose passes configured Gemini and NVIDIA credentials only to the backend; the AI enable flag controls whether the assistant can use them.

## Reloading configuration

The backend loads TOML once when it starts. Editing a bind-mounted TOML file does not by itself change Compose's service definition, so `up --build` may leave the old process running. Restart the backend for TOML-content-only edits. Recreate it after changing credentials, the `APP_SETTINGS_FILE` selector, or Compose settings; restarting cannot replace a container's environment variables.

For the named catalog project, retain the project name and both Compose files:

```sh
# Existing TOML file contents changed; keep the same mounted file and credentials.
docker compose --env-file .env -p princeton-langchain-20261114 -f compose.yaml -f compose.costar.yaml restart backend

# Credentials, selected file, or Compose configuration changed.
docker compose --env-file .env -p princeton-langchain-20261114 -f compose.yaml -f compose.costar.yaml up --build --detach --wait --force-recreate --no-deps backend
```

These commands leave the data initializer and generated-data volume alone. For the AI project without a catalog, omit both `-f` flags. For the original default-project workflow, also omit `-p princeton-langchain-20261114`. Use `up --build --detach --wait` for the initial full-stack startup, before using targeted backend commands.

## Nessie v2

Create or obtain a Nessie v2 API key from the hackathon's [Nessie v2 portal](https://v2.nessieisreal.com/). Set `NESSIE_API_KEY` in `.env`. Set `nessie_base_url` in `settings.toml`; the API host is `https://v2-prod-api.nessieisreal.com`. Requests use `Authorization: Bearer ...`. [Official schema](https://v2-prod-api.nessieisreal.com/openapi.json)

The current MVP pins `cust_01m7cen22b7daz0s8gkcp3mt96`, with two accounts, from historical run `sim_01m7cem2mmfn4d36mdnecdtzqc`. That run created 300 transactions. Reuse it; the application never reseeds on startup. The ID is a resource identifier, not a credential. A different Nessie key cannot access this customer's data.

Preview a historical simulation request:

```sh
docker compose --env-file .env --profile tools run --build --rm --no-deps generator --target nessie --seed 42 --days 90 --transactions 300
```

Create the synthetic dataset only when ready:

```sh
docker compose --env-file .env --profile tools run --build --rm --no-deps generator --target nessie --seed 42 --days 90 --transactions 300 --apply
```

Add your chosen `-p` and `-f` flags when using a named or catalog project. The generator writes its run receipt to that project's shared volume, not the host `.data` directory.

For a deliberate replacement dataset, this writes to the Nessie sandbox and saves the response in `.data/nessie-run.json`. Wait for completion, then put the chosen returned ID in `nessie_customer_id` and set `data_source = "nessie"` in `settings.toml`. `NESSIE_API_KEY` and `APP_API_TOKEN` remain in `.env`; live mode requires both credentials and a pinned customer. Restart the backend and call protected routes with the app bearer token. The existing token does not need regeneration.

The generator returns the run state as supplied by Nessie. If it is still running, check `GET /simulations/{id}` before analyzing. A repeated `--apply` is a new creation attempt, not an idempotent refresh. After a timeout, inspect the provider dashboard before retrying. The local `--end-date` option does not control Nessie's dates.

The default Ledgerly dashboard now prompts for `APP_API_TOKEN` when the backend requires it. The token stays in page memory and is cleared on lock or reload. All charts and tables use `/api/v1/dashboard` and the customer pinned in TOML. The API docs also support the **Authorize** button. The separate React scaffold is not the Docker-served dashboard.

## Photon 2.0 Beta quick controls

The receiver and a one-job worker are implemented. Leave `photon_enabled = false` until you configure a channel, credentials, and the enrolled sender. Nothing starts an outbound worker automatically. Offline tests use signed synthetic events and mock transport; live delivery has not been tested.

In the [Photon dashboard](https://photon.codes/), create/select a Beta project and create a project API key with `project:write`. Put it in `PHOTON_API_KEY` in the ignored `.env`. Register an HTTPS `/webhooks/photon` destination for `message.received`, API version `2026-07-01`; copy that destination's `whsec_...` signing secret into `PHOTON_WEBHOOK_SECRET`. These are two different secrets. See [project setup](https://photon.codes/docs/beta/get-started/project), [webhooks](https://photon.codes/docs/beta/message-delivery/webhook), and the [official API schema](https://api.photon.codes/openapi.json).

Keep these nonsecret settings in your selected TOML file:

| Setting | Value |
| --- | --- |
| `photon_project_id` | Project ID, `pho_prj_...` |
| `photon_agent_user_id` | Receiving agent User ID, `pho_usr_...` |
| `photon_allowed_sender_id` | Your enrolled human User ID, `pho_usr_...` |
| `photon_platform` | `"sms"` or `"imessage"`, matching the provisioned channel |
| `photon_api_base_url` | `"https://api.photon.codes/v1"` |
| `photon_inbox_path` | `".data/photon-inbox.sqlite3"` |
| `photon_enabled` | `true` only after configuration |

An iMessage assignment does not provision SMS. The selected channel needs its own provider setup and a fresh inbound message from the enrolled human. The backend validates signed raw bytes, timestamp freshness, event version/project, matching nested channel identifiers, direct human-to-agent messages, and the exact enrolled IDs. It persists before returning 204. Conflicting duplicate identities fail; retries of the same message deduplicate. No phone numbers or channels are provisioned by this code.

Supported commands are `/help`, `/status`, `/accounts`, `/summary YYYY-MM-DD YYYY-MM-DD`, and `/recurring YYYY-MM-DD YYYY-MM-DD`. Dates are inclusive UTC. Financial replies use deterministic integer-cent tools, preserve source/coverage limitations, and do not invoke a model. Unknown or incomplete commands return usage help. `/accounts` reports the observed checking/savings snapshot and account count; recurring replies show at most three pattern candidates.

After starting your configured backend, prepare one queued reply without sending:

```sh
docker compose --env-file .env -p princeton-langchain-20261114 exec -T backend python -m spending_api.photon_worker --once
```

To explicitly send one queued or prepared reply:

```sh
docker compose --env-file .env -p princeton-langchain-20261114 exec -T backend python -m spending_api.photon_worker --once --send
```

Retain the two catalog `-f` flags if that is your running project. These commands use the running container's configured credentials and data source. There is no background polling loop. The worker persists the exact reply, source hash, body hash, and stable project/message idempotency key before a send. Leases coordinate competing invocations. Uncertain transport/5xx/429 outcomes reuse that body/key with backoff, at most three attempts within ten minutes of preparation; exhausted outcomes remain `uncertain`. Scope changes and legacy inbox rows without a scope binding are blocked. An API 202 is acceptance, not proof of phone delivery. No indefinite exactly-once guarantee or delivery-status webhook reconciliation is claimed.

## AI agents

The selected framework is LangChain with `langchain-google-genai` for Gemini and `langchain-nvidia-ai-endpoints` for hosted NVIDIA Nemotron. Packages and transitive dependencies are locked in `uv.lock`. There is no automatic fallback between providers.

| Environment field | Setup |
| --- | --- |
| `GEMINI_API_KEY` | In [Google AI Studio](https://aistudio.google.com/api-keys), sign in and create an API key for your project. Use the Gemini Developer API key; this integration explicitly disables Vertex AI. [Google guide](https://ai.google.dev/gemini-api/docs/api-key). |
| `NVIDIA_API_KEY` | Sign in at [NVIDIA Build](https://build.nvidia.com/), open the Nemotron model page, and choose Get API Key. [NVIDIA guide](https://docs.api.nvidia.com/nim/docs/api-quickstart). |
| `APP_API_TOKEN` | Reuse your existing app bearer token or generate a new random secret locally. It is required for enabled AI, including fixture mode, and is not a vendor key. |
| `APP_SETTINGS_FILE` | Set `./settings.ai.toml` to select the AI fixture configuration. This is a file selector, not a secret. |

Keep a single active definition of each field in `.env`. If the agent appended commented placeholders to your existing file, uncomment only missing fields and fill them yourself. Never put keys in frontend variables, prompts, tracked files, or shared command output. No key is needed for credential-free verification.

The default models are `gemini-3.8-flash` and `nvidia/nemotron-3.5-lightning-30b-a3b`. Both passed an explicitly authorized live synthetic tool round trip on November 14, 2026 before the multi-agent rewrite. The new delegation framework is verified offline; a live multi-agent run has not been repeated. The older Nemotron 3 Super hosted endpoint returned 404 and its [NVIDIA model page](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/build) labels it deprecated; merely appearing in the model catalog is not proof of endpoint availability. The [Lightning model page](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/build) advertises hosted access and function calling. Account access and quota can still change. Set the model names in TOML if your account uses another supported model. Only the selected provider key is required. No billing changes are needed for offline tests.

From the repository root, start the isolated Compose project:

If the README's default-project quickstart is still using ports 5173/8765, stop that project first with `docker compose --env-file .env.example down`. This preserves its named data volume; do not add `--volumes`. Stop only the project you previously started. Then choose the named project below and retain its `-p` flag for later commands.

```sh
docker compose --env-file .env -p princeton-langchain-20261114 up --build --detach --wait
```

The usual host ports 5173 and 8765 must be free; the project name creates separate containers and a separate data volume. This command does not stop an already running project. The generated fixture is preserved across restarts within the new project.

At `http://127.0.0.1:8765/docs`, authorize with the app token, inspect `GET /api/v1/agent/status`, then invoke `POST /api/v1/agent/answer` with a JSON `question`. The backend authenticates before reading a question or loading data. Clients cannot override provider, customer, source path, credentials, or system prompt. One AI request runs per worker at a time; simultaneous requests receive 429 rather than entering an unbounded queue.

The advisor is a LangChain `create_agent` graph using Deep Agents `SubAgentMiddleware` with explicit compiled spending and optional local-research specialists. Specialists have isolated message contexts and only their domain read tools. Planning uses bounded temporary todos; no filesystem, shell, browser, arbitrary network tool, general-purpose child, persistent memory, or hidden summarizer is installed. Request-local `StateBackend` is the delegation backend.

One shared budget spans advisor and specialists: default 6 model calls, 6 tool calls (including delegation/planning), 45 seconds, 1024 output tokens per call, and a 6144-token output reservation ceiling. Each call's serialized context and tool schemas are capped at 96 KB. Output reservations are conservative bounds, not billed token counts or an input-token billing cap. Complete tool batches are validated before execution. Ambient LangSmith tracing is disabled. Actual read evidence has a source hash, tool/call ID, role, and delegation ID; activity is displayed separately. Missing required inputs produce a distinct `clarification` response rather than a factual answer without evidence.

Every response includes provider/model labels, a snapshot hash, evidence, and coverage caveats. A successful model response is not proof that its prose is correct: structured evidence remains authoritative. No conversation memory is persisted. A failed or limited run is labeled explicitly. The dashboard's **Ask your books** view uses this API and displays tool results separately from the AI interpretation. It sends no question until submitted, keeps the app token only in page memory, and clears questions/evidence on lock or dashboard refresh. Each request needs its own dates and filters; the dashboard date picker is not inherited.

For an explicit live synthetic smoke test in the configured backend, run:

```sh
docker compose --env-file .env -p princeton-langchain-20261114 exec -T backend python scripts/smoke_ai.py --live --settings /app/settings.toml --env-file /dev/null --provider gemini
```

Use `--provider nemotron` for NVIDIA. This command uses the running container's configured keys and allows at most four model requests for one advisor-to-specialist round trip. It sends one synthetic purchase, not loaded bank or property data. Output contains status, counts, and a bounded error category without credentials or raw provider errors. `--live` is mandatory; do not include it in automated offline checks. Runtime credentials/configuration are the ones loaded when that container was created.

## Local CoStar catalog

The supplied local catalog requires no vendor API key. Put the kit in ignored `costardata/`; search reads `data/apartment-listing.jsonl` and `data/commercial-property.jsonl`. Use the [catalog-only recipe](../README.md#costar-catalog-search) with `APP_SETTINGS_FILE=./settings.catalog.toml`, `APP_API_TOKEN`, and both Compose files. The override mounts the kit read-only and refuses to create a missing directory.

The direct endpoint accepts state/city, bedrooms, minimum square feet, maximum starting rent, audience selection, and display limit. It has no bank/model dependency. The UI submits seven explicit canonical fields and requires an exact echo before accepting results. It shows up to three recorded matches with full rent ranges. AI can use the same service when enabled; model-selected filters still need comparison with the original question.

The parser retains a bounded projection and source hashes until backend restart. Defaults exclude specialized housing, invalid money units, conflicting property/listing records, and missing property/floor-plan joins. Unknown dates remain unknown. Asking rents are monthly USD per unit, and the minimum-only filter is not a full-range affordability guarantee. Recorded availability is historical and the supplied Houston/Los Angeles catalog is not the whole market.

Before a timed demo, submit an authenticated `{"limit":1}` catalog request and confirm `status: "ok"` with a source hash. This prepares that process's cache without a model call. JSONL reads retain the 1 MiB buffer for Docker Desktop bind-mount performance. A new process or restart has a new cache.

## Archived spatial prototype

Matterport and Vexcel are absent from the active app. Their earlier implementation and setup notes remain on local branch `archive/spatial-prototype-20261114` at `6917d8d`. Existing private keys were preserved but are no longer loaded by application settings or passed through Compose. No new spatial credentials are needed.
