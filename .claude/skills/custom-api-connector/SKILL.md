---
name: custom-api-connector
description: Build or fix an Erathos custom API connector — the folder of YAML files (`_default.yml`, one file per endpoint, `custom_field_endpoints/`) defined by the erathos-data/custom-api-connector template. Use this whenever someone wants to pull data from an API into Erathos, add/repair endpoints on an existing custom connector, set up pagination, incremental sync (cursors), OAuth2 or API-key auth for a connector, or when `validate_connector.py` is reporting errors. Trigger it for phrasings like "connect <some API> to Erathos", "sync <API> data", "add an endpoint to my connector", "why is my connector returning no rows", or any editing of `_default.yml` / connector endpoint YAML / `response.path` / `primary_key` — even if the words "custom connector" or "YAML" never come up.
---

# Erathos custom API connector

## What you are producing

A connector is a folder of YAML at the **root of a Git repository** that Erathos watches. No Python. Every push rebuilds the connector image.

```
<repo root>/
  _default.yml                     # connector-wide: name, auth, url_base, headers, validation
  orders.yml                       # one file per endpoint — filename IS the endpoint name
  customers.yml
  custom_field_endpoints/
    company_properties.yml         # only for APIs with per-account dynamic fields
  validate_connector.py            # ships with the template; keep it
```

There is no `name:` key inside an endpoint file. `orders.yml` defines an endpoint called `orders`, and that is the name other files reference in `dependency.endpoint`. Renaming the file renames the endpoint.

## The thing that actually makes this hard

`validate_connector.py` checks **shape, not truth**. It will happily pass a connector where `response.path` points at a key the API never returns and every field name is invented from the docs. That connector validates clean, builds fine, and then syncs zero rows — or silently drops columns — and nobody finds out until a user complains.

So the real work in this skill is not writing YAML. It is *establishing what the API actually returns*, then transcribing it. Treat every value you write into `response.path`, `fields[].name`, `pagination.path`, and `primary_key` as a claim about a real HTTP response you have looked at. Everything else follows mechanically.

A corollary: API documentation is evidence, not proof. Docs routinely omit an envelope key, rename a field, or describe a pagination scheme the endpoint outgrew two versions ago. Where docs and a real response disagree, the response wins.

## Step 1 — Start from the template

Work inside a clone of the template repo, or the user's existing connector repo:

```bash
gh repo clone erathos-data/custom-api-connector
```

If there's no network access, ask the user for a local path rather than reconstructing the template from memory — `validate_connector.py` is the schema authority and you want the current copy of it.

If the user already has a connector repo (adding an endpoint, fixing a sync), work in theirs. Read the existing `_default.yml` and a couple of endpoint files first — match their conventions for naming, header style, and timestamp formats instead of introducing a second style.

## Step 2 — Interrogate the API before writing YAML

Write the answers down (a scratch file is fine). You will use every one of them, and the gaps are where connectors go wrong. Ask the user directly for anything you can't determine.

**Connector-wide**
- Base URL. Is any part of it per-tenant (a subdomain, an account ID)? That part becomes an `authentication` entry of `type: variable` referenced as `<name>` inside `url_base`.
- How are credentials sent — header, query param, OAuth2 authorization-code flow?
- Is there a cheap "who am I" endpoint (`/me`, `/whoami`, `/account`) to validate stored credentials before a sync starts?
- Where does the user *get* each credential — which page of the provider's console issues it, what the value looks like, whether it's admin-only or gated behind a plan? Note the URL while the docs are open in front of you; reconstructing this from memory in Step 9 is how invented console paths get shipped.

**Per endpoint**
- Method and path.
- Query params, JSON body, or GraphQL query — which one does it take?
- **Where in the response body does the array of records live?** This becomes `response.path`.
- What uniquely identifies a record? → `primary_key`.
- How do you get page 2? Offset, page number, a next-cursor in the response, or only a time-range filter? → `pagination.type`.
- Can it filter by "changed since X"? Which field, and in what format does the filter expect the timestamp? → `cursor` + `cursor_timestamp_format`.
- Any per-account custom/dynamic fields (CRM-shaped APIs)? Is there a metadata endpoint listing them?
- Any non-2xx status that isn't really a failure — a 500 past the last page, a 404 for a soft-deleted record, a 429 rate limit? → `http_codes`.

**Ask instead of guessing when** the API needs an auth flow that isn't a static header/token or standard OAuth2 authorization-code, a pagination style that isn't offset/page/token/time, or a response shape you cannot reach with a flat list of keys/indices. These are gaps in the notation itself, not puzzles to solve with a creative workaround. Say so plainly and stop.

## Step 3 — Capture real responses

For each endpoint, make a real request and save the response:

```bash
curl -s -H "Authorization: Bearer $API_KEY" "https://api.example.com/orders?limit=2" > /tmp/orders.json
```

