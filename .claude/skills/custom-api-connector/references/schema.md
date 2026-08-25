# Schema reference

Every key below is transcribed from `validate_connector.py`, which is the authority. Where the
template's README disagrees, this file follows the validator — the README has at least two known
errors (`type: text`, `validation.endpoint_url`), noted inline.

**Contents**
- [File layout and naming](#file-layout-and-naming)
- [`_default.yml`](#_defaultyml)
- [Authentication](#authentication)
- [Endpoint files](#endpoint-files)
- [`dependency`](#dependency)
- [`request`](#request)
- [`response`](#response)
- [`pagination`](#pagination)
- [`fields`](#fields)
- [`http_codes`](#http_codes)
- [Custom field endpoints](#custom-field-endpoints)
- [Placeholders](#placeholders)

---

## File layout and naming

```
<repo root>/
  _default.yml
  <endpoint_name>.yml          # any *.yml here that isn't _default.yml is an endpoint
  custom_field_endpoints/
    <custom_field_name>.yml
```

The validator globs `*.yml` directly under the connector directory and treats everything except
`_default.yml` as an endpoint. Files in subdirectories other than `custom_field_endpoints/` are
ignored entirely — so an unadapted example at the root is a live endpoint, while a "notes" file
tucked in a subfolder is invisible.

Endpoint name = filename without `.yml`. This is what `dependency.endpoint` and
`response.custom_field_endpoint` reference.

---

## `_default.yml`

Top-level keys: `name`, `authentication`, `request`, `response`, `validation`. Anything else warns.

| Key | Required | Type | Notes |
|---|---|---|---|
| `name` | **yes** | string | Human-readable connector/API name. |
| `authentication` | no | list | See [Authentication](#authentication). |
| `request` | **yes** | mapping | See below. |
| `response` | no | mapping | Only `http_codes` allowed here. |
| `validation` | no | mapping | Only `endpoint` and `request` allowed. |

### `_default.yml` → `request`

Allowed keys: `url_base`, `method`, `headers`, `cursor_timestamp_format`. **Only these four** — note
that `params`, `json`, and `query` are *not* valid at connector level even though they are valid in
an endpoint's `request`.

| Key | Required | Type |
|---|---|---|
| `url_base` | **yes** | string |
| `method` | no | string |
| `headers` | no | mapping |
| `cursor_timestamp_format` | no | string |

`url_base` accepts only authentication placeholders (`<name>`, `<name.access_token>`,
`<name.refresh_token>`) — pagination and cursor tokens there produce a warning.

### `_default.yml` → `validation`

Allowed keys: `endpoint`, `request`.

> The README's Step 4 table calls this `validation.endpoint_url`. That is wrong — the validator only
> accepts `endpoint`, and `endpoint_url` produces an `unknown key` warning and is then ignored.

`validation.request` allows `method`, `headers`, `params`, `json` — all optional, no `endpoint`
key inside it. Use it only when the credential check needs something beyond the connector-wide
`request.headers`.

---

## Authentication

`authentication` is a list, valid in `_default.yml` only. Every entry needs `name` (string) and
`type` (one of `secret`, `variable`, `oauth2`). `required` (bool) is optional. Names must be unique.

- `secret` — masked in the UI. API keys, client secrets, passwords.
- `variable` — shown in the clear. Subdomains, account IDs, region codes.
- `oauth2` — at most one entry per connector; may coexist with `secret`/`variable` entries for APIs
  that support either method.

`secret` and `variable` entries accept **no other keys** (`name`, `type`, `required` only).

### `type: oauth2`

Allowed keys: `name`, `type`, `required`, `access_token_expires_in_seconds`,
`refresh_token_expires_in_seconds`, `authorization`, `grant`, `refresh`, `revoke`. All of the
latter six are **required** by the validator.

| Key | Type | Notes |
|---|---|---|
| `access_token_expires_in_seconds` | integer | |
| `refresh_token_expires_in_seconds` | integer | |
| `authorization.url` | string | Where the user is sent to grant access. Accepts `<state>` and `<redirect_url>`. |
| `authorization.callback.code` | `{path: [...]}` | Where the code arrives, e.g. `{path: [params, code]}`. |
| `authorization.callback.state` | `{path: [...]}` | Same idiom for the state value. |
| `grant.request` | oauth request block | Exchanges the code for tokens. |
| `grant.response.access_token` | `{path: [...]}` | |
| `grant.response.refresh_token` | `{path: [...]}` | Use `~` if the API issues none. |
| `refresh.request` | oauth request block | |
| `refresh.response.access_token` | `{path: [...]}` | |
| `refresh.response.refresh_token` | `{path: [...]}` | `~` if not rotated. |
| `revoke.request` | oauth request block | No `response` block here. |

**oauth request block** allows exactly: `method`, `url`, `headers`, `params`, `json`. Note `url`
(a full URL) rather than `endpoint` — token endpoints often live on a different host than
`url_base`. `method` and `url` are required.

`client_id` / `client_secret` are not special: declare them as their own `variable`/`secret`
entries and reference them as `<client_id>` / `<client_secret>` inside the grant/refresh/revoke
requests, like any other stored credential.

```yaml
authentication:
  - name: client_id
    type: variable
    required: true
  - name: client_secret
    type: secret
    required: true
  - name: oauth
    type: oauth2
    required: true
    access_token_expires_in_seconds: 3600
    refresh_token_expires_in_seconds: 2592000
    authorization:
      url: "https://api.example.com/oauth/authorize?client_id=<client_id>&state=<state>&redirect_url=<redirect_url>"
      callback:
        code: {path: [params, code]}
        state: {path: [params, state]}
    grant:
      request:
        method: POST
        url: "https://api.example.com/oauth/token"
        json: '{"grant_type": "authorization_code", "client_id": "<client_id>", "client_secret": "<client_secret>", "code": "<code>", "redirect_url": "<redirect_url>"}'
      response:
        access_token: {path: [access_token]}
        refresh_token: {path: [refresh_token]}
    refresh:
      request:
        method: POST
        url: "https://api.example.com/oauth/token"
        json: '{"grant_type": "refresh_token", "client_id": "<client_id>", "client_secret": "<client_secret>", "refresh_token": "<oauth.refresh_token>"}'
      response:
        access_token: {path: [access_token]}
        refresh_token: {path: [refresh_token]}
    revoke:
      request:
        method: POST
        url: "https://api.example.com/oauth/revoke"
```

Once granted, use the tokens anywhere via `<oauth.access_token>` / `<oauth.refresh_token>`, where
`oauth` is that entry's `name`:

```yaml
request:
  headers:
    Authorization: "Bearer <oauth.access_token>"
```

---

## Endpoint files

Top-level keys: `dependency`, `request`, `response`, `fields`. `request`, `response`, and `fields`
are **required**; `dependency` is optional. `fields` must be a non-empty list — including on child
endpoints.

---

## `dependency`

Makes this a child endpoint: instead of syncing independently, it's called once per record returned
by the parent, with one of the parent's field values injected.

| Key | Required | Notes |
|---|---|---|
| `endpoint` | **yes** | Parent endpoint name (its filename, no `.yml`). Must exist; can't be this endpoint. |
| `field` | **yes** | Field name on the parent's records, spelled as in the parent's `fields:` list. |
| `as` | **yes** | Local name for the injected value, used as `<as>`. Can't be a reserved placeholder (`page`, `timepage_start`, `timepage_end`, `fields`) or collide with an authentication field name. |

One parent, one field, per endpoint.

---

## `request`

Allowed keys: `method`, `endpoint`, `params`, `json`, `query`, `headers`, `cursor_timestamp_format`,
`fields_format`.

| Key | Required | Type | Notes |
|---|---|---|---|
| `method` | **yes** | string | |
| `endpoint` | **yes** | string | Path appended to `url_base`. Auth/dependency placeholders only. |
| `params` | no | **mapping** | Query string params. |
| `json` | no | **string** | Request body as a string — not a YAML mapping. Use a `>` folded scalar. |
| `query` | no | **string** | GraphQL query text, pasted verbatim; braces need no escaping. |
| `headers` | no | mapping | Extends/overrides `_default.yml` headers for this endpoint. |
| `cursor_timestamp_format` | no | string | Endpoint-level default; per-field is usually better. |
| `fields_format` | no | mapping | `item` and `separator`, both optional strings. |

Setting more than one of `params` / `json` / `query` warns — an endpoint uses exactly the one the
API expects.

### `fields_format`

Controls how the `<fields>` placeholder expands. Default is a comma-separated, double-quoted list:
`"id", "name", "domain"`, which drops into a JSON array as `"properties": [<fields>]`.

```yaml
request:
  fields_format:
    item: '"field": "<name>"'    # <name> is the only placeholder valid here
    separator: ", "
```

---

## `response`

Allowed keys: `path`, `primary_key`, `pagination`, `custom_field_endpoint`, `http_codes`.

| Key | Required | Type | Notes |
|---|---|---|---|
| `path` | **yes** | list of strings/integers | Where the record array lives, e.g. `[data, issues, nodes]` or `[0, results]`. Integers index into lists. |
| `primary_key` | no | list of strings | Field names uniquely identifying a record. Omitting it means a fresh synthetic key each sync. |
| `pagination` | no | mapping | Omit for single-request endpoints. |
| `custom_field_endpoint` | no | string | Filename (no `.yml`) under `custom_field_endpoints/`. Must exist. |
| `http_codes` | no | mapping | Extends/overrides `_default.yml`'s. |

---

## `pagination`

`type` is required and must be `offset`, `page`, `token`, or `time`. Each type allows a different
key set — an `offset_size` on a `page` paginator warns as an unknown key.

| type | Key | Required | Default | Meaning |
|---|---|---|---|---|
| `offset` | `offset_size` | no | 100 | Records per page. |
| | `initial_value` | no | 0 | Offset of the first request. |
| | `path` | no | — | Where the total record count lives. Omit → page until empty. |
| `page` | `initial_value` | no | 1 | First page number. |
| | `path` | no | — | Where the total page count lives. Omit → page until empty. |
| `token` | `path` | **yes** | — | Where the next-page token lives in the response. |
| `time` | `cursor_timestamp_format` | **yes** | — | Format the API expects window bounds in, e.g. `"%s000"`. |
| | `window_size_in_days` | **yes** | — | Size of each sliding window. |
| | `sort` | **yes** | — | `asc` or `desc` only. |
| | `history_size_in_days` | no | 365 | How far back a first/forced backfill goes. Once a cursor field has a stored value, that value is the window start instead. |

---

## `fields`

A non-empty list. Each entry allows `name`, `type`, `nullable`, `cursor`, `cursor_timestamp_format`.

| Key | Required | Type | Notes |
|---|---|---|---|
| `name` | **yes** | string | As it appears in the response. Dot notation for nested scalars: `state.name`. Must be unique within the file. |
| `type` | **yes** | string | See valid types below. |
| `nullable` | no | boolean | Default false. Set true if the API can omit the key or send `null`. |
| `cursor` | no | boolean | Default false. Marks the field for incremental sync. Multiple cursor fields are allowed (composite). |
| `cursor_timestamp_format` | no | string | Only meaningful with `cursor: true` (warns otherwise). Falls back to the endpoint's, then the connector's. |

**Valid types** (exactly these nine):

`boolean`, `date`, `float`, `integer`, `json`, `string`, `time`, `timestamp`, `unix_timestamp`

> The README lists `text` as a type. `text` is **not** valid and fails validation — use `string`.

Use `json` when you want a nested object or array stored whole. To pull individual scalars out of a
nested object instead, list them with dot notation.

---

## `http_codes`

Valid in `_default.yml` under `response`, and per-endpoint under `response`. A mapping of status
code → config. Keys should be integers (a quoted `"500"` warns). Each config allows only `ignore`
and `message`.

```yaml
http_codes:
  500:
    ignore: true                          # "not actually a failure" — e.g. paging past the last page
    message: "Reached last page."
  502:
    message:
      path: [error]                       # derive the message from the response body
  429:
    message: "Rate limited — will retry with backoff."
```

`message` is either a string or a `{path: [...]}` mapping using the same path idiom as
`response.path`.

---

## Custom field endpoints

For APIs where each account defines extra fields on an entity (HubSpot, Salesforce, most CRMs).
Two pieces:

**1.** A file under `custom_field_endpoints/` describing the metadata endpoint that *lists* the
custom fields. Allowed top-level keys: `request` and `response` only. Declaring `fields:` here is an
error — the schema is discovered at sync time.

```yaml
request:
  method: GET
  endpoint: "/properties"

response:
  path: [results]           # required — where the list of field definitions lives
  custom_field:             # required — maps this API's metadata onto platform concepts
    field_key: name         # required — the API's field key
    field_name: label       # required — human-readable label
    field_type: type        # required — the API's data type
    is_nullable: ~          # optional — `~` if the API has no such concept
    description: description # optional
```

**2.** A `custom_field_endpoint:` key under `response` on each normal endpoint that returns those
fields, naming the file without `.yml`:

```yaml
response:
  path: [results]
  custom_field_endpoint: company_properties
```

Pair this with `<fields>` in the request body so discovered fields are actually requested — see
[Placeholders](#placeholders).

---

## Placeholders

Substituted before the request is sent. Valid inside `request.params`, `request.json`,
`request.query`, `request.headers`, `request.endpoint`, `_default.yml`'s `request.url_base`, and
`validation.request.*`.

| Placeholder | Valid where | Value |
|---|---|---|
| `<page>` | `offset` / `page` / `token` pagination | Current offset, page number, or token. |
| `<timepage_start>`, `<timepage_end>` | `time` pagination only | Bounds of the current window. |
| `<exact_field_name>` | Endpoints with a `cursor: true` field | That field's stored value, formatted by its `cursor_timestamp_format`. Spelled **exactly** as the field's `name`. |
| `<fields>` | Any endpoint | This endpoint's field names plus anything `custom_field_endpoint` discovers. |
| `<name>` | Anywhere | A `secret`/`variable` credential's value. |
| `<name.access_token>`, `<name.refresh_token>` | Anywhere | The oauth2 entry's current tokens. |
| `<name>` | Endpoints with `dependency` | The parent record's value, where `name` is `dependency.as`. |
| `<state>`, `<redirect_url>` | `authorization.url` only | OAuth flow mechanics. |
| `<code>`, `<redirect_url>` | `grant` / `refresh` / `revoke.request` | OAuth flow mechanics. |

**URL-shaped strings are more restrictive.** `request.endpoint`, `request.url_base`, and the oauth
`url` accept **only** authentication and dependency placeholders. Pagination and cursor tokens there
produce a warning — they don't make sense in a base URL or path.

Reserved names — `page`, `timepage_start`, `timepage_end`, `fields` — are substituted automatically
and cannot be declared as authentication fields or used as a `dependency.as` name.

`<state>`, `<redirect_url>`, and `<code>` are likewise reserved: they are mechanics of the
authorization-code flow rather than credentials, so they must not be declared as authentication
entries.
