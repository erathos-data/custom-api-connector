<br />
<p align="center">
  <a href="https://www.erathos.com/">
    <picture>
      <img alt="Erathos" width="50%" src="https://framerusercontent.com/images/KGTL101LDOEsmzHRz0fDsE8PI6U.svg?width=774&height=139">
    </picture>
  </a>

  <h1 align="center">Custom API Connector Template</h1>

  <p align="center">
    <a href="https://github.com/erathos-alexandria/custom-api-connector#adding-a-new-api-connector"><strong>New Connector</strong></a>
    ·
    <a href="https://github.com/erathos-alexandria/custom-api-connector#authentication-reference"><strong>Authentication</strong></a>
    ·
    <a href="https://github.com/erathos-alexandria/custom-api-connector#pagination-reference"><strong>Pagination</strong></a>
    ·
    <a href="https://github.com/erathos-alexandria/custom-api-connector#variable-placeholders"><strong>Variable Placeholders</strong></a>
    ·
    <a href="https://github.com/erathos-alexandria/custom-api-connector#custom-field-endpoints"><strong>Custom Fields</strong></a>
  </p>
</p>
</p>

# Adding a new API connector

This guide is for someone who wants to pull data from a new API into the platform and has
never touched this repo before. You will not write any Python. Everything you need to define
is a handful of YAML files that describe your API: where its data lives, how to page through
it, and what each field is called.

By the end you'll have a folder that looks like this one (`example/`), just describing your
API instead of a made-up one.

## Step 1 — Get set up

1. Fork/clone this repository.
2. Treat every file in here as a template to overwrite, not code to read.
3. Delete the example endpoint files that don't match anything in your API, and add one file
   per endpoint you actually want to sync (see Step 5).

You do not need to touch anything outside this folder. Nothing else in the repository changes
because of what you write here.

## Step 2 — Research your API before writing any YAML

Writing the YAML is mostly transcription once you know the answers to these questions. Get the
answers first — ideally by making a couple of real requests with curl/Postman/Insomnia against
your API and looking at the actual responses, not just the docs.

**Authentication**
- How does the API expect credentials — a header (`Authorization: Bearer ...`), a query
  param, something else?
- Is there a lightweight endpoint you can call just to check the credentials are valid
  (e.g. `/me`, `/whoami`)? You'll want this for `validation` in `_default.yml`.

**Per endpoint you want to sync**
- HTTP method and path (e.g. `POST /companies`).
- What does the request need — query params, a JSON body, a GraphQL query? What do the
  required ones look like?
- Where do you page through results — is there an `offset`, a `page` number, a `cursor`/
  `token` in the response, or does the API only support filtering by a time range? Look at
  a real response and find the field that tells you "there are more results" or "you're on
  page N of M."
- Where in the response body does the array of records actually live? (`results`,
  `data.items`, nested under something else?)
- What field (or combination of fields) uniquely identifies a record? This becomes
  `primary_key`.
- Does the API let you filter by "updated since X"? If so, which field is that timestamp,
  and what format does the API expect it in (ISO string, unix seconds, unix milliseconds)?
  This becomes your `cursor` field.
- Does this endpoint return any custom/dynamic fields per account (common in CRM-style APIs —
  HubSpot, Salesforce) that aren't part of the fixed schema? If so, is there a separate
  metadata endpoint that lists them?
- Are there any non-2xx status codes this endpoint returns that *aren't* really errors (e.g.
  "500 once you've paged past the last page", "404 means the record was deleted, skip it")?

Write these answers down somewhere — you'll use every one of them in Step 4/5.

## Step 3 — File layout

```
your_folder/
  _default.yml                        # connector-wide settings, shared by every endpoint
  some_endpoint.yml                   # one file per endpoint, filename = endpoint's name
  another_endpoint.yml
  custom_field_endpoints/
    some_custom_fields.yml            # only if an endpoint has dynamic/custom fields
```

The filename (without `.yml`) is the endpoint's name — there's no separate `name:` field to
keep in sync with it. Renaming a file renames the endpoint everywhere it's referenced
(including `custom_field_endpoint:` links).

Anything you set in `_default.yml` is inherited by every endpoint file. An endpoint file can
override any of those keys just by setting the same key itself — see `example_custom_fields_use.yml`
overriding `cursor_timestamp_format` on one field, or `_default.yml`'s `404: {ignore: true}`
being extended per-endpoint with more codes.

## Step 4 — Write `_default.yml`

Reference: [`_default.yml`](./_default.yml)