Then let the scaffolder do the transcription — it reads a saved response and emits candidate `response.path` values, a full `fields:` block with dot-notation names, inferred types, nullability, and flagged primary-key/cursor candidates:

```bash
python scripts/scaffold_from_response.py /tmp/orders.json
```

Treat its output as a well-informed draft: it is right about structure and names (it read the actual JSON) and only guessing at intent, so review which fields you actually want synced, confirm the primary key, and confirm the cursor.

**If you cannot reach the API** — no credentials, no sandbox — say so explicitly, write the connector from the docs, and mark every uncertain value with a `# UNVERIFIED:` comment naming what needs checking:

```yaml
response:
  path: [data]  # UNVERIFIED: docs show a "data" envelope; confirm against a real response
```

Then tell the user which endpoints are unverified and what one curl each would confirm. An unverified connector that says so is useful; one that pretends to be verified is a trap.

## Step 4 — Write `_default.yml`

Required: `name` and `request.url_base`. Everything else here is inherited by every endpoint file, which can override any key by setting it themselves.

```yaml
name: "Acme API"

authentication:
  - name: api_key
    type: secret          # secret = masked in the UI; variable = shown in the clear
    required: true

request:
  url_base: "https://api.acme.com/v2"
  method: GET
  headers:
    Authorization: "Bearer <api_key>"
  cursor_timestamp_format: "%Y-%m-%dT%H:%M:%SZ"

response:
  http_codes:
    429:
      message: "Rate limited by the API — will retry with backoff."
    404:
      ignore: true

validation:
  endpoint: "/me"
  request:
    method: GET
```

Put the connector's *most common* `cursor_timestamp_format` here and override it on the few fields that differ — that's cheaper than repeating it on every endpoint.

For OAuth2, read `references/schema.md` § Authentication. The shape is long but entirely mechanical; the one non-obvious part is that `client_id`/`client_secret` are declared as ordinary `variable`/`secret` entries and referenced as `<client_id>`/`<client_secret>`, exactly like any other credential.

## Step 5 — Write one file per endpoint

Four top-level sections, at most: `dependency`, `request`, `response`, `fields`.

```yaml
request:
  method: GET
  endpoint: "/orders"
  params:
    page: "<page>"
    updated_since: "<updated_at>"

response:
  path: [data, results]
  primary_key: [id]
  pagination:
    type: page
    path: [meta, total_pages]

fields:
  - name: id
    type: string
  - name: customer.email
    type: string
    nullable: true
  - name: updated_at
    type: timestamp
    cursor: true
```

**Choosing `pagination.type`** — pick by what the API gives you to advance with:

| The API gives you… | type | Required keys | The placeholder |
|---|---|---|---|
| `?offset=200&limit=100` | `offset` | — (`offset_size`, `initial_value`, `path` optional) | `<page>` = current offset |
| `?page=3` | `page` | — (`initial_value`, `path` optional) | `<page>` = current page number |
| a `next`/`cursor`/`endCursor` in the response | `token` | `path` (where that token lives) | `<page>` = the token |
| only a date-range filter | `time` | `cursor_timestamp_format`, `window_size_in_days`, `sort` | `<timepage_start>`, `<timepage_end>` |

Omit `pagination` entirely for endpoints that genuinely return everything in one request. For `offset`/`page`, `path` (total count / total pages) is optional — without it the connector pages until it gets an empty response, which is fine and often more robust than trusting a total the API computes lazily.

**Field types** — the validator accepts exactly: `string`, `integer`, `float`, `boolean`, `timestamp`, `date`, `time`, `unix_timestamp`, `json`. Use `string` for text — there is no `text` type. Use `json` for a nested object or array you want stored whole rather than flattened. Reach nested scalars with dot notation instead: `state.name` for `{"state": {"name": "open"}}`.

Set `nullable: true` on anything the API can omit or return `null` for. Being wrong here is expensive: a non-nullable field that arrives null fails the row.

**Incremental sync** — mark the "last changed" field `cursor: true`. Its stored value is then injected into the request wherever you write `<exact_field_name>`, spelled **exactly** as the field's `name`: a field named `updatedAt` is `<updatedAt>`, never `<updated_at>`. `cursor_timestamp_format` converts the stored ISO timestamp into whatever the filter expects (`"%s"` unix seconds, `"%s000"` milliseconds, `"%Y-%m-%dT%H:%M:%S.000Z"` ISO-with-Z). Time-window pagination is the exception — it always uses `<timepage_start>`/`<timepage_end>` regardless of the cursor field's name.

Set `primary_key` whenever you can. Without it, records get a fresh synthetic key each sync, so the same real-world record won't map to the same row over time — which also breaks anything downstream that expects stable identity.

**Child endpoints**: if records can only be fetched per-parent-record, add a `dependency` block and reference the injected value as `<as-name>`. One parent, one field. See `references/patterns.md` § Parent/child.

