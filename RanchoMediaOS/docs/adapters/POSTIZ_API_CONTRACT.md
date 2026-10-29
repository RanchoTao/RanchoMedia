# Pinned Postiz public API contract

Source: unmodified `upstream/postiz` commit `528f6ea37c8cf1156ad4a12de4e7aed853c019f2` (AGPL-3.0). This wrapper is independent Rancho code and imports no Postiz implementation.

| Contract | Pinned source |
| --- | --- |
| `Authorization` raw API key or `pos_` OAuth token, organization-scoped; missing/invalid returns 401 | `apps/backend/src/services/auth/public.auth.middleware.ts:25-60` |
| `POST /public/v1/posts` and `GET /public/v1/posts` | `apps/backend/src/public-api/routes/v1/public.integrations.controller.ts:59,114-204` |
| POST body: `type`, `date`, `shortLink`, `tags`, `posts[].integration.id`, `posts[].value[].content/image`, provider `settings.__type` | `libraries/nestjs-libraries/src/dtos/posts/create.post.dto.ts:23-125` |
| GET requires `startDate` and `endDate`; returns `{posts: [...]}` | `get.posts.dto.ts:7-21`, public controller `:114-125` |
| POST result is an array of Postiz posts, and creation can start an async workflow | public controller `:204`, `posts.service.ts:927-988` |

The public POST route accepts no Rancho idempotency key. `posts.repository.ts:542-543` creates a UUID group when the caller's group is not kept; the public controller does not request `keepGroup`. GET has a date filter, not a key filter. Therefore an HTTP 2xx result confirms only Postiz's internal receipt, not external platform visibility; a timeout or 5xx after POST is ambiguous. No automatic retry is allowed after that point.

`PostizAdapter.create_draft` uses `type=draft` against an explicitly local HTTP base URL, with redirects disabled and a five-second timeout. The wrapper classifies 400 as terminal, 401/403 as auth, 429 as rate-limited, and 5xx/timeout as unknown. It can return candidate internal IDs from GET for human reconciliation. Provider-specific settings and media upload, real Postiz service behavior, external publication, visibility, platform quotas, and credential storage are unverified.
