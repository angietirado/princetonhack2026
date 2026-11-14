# Integration setup

Use `settings.example.toml` for non-secret options and `.env.example` for credentials. Start native Python commands from the repository root so both files and relative fixture paths resolve consistently. Keep keys out of the React app and Git.

Keep credentials in the root `.env` and public options in `settings.toml`. The backend reads public options from TOML; those values take priority over obsolete public options left in `.env`. Secrets are loaded from process environment or `.env`, with process environment taking precedence. The public settings file rejects credential fields. Docker mounts it read-only and passes only the relevant credentials to each Python service.

`.env.example` sets `APP_SETTINGS_FILE=./settings.example.toml` for offline checks. Omit that selector from your private `.env` to use `settings.toml`. Native commands also accept `APP_SETTINGS_FILE` in the process environment; they default to `settings.toml`.

After editing settings, run `docker compose --env-file .env up --build --detach --wait` to recreate affected services. Validate shared configuration with `docker compose --env-file .env.example config --quiet`; this uses the public template instead of your private `.env`. Gemini and NVIDIA credentials are passed only to the backend when the optional AI assistant is enabled.

## Nessie v2

Create or obtain a Nessie v2 API key from the hackathon's [Nessie v2 portal](https://v2.nessieisreal.com/). Set `NESSIE_API_KEY` in `.env`. Set `nessie_base_url` in `settings.toml`; the API host is `https://v2-prod-api.nessieisreal.com`. Requests use `Authorization: Bearer ...`. [Official schema](https://v2-prod-api.nessieisreal.com/openapi.json)

The current MVP pins `cust_01m7cen22b7daz0s8gkcp3mt96`, with two accounts, from historical run `sim_01m7cem2mmfn4d36mdnecdtzqc`. That run created 300 transactions. Reuse it; the application never reseeds on startup. The ID is a resource identifier, not a credential. A different Nessie key cannot access this customer's data.

Preview a historical simulation request:

```powershell
.\scripts\uv.ps1 run --package spending-data-generator generate-data --target nessie --seed 42 --days 90 --transactions 300
```

Create the synthetic dataset only when ready:

```powershell
.\scripts\uv.ps1 run --package spending-data-generator generate-data --target nessie --seed 42 --days 90 --transactions 300 --apply
```

For a deliberate replacement dataset, this writes to the Nessie sandbox and saves the response in `.data/nessie-run.json`. Wait for completion, then put the chosen returned ID in `nessie_customer_id` and set `data_source = "nessie"` in `settings.toml`. `NESSIE_API_KEY` and `APP_API_TOKEN` remain in `.env`; live mode requires both credentials and a pinned customer. Restart the backend and call protected routes with the app bearer token. The existing token does not need regeneration.

The generator returns the run state as supplied by Nessie. If it is still running, check `GET /simulations/{id}` before analyzing. A repeated `--apply` is a new creation attempt, not an idempotent refresh. After a timeout, inspect the provider dashboard before retrying. The local `--end-date` option does not control Nessie's dates.

The default Ledgerly dashboard now prompts for `APP_API_TOKEN` when the backend requires it. The token stays in page memory and is cleared on lock or reload. All charts and tables use `/api/v1/dashboard` and the customer pinned in TOML. The API docs also support the **Authorize** button. The separate React scaffold is not the Docker-served dashboard.

## Photon 2.0 Beta

Photon is deferred for the MVP. Leave `photon_enabled = false` in `settings.toml`; no Photon credentials or identifiers are required for the terminal workflow. The setup below is for the later phone milestone.

