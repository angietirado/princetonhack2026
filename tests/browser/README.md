# Browser regression checks

Start the credential-free application, then run the tools-profile browser service:

```sh
docker compose --env-file .env.example up --build --detach --wait
docker compose --env-file .env.example --profile tools run --build --no-deps --rm browser-tests
```

The Chromium suite checks desktop/mobile rendering, cohort placeholders, filtering,
questions, exports, import preview/commit/reload, comparison reconciliation, and
experiment budget behavior. Screenshots and the HTML report are written to the
ignored `.data/browser-artifacts` directory.

Tests temporarily select the demo and restore the originally active dataset in
`afterAll`. The import scenario also restores its previous selection in `finally`.
Its small fixture is explicitly marked synthetic, saved as a separate immutable
dataset, and reused idempotently on subsequent runs. Existing datasets are retained.
Tests run with one worker because dataset selection is shared by the local app.

Playwright and Chromium are pinned to matching 1.65.1 releases. Browser dependencies
and their lockfile are isolated here; the root dashboard test dependencies are unchanged.