## Step 6 — Delete every example file you did not adapt

Every `*.yml` at the repo root other than `_default.yml` is treated as a real endpoint. Leftover `example_orders.yml` from the template becomes a live endpoint pointed at a nonexistent path — it will validate clean and fail at sync. Delete the ones you didn't adapt, including unused files under `custom_field_endpoints/`.

## Step 7 — Validate, then fix

```bash
python validate_connector.py .
```

It needs PyYAML (`pip install pyyaml`) — the repo doesn't declare this, so a `ModuleNotFoundError: No module named 'yaml'` on first run is expected, not a problem with your connector.

Errors fail the run; warnings don't. But warnings are where the quiet damage lives: an unknown key is *ignored*, so a single misspelled key — `validation.endpoint_url` for `validation.endpoint` — warns once and leaves the connector with no credential check at all, while the run still exits 0. Resolve every warning or consciously accept it; don't read "0 errors" as "correct".

`references/troubleshooting.md` maps the validator's messages to causes and fixes. The four that account for most failures:

- **`'text' is not a valid type`** — use `string`.
- **`request.json: expected str, got dict`** — `json:` and `query:` are strings, not YAML mappings. Use a `>` folded scalar and paste the body verbatim.
- **`unknown key` on `validation.endpoint_url`** — the key is `validation.endpoint`.
- **`placeholder <x> doesn't match…`** — a cursor placeholder must match a `cursor: true` field's `name` character-for-character.

Then re-read your own diff against the saved responses from Step 3: does each `response.path` still match, and does every field name appear in the JSON? That's the check the validator can't do for you.

## Step 8 — Ship it

The connector is deployed by pushing to a repo Erathos watches — every push to the watched repository rebuilds the image, after which it appears in the datasource catalog with a **Custom** badge.

Commit the connector folder and push (branch + PR if the repo has review conventions — check before pushing to a default branch). If the repo isn't being watched yet, tell the user the one-time setup: install the Erathos GitHub App, then **Settings → Workspace → Repositories → Add repository** in `owner/repo` form. Don't push to a shared repo without the user's go-ahead — a push here is a deploy.

## Step 9 — Explain where the credentials come from

The connector is finished, but nothing syncs until someone fills in the credential fields — and those values come from the source API's console, not from Erathos. Close every connector with a short handoff, written in the language the user is speaking.

Walk the `authentication:` entries of `_default.yml` in the order they appear. For each one:

- **What it is**, and whether it's `required`. Give the `name` as declared plus a plain-language label — `name` is a code identifier, so the field the user sees may read differently.
- **Where to get it**: the menu path and/or the direct URL, taken from the documentation you actually read while building this connector. Link that page.
- **What the value looks like** — a prefix, a length, a shape (`acme_live_…`, a UUID, the `acme` out of `acme.vendor.com`). This is what lets the user tell a right value from a wrong one before pasting it.
- **What will bite them**: shown only once at creation, admin/owner-only, behind a paid plan or a feature flag, separate sandbox and production keys, the scopes the endpoints you built need, IP allowlists, expiry.

Ground each "where to get it" the way you ground a `response.path`. If the docs you read say where the key is issued, say it and link it. If they never say, say *that* — and point at where to ask (the provider's console, the account's support contact) instead of inventing a `Settings → Developers → API Keys` that sounds right. A fabricated path costs the user a support ticket and costs you their trust in the rest of the handoff.

For an `oauth2` entry nobody pastes a token. The user registers an OAuth app in the provider's console to get `client_id` / `client_secret`, and the rest happens through the connect flow. Tell them the redirect/callback URL that has to be registered on that app, and the scopes the endpoints you built require.

Then the Erathos side, briefly. In the datasource catalog the connector appears under whatever `name` you set in `_default.yml`, carrying a **Custom** tag. Quote that name back to the user — they picked it, but they won't remember how you spelled it — and tell them that typing it into the catalog's search bar is the fastest way to it. Creating the datasource asks for exactly the fields you declared, in that order, with `secret` entries masked and `variable` entries in the clear. If you wrote a `validation` block, say what it calls (e.g. `GET /me`) and that a wrong credential fails right there on save rather than at the first sync.

## Reference files

Read these as needed rather than up front:

- `references/schema.md` — every key the validator accepts, with required/optional and types. Go here for OAuth2, `http_codes`, `custom_field_endpoints/`, `fields_format`, and the full placeholder rules.
- `references/patterns.md` — copy-adaptable worked endpoints: each pagination type, GraphQL, parent/child dependency, custom fields, per-tenant subdomains.
- `references/troubleshooting.md` — validator message → cause → fix, plus the "validates clean but syncs nothing" failures.
- `scripts/scaffold_from_response.py` — turns a saved JSON response into a draft `response.path` + `fields:` block.