Use the [Photon Beta project setup](https://photon.codes/docs/beta/get-started/project) and [Photon messaging documentation](https://photon.codes/docs/beta/messaging/index). Configure an SMS-capable number and the SMS setup required for that number. An assigned iMessage line is a different channel; it does not establish that SMS works.

Enable `photon_enabled` when ready. Keep the API key and webhook secret in `.env`; keep the other options in `settings.toml` using their lowercase names:

| Setting | Purpose |
| --- | --- |
| `photon_project_id` | `pho_prj_...` ID from the project dashboard; `settings.toml` |
| `PHOTON_API_KEY` | Project bearer key for the future outbound worker |
| `photon_agent_user_id` | The project's agent User ID on the selected channel; `settings.toml` |
| `photon_allowed_sender_id` | The explicitly enrolled human User ID for this demo; `settings.toml` |
| `PHOTON_WEBHOOK_SECRET` | The destination's `whsec_...` signing secret |
| `photon_inbox_path` | Durable SQLite file, default `.data/photon-inbox.sqlite3`; `settings.toml` |

Expose the backend through a public HTTPS tunnel or deployment. Add `/webhooks/photon` as a destination and subscribe to `message.received` using the payload API version from the project's event catalog. Save the destination secret in `.env`; it is different from the API key. The current receiver expects the message at `data.object`, with `sentBy`, `receivedBy`, `space`, and plain-text `content`. Confirm the sample event for the selected version matches that boundary. [Photon webhook setup](https://photon.codes/docs/beta/message-delivery/webhook)

When enabled, the receiver verifies signatures over the raw bytes, enforces timestamp freshness through Standard Webhooks, restricts the sender and agent, ignores group chats and bot echoes, and persists an event only once. A `204` confirms receipt. The receiver **does not send an SMS reply**; implement the worker described in the design before expecting a phone conversation.

`spending_api.photon.send_reply()` implements the outbound transport against the 2.0 API with a stable key derived from the incoming message ID. The future worker must persist the exact reply body before sending so a retry uses the same key and body. Track provider acceptance and actual delivery separately. [Photon message model](https://photon.codes/blog/introducing-photon-2.0)

No phone numbers were provisioned and no real messages were sent as part of the scaffold setup.

## AI agents

The selected framework is LangChain with `langchain-google-genai` for Gemini and `langchain-nvidia-ai-endpoints` for hosted NVIDIA Nemotron. Packages and transitive dependencies are locked in `uv.lock`. There is no automatic fallback between providers.

| Environment field | Setup |
| --- | --- |
| `GEMINI_API_KEY` | In [Google AI Studio](https://aistudio.google.com/api-keys), sign in and create an API key for your project. Use the Gemini Developer API key; this integration explicitly disables Vertex AI. [Google guide](https://ai.google.dev/gemini-api/docs/api-key). |
| `NVIDIA_API_KEY` | Sign in at [NVIDIA Build](https://build.nvidia.com/), open the Nemotron model page, and choose Get API Key. [NVIDIA guide](https://docs.api.nvidia.com/nim/docs/api-quickstart). |
| `APP_API_TOKEN` | Reuse your existing app bearer token or generate a new random secret locally. It is required for enabled AI, including fixture mode, and is not a vendor key. |
| `APP_SETTINGS_FILE` | Set `./settings.ai.toml` to select the AI fixture configuration. This is a file selector, not a secret. |

Keep a single active definition of each field in `.env`. If the agent appended commented placeholders to your existing file, uncomment only missing fields and fill them yourself. Never put keys in frontend variables, prompts, tracked files, or shared command output. No key is needed for credential-free verification.

The initial models are `gemini-3.8-flash` and `nvidia/nemotron-3-super-120b-a12b`. These identifiers were checked against official provider documentation; account access, quota, cost, and live tool calling still require a credentialed smoke test. Set the model names in TOML if your account uses another supported model. Only the selected provider key is required. Do not enable billing or buy access solely to run offline tests.

From the repository root, start the isolated Compose project:

```sh
docker compose --env-file .env -p princeton-langchain-20261114 up --build --detach --wait
```

The usual host ports 5173 and 8765 must be free; the project name creates separate containers and a separate data volume. This command does not stop an already running project. The generated fixture is preserved across restarts within the new project.

At `http://127.0.0.1:8765/docs`, authorize with the app token, inspect `GET /api/v1/agent/status`, then invoke `POST /api/v1/agent/answer` with a JSON `question`. The backend authenticates before reading a question or loading data. Clients cannot override provider, customer, source path, credentials, or system prompt. One AI request runs per worker at a time; simultaneous requests receive 429 rather than entering an unbounded queue.

The sequential LangChain loop offers structured spending-summary, transaction, and recurring-candidate tools. Default limits are 4 model turns, 6 tool calls, 45 seconds, and 1024 output tokens per model call. Tools use integer cents and return bounded evidence. Oversized or malformed tool requests fail closed. Ambient LangSmith tracing is disabled for the request. No tool transfers money, cancels services, sends messages, or executes code.

Every response includes provider/model labels, a snapshot hash, evidence, and coverage caveats. A successful model response is not proof that its prose is correct: structured evidence remains authoritative. No conversation memory is persisted. A failed or limited run is labeled explicitly. The API is not yet connected to the dashboard or Photon worker.

## CoStar spatial credentials

The supplied local CoStar catalog requires no API key. Its Matterport model references are metadata, not evidence that an interior was inspected, and recorded availability is not live availability.

For later live Matterport access, [Matterport API setup](https://developers.matterport.com/api/) uses **Settings > Developer Tools > API Token Management**. Map the Token ID to `MATTERPORT_API_KEY` and the Token Secret to `MATTERPORT_API_TOKEN`. The separate `MATTERPORT_SDK_KEY` is for an interactive browser SDK; an API token does not replace it. A personal key may not grant access to sponsor-owned models; use the event's provided access instructions.

`VEXCEL_API_KEY` is reserved for later aerial imagery integration. Do not invent a key or assume a personal account can access the event dataset. Consult the CoStar track's pinned Quick Start and organizers for sponsored Matterport/Vexcel credentials. These spatial credential fields are not used by the local catalog implementation. Photon remains deferred and its credentials are not needed for this milestone.