| Key | Required? | Meaning |
|---|---|---|
| `name` | **Required** | Human-readable name of the API/connector. |
| `authentication` | Optional | List of credential fields collected from the user. See "Authentication reference" below. |
| `request.url_base` | **Required** | Base URL every endpoint's path is appended to. Can use `<name>` for a declared authentication field (e.g. a per-tenant subdomain) - see "Variable Placeholders". |
| `request.method` | Optional | Default HTTP method if an endpoint doesn't need to override it. |
| `request.headers` | Optional | Headers sent on every request (e.g. auth header). Use `<name>` (or `<name.access_token>`/`<name.refresh_token>` for OAuth) to inject a stored credential - see "Authentication reference". |
| `request.cursor_timestamp_format` | Optional | Default format to convert a cursor field's value into whatever string the API expects, when an endpoint doesn't set its own (see Step 5). |
| `response.http_codes` | Optional | Connector-wide status-code handling, extendable per endpoint. See "Variable Placeholders" below for `message`. |
| `validation.endpoint_url` | Optional | Path used to sanity-check stored credentials before a sync starts. |
| `validation.request.method` | Optional | Method for that check, usually `GET`. |
| `validation.request.headers` | Optional | Extra/overriding headers for just this check, if it needs something beyond `request.headers` (e.g. an API that validates credentials via a different auth scheme than normal requests use). |
| `validation.request.params` | Optional | Query params for this check, if the endpoint needs any. |
| `validation.request.json` | Optional | Request body for this check, if it's a `POST`/etc. Same placeholder rules as an endpoint's `request.json`. |

## Step 5 — Write one file per endpoint

Every endpoint file has (at most) four top-level sections: `dependency`, `request`, `response`,
`fields`.

### `dependency`

Only needed if this endpoint's records can't be fetched on their own — they hang off a record
from another endpoint (e.g. an API that needs an order's `id` to look up that order's line
items). Declaring `dependency` makes this a *child* endpoint: instead of being synced
independently, it's called once per record returned by the parent endpoint, with the declared
field's value injected as a placeholder.

An endpoint can depend on exactly one parent endpoint, via exactly one field from it.

```yaml
# example_order_details.yml
dependency:
  endpoint: example_orders   # parent endpoint's name (matches its filename, no .yml)
  field: id                  # field on the parent's records to pull in
  as: order_id               # this endpoint references it via <order_id>

request:
  method: GET
  endpoint: "/orders/<order_id>/line_items"
```

| Key | Required? | Meaning |
|---|---|---|
| `endpoint` | **Required** | Name of the parent endpoint (its filename, without `.yml`). Must be another file in this folder — an endpoint can't depend on itself. |
| `field` | **Required** | Name of the field on the parent's records to pull in, exactly as spelled in that endpoint's `fields:` list. |
| `as` | **Required** | Name this endpoint will use to reference that value — see "Variable Placeholders". Can't be a reserved token (`page`, `fields`, ...) or collide with a declared `authentication` field's name. |

See `example_orders.yml` / `example_order_details.yml` for the full parent/child pair.

### `request`

| Key | Required? | Meaning |
|---|---|---|
| `method` | **Required** | `GET`, `POST`, etc. |
| `endpoint` | **Required** | Path appended to `url_base`, e.g. `"/companies"`. Can also use `<name>` for a declared authentication field - see "Variable Placeholders". |
| `params` | Optional | Query-string parameters (REST, `GET`-style pagination). |
| `json` | Optional | Request body (REST, `POST`-style). Use YAML's `>` folded-scalar so you can paste the JSON body as-is. |
| `query` | Optional | GraphQL query text, pasted verbatim — no need to escape braces (see "Variable Placeholders"). |
| `headers` | Optional | Overrides/extends `_default.yml`'s headers for this endpoint only. |
| `cursor_timestamp_format` | Optional | Overrides the connector-wide default (see `_default.yml`) for this endpoint. Usually set per-field instead (see `fields` below) — only set it here if every cursor field on this endpoint needs the same non-default format. |

An endpoint uses exactly one of `params`, `json`, or `query`, matching whatever the API
actually expects.

### `response`

| Key | Required? | Meaning |
|---|---|---|
| `path` | **Required** | Where the list of records lives in the response body. A list of keys/indices, e.g. `[data, issues, nodes]` or `[0, results]`. |
| `primary_key` | Optional | List of field names that uniquely identify a record, e.g. `[id]`. **If you omit this, records get a random synthetic key on every sync instead of a stable one** — fine for endpoints nothing else depends on, but it means the same real-world record won't reliably map to the same row across syncs. Set it whenever you can. |
| `pagination` | Optional | See the pagination reference below. **Omit entirely** if the endpoint is self-contained and always returns everything in one request. |
| `custom_field_endpoint` | Optional | Name of a file under `custom_field_endpoints/` (without `.yml`) that this endpoint's custom fields are discovered from. Only needed if your research in Step 2 found dynamic/custom fields. |
| `http_codes` | Optional | Overrides/extends `_default.yml`'s `http_codes` for this endpoint only. |

