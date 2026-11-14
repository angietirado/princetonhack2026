# Integration setup

Use `.env.example` as the configuration reference. Start all Python commands from the repository root so `.env` and relative fixture paths resolve consistently. Keep keys out of the React app and Git.

## Nessie v2

Create or obtain a Nessie v2 API key from the hackathon's [Nessie v2 portal](https://v2.nessieisreal.com/). Set `NESSIE_API_KEY` in `.env`. The correct base URL is `https://v2-prod-api.nessieisreal.com`; requests use `Authorization: Bearer ...`, not a query-string key. [Official schema](https://v2.nessieisreal.com/openapi.yaml)

Preview a historical simulation request:

```powershell
.\scripts\uv.ps1 run --package spending-data-generator generate-data --target nessie --seed 42 --days 90 --transactions 300
```

Create the synthetic dataset only when ready:

```powershell
.\scripts\uv.ps1 run --package spending-data-generator generate-data --target nessie --seed 42 --days 90 --transactions 300 --apply
```

This writes to the Nessie sandbox and saves the response in `.data/nessie-run.json`. Pick the intended customer from `customer_ids`, then set `NESSIE_CUSTOMER_ID` and `DATA_SOURCE=nessie`. Set `APP_API_TOKEN` to a strong local secret as well; live mode refuses to start without all three settings. Restart the backend and call its protected routes with that bearer token.

The generator returns the run state as supplied by Nessie. If it is still running, check `GET /simulations/{id}` before analyzing. A repeated `--apply` is a new creation attempt, not an idempotent refresh. After a timeout, inspect the provider dashboard before retrying. The local `--end-date` option does not control Nessie's dates.

The minimal React page is configured for fixture mode. It does not store or prompt for the temporary app bearer token. Use the API docs' **Authorize** button to check live data until the frontend teammate implements the real authentication flow.

## Photon 2.0 Beta

Use the [Photon Beta project setup](https://photon.codes/docs/beta/get-started/project) and [Photon messaging documentation](https://photon.codes/docs/beta/messaging/index). Configure an SMS-capable number and the SMS setup required for that number. An assigned iMessage line is a different channel; it does not establish that SMS works.

Record these server-side values:

| Variable | Purpose |
| --- | --- |
| `PHOTON_PROJECT_ID` | `pho_prj_...` ID from the project dashboard |
| `PHOTON_API_KEY` | Project bearer key for the future outbound worker |
| `PHOTON_AGENT_USER_ID` | The project's agent User ID on the selected channel |
| `PHOTON_ALLOWED_SENDER_ID` | The explicitly enrolled human User ID for this demo |
| `PHOTON_WEBHOOK_SECRET` | The destination's `whsec_...` signing secret |
| `PHOTON_INBOX_PATH` | Durable SQLite file, default `.data/photon-inbox.sqlite3` |

Expose the backend through a public HTTPS tunnel or deployment. Add `/webhooks/photon` as a destination and subscribe to `message.received` using the payload API version from the project's event catalog. Save the destination secret in `.env`; it is different from the API key. The current receiver expects the message at `data.object`, with `sentBy`, `receivedBy`, `space`, and plain-text `content`. Confirm the sample event for the selected version matches that boundary. [Photon webhook setup](https://photon.codes/docs/beta/message-delivery/webhook)

The receiver verifies signatures over the raw bytes, enforces timestamp freshness through Standard Webhooks, restricts the sender and agent, ignores group chats and bot echoes, and persists an event only once. A `204` confirms receipt. The receiver **does not send an SMS reply**; implement the worker described in the design before expecting a phone conversation.

`spending_api.photon.send_reply()` implements the outbound transport against the 2.0 API with a stable key derived from the incoming message ID. The future worker must persist the exact reply body before sending so a retry uses the same key and body. Track provider acceptance and actual delivery separately. [Photon message model](https://photon.codes/blog/introducing-photon-2.0)

No phone numbers were provisioned and no real messages were sent as part of the scaffold setup.

## AI agents

Select a model/runtime in the design decision log before adding an SDK or provider credentials. Wrap `SpendingTools` methods as structured tools using their typed inputs and outputs, with the customer's snapshot supplied by server context. Add a bounded worker loop and persist conversation state alongside the inbox/outbox.

The current scaffold contains calculations and transport boundaries, not a simulated AI conversation. The browser and webhook tests run without any LLM API key.
