# Nessie merchant sandbox imports

This optional importer lets the owner dashboard analyze a selected merchant's synthetic Nessie purchases across multiple simulated customers. It is a sandbox demonstration of the Capital One integration, not a connection to a real business's POS system or permission to inspect real customers' banking.

The adapter uses the [official Nessie v2 public schema](https://v2-prod-api.nessieisreal.com/openapi.json), inspected November 14, 2026. Offline tests exercise the documented response shapes. No credentialed capture has been verified.

## Docker commands

The default application and all automated checks remain credential-free. Inspect the importer without making a request:

    docker compose --env-file .env.example --profile tools run --build --rm nessie-import --help

An operator who wants a real read of their Nessie sandbox can use their existing ignored credential file. Set NESSIE_API_KEY there, then supply a known merchant ID and explicit UTC coverage dates. This command intentionally reads .env; agents must not use it for routine verification.

    docker compose --env-file .env --profile tools run --build --rm nessie-import --merchant-id YOUR_SANDBOX_MERCHANT_ID --coverage-start 2026-10-01 --coverage-end 2026-11-13

The default is a preview: it prints a content hash, selected-merchant counts, integer-cent totals, date coverage and limitations. It does not create a dataset or change the dashboard selection.

To save the snapshot fetched by that invocation, add --save:

    docker compose --env-file .env --profile tools run --build --rm nessie-import --merchant-id YOUR_SANDBOX_MERCHANT_ID --coverage-start 2026-10-01 --coverage-end 2026-11-13 --save

Saving adds an immutable dataset to the shared owner-data volume and preserves the currently selected dataset. Repeated identical captures deduplicate by content hash. Choose the saved dataset in the dashboard, or add --select to the save command to select it explicitly. --select is rejected unless --save is also present.

Each invocation fetches one fresh snapshot. A later --save invocation does not promise to reproduce an earlier preview: the provider may have changed. The actual save validates the content hash of the snapshot fetched in that same invocation. The CLI never automatically overwrites an existing dataset.

## Data contract

The API has no merchant-scoped transaction-list route. The importer performs only these GET reads, against the fixed HTTPS host v2-prod-api.nessieisreal.com:

1. /merchants/{explicit merchant ID} confirms the returned merchant identity.
2. /accounts is cursor-paginated to map payer accounts to customer IDs.
3. /transactions is cursor-paginated, then restricted to the chosen merchant and coverage.

The API's tenant-wide lists may contain unrelated records. Those records remain transient inputs and are not persisted, exported, sent to a model, or added to the dashboard dataset. No customer-details endpoint is needed. API ownership of a sandbox does not establish business ownership or customer consent in a production system.

Only completed purchase transactions become orders. A customer's multiple accounts map to the same customer pseudonym. Stored customer, order and refund IDs are deterministic SHA-256 pseudonyms scoped to the selected merchant, so raw account identifiers and unrelated transaction descriptions are discarded. Pseudonyms are not a guarantee of anonymity.

Completed refunds require a valid completed purchase parent at the same merchant, matching the original payer account, consistent merchant fields, and a timestamp no earlier than the purchase. Multiple refunds cannot exceed the original purchase amount. A refund within requested coverage whose purchase is older than that coverage causes a clear failure asking the operator to extend the start date; dates are never expanded silently.

Nessie supports forced refunds without a parent. An unlinked refund with an explicit selected merchant fails import, because attribution is incomplete. A refund with neither an attributable parent nor an explicit selected merchant is excluded; the importer does not guess where it belongs. Chargebacks and all non-purchase/non-refund transaction kinds are excluded and remain an explicit coverage limitation.

Money stays in integer USD cents. Values must be positive integers no greater than 9,999,999,999 cents. Boolean, fractional and string amounts are rejected. Dates come from created_at converted to UTC; they do not establish settlement dates or actual POS visit times.

Every saved dataset has synthetic=true, a label naming its selected Nessie merchant, and the business label "Nessie sandbox merchant". Requested coverage and the dataset's as_of are analysis boundaries, not evidence that the provider contains complete history. The API does not provide an atomic snapshot across the three reads, so concurrent sandbox changes can still affect a capture.

## Limits and failures

The importer requests 100 records per page and allows at most 100 pages and 10,000 raw rows per collection. The resulting merchant dataset is capped at 5,000 orders plus refunds, with at most 731 inclusive coverage days between 2000 and 2100. Individual JSON responses are capped at 1 MB, total response bodies at 12 MB, and the overall read at 60 seconds.

Repeated cursors, incomplete pagination, conflicting duplicate records, invalid identities, malformed JSON, excessive responses, missing accounts and inconsistent refunds fail without returning a partial dataset. Identical repeated page records are deduplicated. Provider error bodies and credentials are never printed. Redirects are not followed, environment proxy settings are not inherited, and no alternate base URL is accepted.

The adapter never creates customers, purchases or simulations, settles transactions, changes balances, or sends messages. Generated records and existing application volumes are preserved.

## Verification and outstanding evidence

    docker compose --env-file .env.example --profile tools run --build --rm backend-tests /app/.venv/bin/pytest -q -p no:cacheprovider tests/test_merchant_nessie.py

The tests use httpx.MockTransport and synthetic fixtures. They cover scope, multi-account identity, exact totals, refund attribution and chronology, duplicate pages, UTC boundaries, read-only requests, fixed host/authentication headers, pagination failure, safe errors, and preview/save/select behavior in temporary SQLite storage.

Live authentication, real returned refund payer-field behavior, access to a useful multi-customer merchant scenario, concurrent provider pagination consistency, and credentialed latency remain unverified. The official schema supports the implemented field interpretation; the importer fails rather than guessing when the expected relationship is absent.