### `fields`

A list, one entry per field you want synced.

| Key | Required? | Meaning |
|---|---|---|
| `name` | **Required** | Field name as it appears in the response. For nested values, use dot notation, e.g. `state.name` for `{"state": {"name": "..."}}`. |
| `type` | **Required** | One of: `text`, `integer`, `float`, `boolean`, `timestamp`, `json`. Use `json` for a field you want stored as raw nested data rather than flattened. |
| `nullable` | Optional | Set `true` if the API can omit this field or return `null`. Defaults to not-nullable. |
| `cursor` | Optional | Set `true` if this field is used for incremental sync ("give me records updated after X"). More than one field can be a cursor at once (composite cursor). Defaults to `false`. |
| `cursor_timestamp_format` | Optional | Only meaningful when `cursor: true`. Converts the stored (ISO) timestamp into whatever format your API's filter expects — see the examples in `example_graphql.yml` / `example_custom_fields_use.yml`. Falls back to the connector-wide default if omitted. |

### `http_codes` entries (used in both `_default.yml` and per-endpoint)

```yaml
http_codes:
  500:
    ignore: true              # true = "not actually a failure", e.g. end-of-pagination
    message: "some text"      # optional; static string, OR:
  502:
    message:
      path: [error]            # ...derive it from the response body instead
```

`path` here is the exact same idiom used by `response.path` and `pagination.path` — "look
this value up inside the response body."

## Authentication reference

`authentication` (in `_default.yml` only) is a list of credential fields the platform collects
from the user and stores. Every entry has:

| Key | Required? | Meaning |
|---|---|---|
| `name` | **Required** | Code identifier for this credential — no spaces. Referenced later via placeholders (see "Variable Placeholders" below), not shown to the user. |
| `type` | **Required** | One of `secret`, `variable`, `oauth2`. |
| `required` | Optional | Whether the user must supply this credential before a sync can run. |

At most one `type: oauth2` entry is allowed per connector. It can sit alongside
`secret`/`variable` entries, for APIs that support more than one auth method (e.g. an API key
*or* OAuth).

### `type: secret` / `type: variable`

No further keys. The only difference is how the platform's UI treats the value: `secret` is
masked (API keys, client secrets, passwords); `variable` is shown in the clear (e.g. an account
subdomain). Reference the stored value via `<name>` — see "Variable Placeholders".

### `type: oauth2`

Describes a full OAuth2 authorization-code flow: the platform sends the user to
`authorization.url`, the API redirects back with a code, the connector exchanges that code for
tokens via `grant`, `refresh` is used to get a new access token once the current one expires,
and `revoke` is used to invalidate the tokens later (e.g. if the user disconnects the
integration).

Declare the client ID/secret as their own
`secret`/`variable` entries (conventionally `variable` for `client_id`, `secret` for
`client_secret`) and reference them via `<client_id>`/`<client_secret>` inside
`grant`/`refresh`/`revoke` requests, the same as any other stored credential. See `_default.yml`
for the full pattern.

| Key | Required? | Meaning |
|---|---|---|
| `access_token_expires_in_seconds` | **Required** | Lifetime of an issued access token. |
| `refresh_token_expires_in_seconds` | **Required** | Lifetime of an issued refresh token. |
| `authorization.url` | **Required** | URL the user is sent to in order to grant access. `<state>` and `<redirect_url>` are reserved placeholders here (see below) — no need to declare them as authentication fields. |
| `authorization.callback.code` | **Required** | Where in the callback request the authorization code lives, e.g. `{path: [params, code]}`. |
| `authorization.callback.state` | **Required** | Same idiom, for the `state` value. |
| `grant.request` | **Required** | Request made to exchange the code for tokens. Same shape as an endpoint's `request` (`method`, `headers`, `params`, `json`), except it takes a full `url` instead of `endpoint` — the token endpoint is often on a different host than `request.url_base`. The captured `authorization.callback.code` value is injected into this request via `<code>` (see below). |
| `grant.response.access_token` | **Required** | Where the access token lives in that response, e.g. `{path: [access_token]}`. |
| `grant.response.refresh_token` | **Required** | Same idiom, for the refresh token. For APIs that do not use refresh token you may use a null value `~` |
| `refresh.request` | **Required** | Request made to trade the stored refresh token for a new access token, once the current one expires. Same shape as `grant.request`. Reference the stored refresh token via `<name.refresh_token>` (see "Variable Placeholders"), same as you would in any endpoint's request. |
| `refresh.response.access_token` | **Required** | Where the new access token lives in that response, same idiom as `grant.response.access_token`. |
| `refresh.response.refresh_token` | **Required** | Where the new refresh token lives, if the API rotates it on refresh. For APIs that return the same/no new refresh token you may use a null value `~`. |
| `revoke.request` | **Required** | Request made to revoke the stored tokens. Same shape as `grant.request`. |

