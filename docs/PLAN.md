# Campus Exchange plan and endpoints

React + TypeScript + Vite frontend, FastAPI backend, MongoDB (PyMongo), and Docker Compose. Check that an email is syntactically valid and ends in `.edu`; no email delivery, passwords, or sign-in flow. Every browser profile can buy and sell. Payment and pickup are arranged externally between students.

## Current implementation plan
1. Add `DEVMODE` (off by default) with two reusable sample student profiles and editable listings.
2. Gate development selection, sessions, and data on the backend flag.
3. Simplify the UI around search, listings, selling, and messages, with a compact development switcher.
4. Verify normal and development flows, including switching identities and disabling the flag; rebuild the running app.

## Scope
- Public browsing, search, category/price filters, and newest/price sorting.
- A `.edu` address check before listing or messaging. This checks eligibility, not inbox ownership or enrollment.
- Both buying and selling from one profile, with no exclusive account roles.
- Listing creation/editing/removal and available/reserved/sold statuses. Optional HTTPS photo URLs and category artwork.
- One private conversation per buyer/listing. Agree on payment method and pickup in messages; no checkout, wallet, escrow, or payment processing.
- Optional labeled demo listings, which cannot receive messages.

## Identity model
An opaque `campus_session` HttpOnly, SameSite=Lax cookie identifies this browser profile and expires after seven days. Existing valid cookies retain their profile. Without a valid cookie, any eligible email creates a new profile, even when another profile has the same email. Clearing or losing the cookie means old listings/messages cannot be recovered through an email lookup. Both marketplace roles are available to every profile.

With `DEVMODE=true`, `dev_student1` and `dev_student2` are deliberately reusable fixture identities selected through dedicated development routes. Their seeded and created listings are marked internally as development data. Their profiles, data, and selections are inaccessible when the flag is false. Normal email entry never recovers a development identity. Switching profiles clears frontend conversation state and rotates the browser cookie.

## Data contract
IDs are UUID strings, timestamps UTC ISO 8601, prices integer USD cents. Errors use `{detail: string}` or FastAPI validation arrays. Pages use `{items, total, page, page_size}`.

Public profile: `{id, display_name, university, edu_eligible: true, email_verified: false}`. Private profile adds `{email, created_at, dev_username: string|null}`. Public responses never expose email.

Listing: `{id, title, description, price_cents, category, condition, pickup_location, image_url, status, seller, created_at, updated_at, is_demo}`.
Categories: `books`, `electronics`, `furniture`, `clothing`, `sports`, `other`.
Conditions: `new`, `like_new`, `good`, `fair`.
Statuses: `available`, `reserved`, `sold`.

Conversation: `{id, listing: {id, title, price_cents, image_url, status}, buyer, seller, last_message, updated_at, created_at}`. Soft-deleted listings appear as `removed` in retained conversations.
Message: `{id, conversation_id, sender_id, body, created_at}`.

| Method | Endpoint | Request / response | Access |
|---|---|---|---|
| GET | `/api/health` | `{status: "ok", database: "ok"}` | Public |
| GET | `/api/config` | `{email_policy: "edu", verification_mode: "domain_only", demo_data, devmode}` | Public |
| GET | `/api/dev/students` | `{items: [{username, id, display_name, email}]}` | DEVMODE only |
| POST | `/api/dev/session` | `{username}` -> private fixture profile and browser cookie | DEVMODE only; named allowlist |
| POST | `/api/student/verify` | `{email, display_name?}` -> private profile, remembers browser | Public, rate limited |
| DELETE | `/api/student/session` | 204, clears browser profile session | Current cookie |
| GET | `/api/me` | Private profile | Browser profile |
| PATCH | `/api/me` | `{display_name}` -> private profile | Browser profile |
| GET | `/api/listings` | `q?, category?, min_price?, max_price?` (cents), `sort=newest|price_asc|price_desc`, `page=1`, `page_size=24` -> listings page | Public |
| POST | `/api/listings` | Listing fields excluding server fields -> listing, 201 | Browser profile |
| GET | `/api/listings/{id}` | Listing, including sold | Public |
| PATCH | `/api/listings/{id}` | Partial fields/status -> listing | Owner profile |
| DELETE | `/api/listings/{id}` | 204; soft-delete, retain conversations | Owner profile |
| GET | `/api/me/listings` | Own listings page | Browser profile |
| POST | `/api/listings/{id}/conversations` | `{message}` -> conversation, 201 new / 200 existing | Non-owner profile, available real listing |
| GET | `/api/conversations` | Own conversations page | Browser profile |
| GET | `/api/conversations/{id}/messages` | Chronological message page; default page_size=100 | Participant profile |
| POST | `/api/conversations/{id}/messages` | `{body}` -> message, 201 | Participant profile |

MongoDB collections: users (emails are not unique identities), sessions (hashed token/expiry), listings, conversations (unique listing_id + buyer_id), messages, rate_limits. Existing marketplace data is retained. TTL indexes clean up sessions/rate limits, with explicit expiration checks for access.

## Validation
Test email domains, same-email browser isolation, profile expiry/clearing, listing ownership, message privacy, filters/statuses, and the same person buying and selling. Run tests against mongomock and real MongoDB, production Docker builds, HTTP smoke flow, and desktop/mobile browser checks.

API checks: 41 tests pass against mongomock and real MongoDB, including eight development-mode tests for switching, data isolation, disabled-mode access, and fixture persistence. Docker and browser checks cover the regular marketplace flow plus development switching, own-listing isolation, reload persistence, and desktop/mobile layout. Local `.env` enables development accounts; `.env.example` keeps them disabled by default.

## Later improvements
Photo uploads, reporting/moderation, saved listings, university filters, and recoverable verified identities. Payment remains external.
