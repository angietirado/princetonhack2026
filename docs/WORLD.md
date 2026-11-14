# One fixed Houston shopping world

The three new scenarios use one shared, explicitly fictional store world. Corner Market,
Grove Grocer, Pantry & Co., Value Foods and Fresh Stop have identical coordinates, IDs,
prices and shopping times in every scenario. Moving the starting home changes travel;
the stores no longer move with it. The basket, travel assumptions, $65 shopping allowance,
45-minute limit, driving mode and two-store maximum are identical at the start.

## Recorded housing facts

The selected records in the local CoStar residential-listings kit are public-display
(`permissionLevels.homes = 10`), USD (`monetaryUnit = 1`), rentals
(`transactionType = 2`), and Active in the supplied snapshot (`listingStatus = 1`).
The generator also accepts Contract status 2, with the same historical-availability caveat.

| Scenario | Source listing | Recorded address | Asking rent/month | Bedrooms | Area |
|---|---|---|---:|---:|---:|
| houston-rental-west | 240486874 | 1010 Rosine Street, unit 49 | $2,300 | 2 | 1,300 sq ft |
| houston-rental-central | 232112148 | 732 Ruthven Street | $3,200 | 2 | 2,597 sq ft |
| houston-rental-east | 238679692 | 2323 Polk Street, unit 205 | $2,499 | 2 | 1,312 sq ft |

Rent comes from `currentPrice` on rental records and is converted exactly to
`home.monthly_rent_cents`. Bedrooms come from `property.bedsMin/bedsMax`; area from
`property.sqftMin/sqftMax`; unit labels from `property.address.unitNumber`.
Listing and days-on-market dates retain their source meaning. Neither is presented
as a current verification timestamp. The source's complete snapshot date is unknown.

These homes differ in size and other features; their rents are not a controlled
like-for-like valuation. Current availability, additional fees and utilities are
unverified. Unknown fees and all-in housing cost remain null, never zero.
Rent is context, not evidence of resident income or a reason to assign a shopping budget.

## Simulation assumptions

The five stores and their locations are fictional. Their prices are fixed estimates,
not observed local prices. Travel uses distance times a road factor and an assumed
speed; parking, shopping and returning home are included. This does not establish
pedestrian access, road routes, traffic, transit service or real savings.

Every scenario includes `world_id = houston-inner-loop` and a common `world_version`,
a SHA-256 of the canonical shared basket, store and travel definition. Each scenario
also has its own version hash, and the home has a fingerprint of the public source
projection used. Store changes change the world version; home changes do not.

New scenario IDs prevent comparison with the earlier home-centered layouts.
Existing saved sessions keep their immutable snapshots. Their data is not migrated
or reinterpreted using the new geography.

## Rebuild and verify

The full CoStar kit remains ignored. To regenerate from it with Docker, mount only
the needed source directory and output directory into a one-off backend container:

```powershell
docker compose --env-file .env.example run --rm --no-deps --volume ./costardata/data:/source:ro --volume ./data:/output backend python scripts/build_scenarios.py --source /source/residential-listings.jsonl --output /output/scenarios.json
docker compose --env-file .env.example --profile tools run --build --rm backend-tests
```

Build the backend image first when the generator code has changed. Regeneration is
deterministic and independent of source row order. Missing selected IDs, duplicate
selected records, non-public records, sales, stale/unknown statuses, unknown currency,
invalid rent and invalid geography cause failure; another property is never substituted.
The output is replaced atomically only after all selected records have been validated.
A source or write failure preserves the previous catalog.