Once granted, reference the stored tokens via `<name.access_token>` / `<name.refresh_token>`,
where `name` matches this entry's `name` — see "Variable Placeholders".

**Reserved OAuth2 placeholders.** A couple of tokens are recognized by name rather than needing
to be declared as authentication fields, because they aren't credentials — they're mechanics of
the authorization-code flow itself:
- `<redirect_url>` (valid in `authorization.url` and in `grant`/`refresh`/`revoke.request`) is the
  callback URL the API sends the user back to once they approve access. The API checks it against
  the redirect URL registered for your OAuth app, so it has to be sent both when kicking off the
  flow (`authorization.url`) and again when exchanging the code for tokens (`grant.request`) — most
  APIs reject the exchange if the two don't match.
- `<state>` (valid in `authorization.url` only) is an opaque value the platform generates and the
  API echoes back unchanged in the callback (captured via `authorization.callback.state`). It's
  what ties the callback back to the specific authorization attempt that started it and guards
  against CSRF — it isn't a credential, so it can't be declared as one.
- `<code>` (valid in `grant`/`refresh`/`revoke.request`) is the one-time authorization code the API
  issued in the callback (captured via `authorization.callback.code`). It's injected into
  `grant.request` as the base logic of the grant step exchange.

These behave like `<page>`/`<fields>` elsewhere in the notation — they're substituted
automatically and don't need (and can't use) a matching `authentication` entry.

## Pagination reference

Set `pagination.type` to one of the four below. Full examples:
[`example_offset_pagination.yml`](./example_offset_pagination.yml),
[`example_page_pagination.yml`](./example_page_pagination.yml),
[`example_token_pagination.yml`](./example_token_pagination.yml),
[`example_time_pagination.yml`](./example_time_pagination.yml).

| Type | Key | Required? | Meaning |
|---|---|---|---|
| `offset` | `offset_size` | Optional (default `100`) | How many records to request per page. |
| | `initial_value` | Optional (default `0`) | Offset of the first request. |
| | `path` | Optional | Where the total record count lives, to know when to stop. If omitted, keeps requesting until a page comes back empty. |
| `page` | `initial_value` | Optional (default `1`) | Page number of the first request. |
| | `path` | Optional | Where the total page count lives to know when to stop. If omitted, keeps requesting until a page comes back empty. |
| `token` | `path` | **Required** | Where the next-page cursor/token lives in the response. Injected into the next request via `<page>` (see Variable Placeholders). |
| `time` | `cursor_timestamp_format` | **Required** | Format the API expects the time-window bounds in (e.g. `"%s000"` for unix milliseconds). |
| | `window_size_in_days` | **Required** | Size of each sliding time window requested. |
| | `history_size_in_days` | Optional (default `365`) | How far back the very first (or a manually-forced) backfill run goes. Once a cursor field has a value from a prior sync, that value is used as the window start instead — `history_size_in_days` only matters again if someone deliberately forces a full history re-run. |
| | `sort` | **Required** | Sort order requested from the API, e.g. `asc`. |

## Variable Placeholders

Inside `request.params`, `request.json`, `request.query`, `request.headers`, `request.endpoint`,
`request.url_base` (in `_default.yml`), or `validation.request.headers`/`.params`/`.json`, use
these tokens — they get substituted before the request is sent. `request.endpoint` and
`request.url_base` are more restrictive than the rest: only the authentication placeholders
(`<name>`, `<name.access_token>`, `<name.refresh_token>`) are valid there — pagination and
cursor placeholders don't make sense in a URL base or path.

