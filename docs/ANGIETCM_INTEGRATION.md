# angietcm frontend integration

Source: `origin/angietcm` at `78f514c` (Build 2ndLife campus marketplace frontend). The existing marketplace was checkpointed before merging. The partner's standalone Vite app lives in `apps/frontend` so the current FastAPI, MongoDB, and Docker layout remains usable.

## Plan

1. Preserve the partner's 2ndLife brand, teal/cream stylesheet, centered mobile layout, route structure, cards, bottom navigation, and sheets.
2. Connect the screens to existing browser profiles, dev students, listings, and messages.
3. Add persistent listing types, pickup requests, repair profiles/requests, location preferences, and free-post IOU counts needed by those screens.
4. Keep domain-only `.edu` checking and external payments. Replace mock verification, ratings, escrow, walking times, and AI scan claims with accurate copy and useful controls.
5. Verify API permissions/state transitions against both test and real MongoDB, build Docker, and exercise the integrated desktop/mobile flows.

## Added API contract

Existing endpoints remain supported. Listing input/output gains `kind` (`sale`, `rent`, `free`, `urgent`, `wanted`) and `needs_repair`; existing records default to sale. Rental prices are per day. Free and request posts have zero prices. Profiles gain a campus location and IOU count.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/listings?kind=...&location=...` | Filter by transaction type and campus location |
| PATCH | `/api/me` | Update display name or campus location |
| POST | `/api/listings/{id}/orders` | Request pickup, rental, or help and create/reuse the private conversation |
| GET | `/api/orders?role=buying\|selling\|borrowing` | Participant-only activity |
| GET | `/api/orders/{id}` | Pickup details and conversation link |
| PATCH | `/api/orders/{id}` | Update pickup progress/location with role and transition checks |
| GET | `/api/repairers` | Browse student repair profiles |
| GET | `/api/repairers/me` | Read the current student's repair profile |
| POST | `/api/repairers/me` | Publish/update a self-reported repair profile |
| POST | `/api/repairers/{id}/requests` | Send a repair request |
| GET | `/api/repair-requests` | View sent/received repair requests |
| GET | `/api/repair-requests/{id}` | Read a participant's repair request |
| PATCH | `/api/repair-requests/{id}` | Accept, complete, or cancel with role checks |

Pickup states are requested, accepted, ready, completed, cancelled, and returned for rentals. These describe coordination, never an in-app payment. All development identities and related data remain controlled by `DEVMODE`.

Accepted pickups reserve one listing for one request. Sellers mark items ready; buyers confirm pickup, and rental owners confirm returns. A cancelled request or completed rental return allows a new request while retaining the previous history. Repair requests have requested, accepted, completed, and cancelled states, with private conversations shared only by the requester and repair provider.

Free-post IOU awards are recorded once when a free listing is created. Editing its kind or restarting the app does not mint extra tickets. They are a sharing counter, with no redemption or money value.

## Verification

All 60 API tests pass against mongomock and isolated real MongoDB databases. They cover concurrent request deduplication, one successful reservation among competing buyers, changed-price/type protection, rental reuse, retained history after soft deletion, repair request privacy, and disabled development mode. The real Docker HTTP smoke flow passes for profile identity, trading, messages, pickup completion, rental return, free-post tickets, location persistence, and cleanup.

Production TypeScript/Vite and Docker builds pass. Browser checks cover `.edu` entry, listing publication, private messages, pickup acceptance/readiness/completion, dev-profile switching and isolation, blocked concurrent identity changes, profile/location persistence, rental returns, free-post tickets, and repair publication/request/chat. Desktop screenshots and 320px/390px mobile layouts were inspected. The original branch stylesheet is retained as an exact 281-line prefix, followed by rules for the integrated controls.
