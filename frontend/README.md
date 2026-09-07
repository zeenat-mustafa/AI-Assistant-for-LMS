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

## Routes and auth (5.2)

| Route | Who | Notes |
| --- | --- | --- |
| `/` | anyone | Redirects to the caller's role home, or `/login`. |
| `/login` | anyone | One form for both roles — the backend has no role field on login. |
| `/register` | anyone | Student self-registration. `POST /auth/register` always creates a `student`, so there is deliberately no instructor sign-up. |
| `/instructor` | instructors | Create a session; list the ones you own (5.3). |
| `/instructor/sessions/[id]` | instructors | Assignment files (5.3), plus the grade roster and grading chat (5.4). |
| `/student` | students | Every session in the system, with an enrolment caveat (5.5). |
| `/student/sessions/[id]` | students | Assignment downloads and own submission status (5.5). |

The dynamic route's `page.tsx` is a Server Component whose only job is to
`await props.params` (typed with the generated `PageProps<'/instructor/sessions/[id]'>`)
and hand the numeric id to a client component — the guard and all data
fetching need the browser, but parsing the segment does not.

### Two backend behaviours the UI has to work around

- **`GET /sessions` has no owner filter.** It is literally "List all
  sessions" and is open to any authenticated user, so the dashboard narrows
  to `instructor_id === user.id` on the client. That narrowing is applied to
  one page of results (`limit=200`, the backend maximum), which is correct at
  demo scale but would drop sessions past the first page if the data ever
  grew. `SessionList.total` counts every instructor's sessions, so it is
  deliberately never displayed.
- **Assignment upload is atomic per batch.** The backend checks every
  filename in the request before writing anything, so one duplicate name
  rejects the whole upload — including files that would otherwise have been
  fine. The error message says "nothing was uploaded" for that reason.
- **The chat endpoints cannot be scoped by the caller.** `POST /chat` and
  `/chat/stream` take only `{"instruction": string}`; Phase 3 resolves the
  session by matching the instruction *text*. The embedded panel therefore
  pre-fills the input with this session's title and sends it verbatim — it
  never rewrites what the instructor typed, so Phase 3's deliberate
  never-guess-on-ambiguity behaviour stays intact.
- **`combined_score` is 0.0, not null, for an ungraded submitter.** It is
  null only when the session has no assignment files at all. The roster
  checks `per_file.length` to tell "not graded yet" from a genuine zero.
- **The grade report omits students with zero submissions**, and the API
  exposes no class roster to cross-reference against, so the table says so
  in a footnote instead of implying it is the full class.
- **There is no enrolment concept at all**, and `GET /sessions` is open to
  any authenticated user, so the student dashboard lists *every* session
  with a visible note saying so. Filtering by "has a submission" was
  rejected: the seeded demo student has none, so it would render an empty
  dashboard with no route to any session.
- **`GET /sessions/{id}/submissions/mine` answers 200 with a `null` body**
  when nothing has been submitted — it is not a 404. That is the normal
  "not submitted yet" case and must never render as an error.
- **Re-uploading a submission REPLACES the previous one and deletes its
  grades.** `upload_submission` deletes the old `Submission` and its
  `SubmissionFile` rows; `Grade` cascades from `SubmissionFile` both via
  the ORM relationship and the FK. This is not the assignment side's
  atomic-409 behaviour. 5.6's upload UI has to warn about it.

**Guarding is client-side, per page, via `<RequireAuth>`.** Next 16 renamed
Middleware to `proxy.ts`, but it runs on the server and can only read
cookies — our JWT is in `localStorage`, so it is invisible there, and the
Next docs limit Proxy to optimistic checks rather than real authorization.
Layout-level checks are also ruled out by the docs: because of Partial
Rendering, layouts do not re-render on navigation and cannot stop a route
segment from rendering. The docs' guidance is to check close to the
conditionally-rendered component, which is what `RequireAuth` does.

The guard verifies the session with a real `GET /auth/me` rather than
trusting that a token string exists — an expired token, or one from a reset
dev database, resolves to anonymous and is cleared.

**This guard is UX, not security.** Enforcement stays in the backend, where
every protected endpoint already requires the Bearer token.

## Testing

```bash
npm test            # Vitest, all mocked — never touches a real backend
npm run verify:api  # LIVE run against a real backend on NEXT_PUBLIC_API_BASE_URL
```

`npm test` runs two Vitest projects: `unit` (node environment, the API
client) and `dom` (jsdom + Testing Library, the React forms and the route
guard). Vitest 5 removed `environmentMatchGlobs`, so `projects` in
`vitest.config.mts` is what splits them.

`npm test` covers the fetch wrapper (auth headers, query encoding, form vs.
FormData bodies, 204s, `ApiError` for 401/422/500/transport failures) and the
SSE frame parser (including an event split across two network chunks).

`npm run verify:api` uses a separate Vitest config (`vitest.verify.mts`) so a
real network call can never leak into `npm test`. It logs in with the demo
instructor, then exercises `/auth/me`, `/sessions`, `/sessions/{id}`,
`/sessions/{id}/grades`, `/sessions/{id}/submissions`, `/chat` and both
`/chat/stream` paths against the dev database. Override the credentials with
`VERIFY_INSTRUCTOR_EMAIL` / `VERIFY_INSTRUCTOR_PASSWORD`.