| Placeholder | Where it's valid | Value |
|---|---|---|
| `<page>` | `offset` / `page` / `token` pagination | Current offset, page number, or token, depending on `pagination.type`. |
| `<timepage_start>`, `<timepage_end>` | `time` pagination only | Start/end of the current sliding time window. |
| `<exact_field_name>` | Any endpoint with a `cursor: true` field | The cursor field's current stored value, converted using its `cursor_timestamp_format`. The placeholder must be spelled exactly like the field's `name` — e.g. a field named `updatedAt` is referenced as `<updatedAt>`, not `<updated_at>`. (Time pagination is the one exception — it always uses `<timepage_start>`/`<timepage_end>` instead, regardless of the cursor field's name.) |
| `<fields>` | Any endpoint where you can reduce the fields returned in the response | Expands to this endpoint's `fields:` names plus whatever `custom_field_endpoint` discovers at sync time. |
| `<name>` | Anywhere, for a `secret`/`variable` `authentication` entry | The stored credential value. |
| `<name.access_token>`, `<name.refresh_token>` | Anywhere, for the connector's `oauth2` `authentication` entry | The current access/refresh token, where `name` matches that entry's `name`. |
| `<name>` | Anywhere in an endpoint that declares `dependency` | The parent record's field value, where `name` matches that endpoint's `dependency.as`. Same placement rules as an authentication placeholder (including `request.endpoint`/`url_base`). |
| `<state>`, `<redirect_url>` | `authentication[].authorization.url` only | Reserved for the OAuth2 authorization-code flow — see "Reserved OAuth2 placeholders" above. |
| `<code>`, `<redirect_url>` | `authentication[].grant`/`refresh`/`revoke.request` only | Same idiom, for the token-exchange requests. |

GraphQL queries (`request.query`) are pasted in as normal GraphQL — you do **not** need to
escape/double any curly braces. See `example_graphql.yml`.

### Customizing `<fields>`'s output format

By default `<fields>` expands to a comma-separated, double-quoted list —
`"id", "name", "domain"` — which drops straight into a JSON array like
`"properties": [<fields>]` (see `example_custom_fields_use.yml`). If your API needs a
different shape, set `request.fields_format`:

```yaml
request:
  fields_format:
    item: '"field": "<name>"'   # <name> is replaced with each field's name
    separator: ", "
```

This would make `<fields>` expand to `"field": "id", "field": "name", "field": "domain"`
instead. `item`/`separator` are both optional — omit either to keep its default.

## Custom field endpoints

Some APIs (HubSpot, Salesforce, and most CRM-shaped tools) let each account define its own
extra fields per entity, on top of a fixed base schema. If your research in Step 2 found this,
you need two things:

1. A file under `custom_field_endpoints/` describing the metadata endpoint that *lists* the
   custom fields — see [`custom_field_endpoints/example_custom_fields.yml`](./custom_field_endpoints/example_custom_fields.yml).
   It has `request` and `response` like a normal endpoint, but **no `fields:` list** — the
   schema is discovered at sync time, not declared up front. Instead it has
   `response.custom_field`, which maps *this API's* metadata field names onto the fixed
   concepts the platform expects:

   ```yaml
   response:
     path: [results]         # required — where the list of custom-field definitions lives
     custom_field:
       field_key: name         # required — API's field name/key
       field_name: label        # required — API's human-readable label
       field_type: type         # required — API's data type for this field
       is_nullable: ~            # optional — leave `~` (null) if the API has no such concept
       description: description  # optional
   ```

2. A `custom_field_endpoint: <name>` key on whichever normal endpoint(s) actually return
   those custom fields (the `<name>` is the custom-field file's name, without `.yml`) — see
   `custom_field_endpoint: example_custom_fields` in `example_custom_fields_use.yml`.

## Checklist before you're done

- [ ] `_default.yml` has `name`, `request.url_base`, and enough `request.headers`/auth info
      to make a real request.
- [ ] Every endpoint file has `request.method` and `request.endpoint`.
- [ ] Every endpoint file has `response.path` pointing at the actual list of records.
- [ ] You've set `primary_key` wherever consistent record identity matters.
- [ ] You've set `pagination` on every endpoint except genuinely single-request ones.
- [ ] Every field you listed actually exists in a real response you looked at (not just docs).
- [ ] Any status code that isn't a real error (end-of-pagination quirks, soft-deleted
      records, rate limits) is handled under `http_codes`, not left to fail the sync.
- [ ] Run validate_connector.py to confirm whether your files are missing any required fields or in bad format.

## When to ask instead of guessing

This notation covers everything the four pagination types, GraphQL/REST requests, custom
fields, and per-status-code handling can express. If your API needs something that doesn't
fit anywhere above — an auth flow beyond a static header/token, a pagination style that isn't
offset/page/token/time, or a response shape you can't reach with a flat `path:` list — stop
and ask rather than forcing it into the closest-looking field. It's very likely something the
notation needs to be extended to support, not something you're missing.
