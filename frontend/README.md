# Frontend — AI Assistant for LMS

Next.js UI for the FastAPI grading backend in `../backend`. Phase 5 of the
project; sub-feature **5.1** is the scaffold plus the typed API client. No
pages/screens are built yet (5.2 onward).

## Stack and why

| Choice | Version | Reason |
| --- | --- | --- |
| Next.js, App Router, TypeScript | 16.3.4 | Current stable release, verified with `npm view next version` on 2026-09-07 rather than assumed. App Router because Pages Router is legacy. |
| React | 19.2.8 | Pulled in by `create-next-app@16.3.4`. |
| Tailwind CSS | 4.x | Confirmed as the default `create-next-app` styling option. The UI is explicitly "thin, not the focus", so utility classes beat setting up a component library or hand-rolled CSS modules. |
| Vitest | 5.0.0 | Vite-native, no separate Babel/transform config, and it resolves the `@/*` alias the same way Next does. Jest would need extra transform plumbing for the same result. |

`@types/node` was raised from the scaffold's `^20` to `^26` (matching the local
Node 26.3.0) because Vitest 5 requires `^22 \|\| >=24`. That is a real version
fix, not a `--legacy-peer-deps` workaround.

## Auth token storage (MVP decision)

The JWT is kept in `localStorage` and attached as `Authorization: Bearer` by
the fetch wrapper.

**Tradeoff, stated rather than engineered around:** `localStorage` is readable
by any script on the page, so a successful XSS could steal the token; an
httpOnly cookie could not be read by script. It is chosen anyway because the
backend authenticates with a plain Bearer JWT and has no cookie or CSRF
handling at all — switching would require backend changes, which are out of
scope for 5.1. This is a local capstone demo, not deployed software.

## Setup

```bash
cp .env.example .env.local   # adjust NEXT_PUBLIC_API_BASE_URL if needed
npm install
npm run dev                  # http://localhost:3000
```

The backend must be running separately:

```bash
cd ../backend && uvicorn app.main:app --reload --port 8000
```

`NEXT_PUBLIC_API_BASE_URL` defaults to `http://127.0.0.1:8000/api/v1`, which is
one of the origins the backend's CORS config already allows.

## API client

Everything lives in `src/lib/api/` and is re-exported from `@/lib/api`. Nothing
outside that folder should call `fetch` against the backend directly.

| File | Contents |
| --- | --- |
| `types.ts` | TypeScript mirrors of every backend Pydantic schema, plus the `/chat` response union and the grading-pipeline event types. |
| `config.ts` | Base URL resolution and `buildUrl`. |
| `token-storage.ts` | SSR-safe `localStorage` token accessors. |
| `client.ts` | `apiFetch` — auth header, body encoding, JSON parsing, `ApiError`. |
| `auth.ts` `sessions.ts` `assignments.ts` `submissions.ts` `grades.ts` `chat.ts` | One module per backend router, mirroring `backend/app/routers/`. |

Non-2xx responses and transport failures always **throw** `ApiError`
(carrying `status`, a flattened `detail`, and the parsed `body`) — a failed
call can never be mistaken for an empty one.

### Streaming `/chat/stream`

`EventSource` cannot be used: it is GET-only, carries no request body, and
cannot set headers, so it can send neither the `{"instruction": ...}` body nor
the `Authorization` header the endpoint requires. `streamChat` therefore issues
a normal `fetch` POST and reads `response.body` incrementally with a
`ReadableStream` reader, decoding `data: {json}\n\n` frames as they arrive:

```ts
for await (const event of streamChat("grade week 3 day 1")) {
  if (isChatEarlyExit(event)) console.log(event.status, event.message);
  else console.log(event.event);
}
```

## Testing

```bash
npm test            # Vitest, all mocked — never touches a real backend
npm run verify:api  # LIVE run against a real backend on NEXT_PUBLIC_API_BASE_URL
```

`npm test` covers the fetch wrapper (auth headers, query encoding, form vs.
FormData bodies, 204s, `ApiError` for 401/422/500/transport failures) and the
SSE frame parser (including an event split across two network chunks).

`npm run verify:api` uses a separate Vitest config (`vitest.verify.mts`) so a
real network call can never leak into `npm test`. It logs in with the demo
instructor, then exercises `/auth/me`, `/sessions`, `/sessions/{id}`,
`/sessions/{id}/grades`, `/sessions/{id}/submissions`, `/chat` and both
`/chat/stream` paths against the dev database. Override the credentials with
`VERIFY_INSTRUCTOR_EMAIL` / `VERIFY_INSTRUCTOR_PASSWORD`.
