# Keep a reviewed pilot record

An experiment draft is a decision record. Its calculation describes an illustrative offer/holdout split and maximum offer cost; saving it does not establish that a pilot ran or that it worked.

## Review and save

1. Open Experiments and choose a suggested idea or start your own pilot.
2. Enter a title, hypothesis and measurement plan. Set the customer group, audience cap, offer, budget and equal evaluation window.
3. Calculate the pilot. Review the available audience, split, maximum offer cost, feasibility and limitations.
4. Save the reviewed draft. It remains available in Saved drafts after a reload, with the original source and dates.

Changing numeric assumptions or the observed source requires a fresh calculation. Saved records retain the requested audience cap as well as the smaller available audience, if applicable. A missing API budget remains unspecified, and an infeasible calculation keeps its warnings when saved; saving is not approval.

## Revisit and record observations

Open a saved draft to read its original hypothesis, measure, source, assumptions and calculation. Drafts from another dataset or older revision remain explicitly identified. Viewing one does not switch the active dataset.

Add a dated owner observation to record what happened, a practical obstacle, or a reason to revise the idea. These notes are append-only and owner-reported. They do not verify customer assignment, attendance, consent, causality, uplift or return on investment. Notes are stored locally and are not supplied to the AI investigator.

If assumptions change, calculate and save another draft. The original record remains intact. Repeating an identical save or identical note returns the existing record instead of creating duplicates.

## Persistence and evidence

Records share the project's existing Docker `owner-data` volume, using additive tables in `owner-datasets.sqlite3`. They do not modify the imported datasets or active selection. Ordinary container rebuilds and `docker compose --env-file .env.example down` preserve the volume; `down --volumes` removes it.

The server recomputes each new saved calculation from the selected immutable dataset, checks the reviewed source and calculation hash, and stores the resulting evidence. It does not accept browser-supplied totals. Exact retries can recover an already saved record even if another tab has since changed the active source.

The workspace is intended for a single owner or authorized local user. Saved drafts contain aggregate counts and source metadata rather than an assigned customer roster. Use business observations rather than personal contact information in free-text notes.

## API

- `POST /api/v1/owner/experiments/plan` returns the calculation, exact normalized inputs, dataset identity and reviewed calculation hash.
- `POST /api/v1/owner/pilots` saves a reviewed draft with expected dataset/revision, reviewed hash, title, hypothesis, measure and explicit calculation inputs.
- `GET /api/v1/owner/pilots` returns bounded summaries, optionally filtered by dataset.
- `GET /api/v1/owner/pilots/{id}` reads a saved record independently of the active source.
- `POST /api/v1/owner/pilots/{id}/notes` adds a dated owner-reported observation.

The same owner authorization protects these endpoints. The status response identifies whether local persistence is configured. A mismatched source or calculation produces a conflict requiring a fresh review; local storage failures remain visible rather than being presented as successful saves.
