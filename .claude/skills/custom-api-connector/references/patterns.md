# Worked patterns

Adapt these rather than writing from scratch. Each one is a complete, validator-clean file.

**Contents**
- [Offset pagination](#offset-pagination)
- [Page-number pagination](#page-number-pagination)
- [Token / cursor pagination](#token--cursor-pagination)
- [Time-window pagination](#time-window-pagination)
- [Single-request endpoint](#single-request-endpoint)
- [POST with a JSON body](#post-with-a-json-body)
- [GraphQL](#graphql)
- [Parent/child (`dependency`)](#parentchild-dependency)
- [Custom fields](#custom-fields)
- [Per-tenant subdomain](#per-tenant-subdomain)
- [Timestamp format cheatsheet](#timestamp-format-cheatsheet)

---

## Offset pagination

`?offset=200&limit=100`. `<page>` carries the current offset.

```yaml
request:
  method: GET
  endpoint: "/products"
  params:
    offset: "<page>"
    limit: "100"

response:
  path: [results]
  primary_key: [id]
  pagination:
    type: offset
    offset_size: 100          # optional, default 100 — keep in sync with the limit param
    initial_value: 0          # optional, default 0
    path: [metadata, total_count]   # optional — omit to page until an empty response

fields:
  - name: id
    type: string
  - name: name
    type: string
```

---

## Page-number pagination

```yaml
request:
  method: GET
  endpoint: "/users"
  params:
    page: "<page>"

response:
  path: [data]
  primary_key: [id]
  pagination:
    type: page
    initial_value: 1          # optional, default 1 — set to 0 for zero-indexed APIs
    path: [meta, total_pages]

fields:
  - name: id
    type: string
  - name: email
    type: string
    nullable: true
```

---

## Token / cursor pagination

The response carries the next page's token; `pagination.path` says where, and `<page>` injects it
into the next request.

```yaml
request:
  method: GET
  endpoint: "/orders"
  params:
    cursor: "<page>"

response:
  path: [data]
  primary_key: [id]
  pagination:
    type: token
    path: [metadata, next]    # required for token pagination

fields:
  - name: id
    type: string
  - name: product_name
    type: string
  - name: buyer.name          # dot notation reaches {"buyer": {"name": ...}}
    type: string
    nullable: true
```

---

## Time-window pagination

For APIs that only let you filter by a date range — event logs, audit trails. The connector walks
forward in windows of `window_size_in_days`.

```yaml
request:
  method: GET
  endpoint: "/events"
  params:
    start_date: "<timepage_start>"
    end_date: "<timepage_end>"

response:
  path: [results]
  primary_key: [id]
  pagination:
    type: time
    cursor_timestamp_format: "%s000"   # required — this API wants unix milliseconds
    window_size_in_days: 30            # required
    sort: asc                          # required — asc or desc
    history_size_in_days: 365          # optional, default 365

fields:
  - name: id
    type: string
  - name: at
    type: timestamp
    cursor: true              # its stored value becomes the next run's window start
```

Note the two formats in play: `pagination.cursor_timestamp_format` formats the *window bounds*, and
a field's own `cursor_timestamp_format` formats `<field_name>` placeholders. With time pagination
you use `<timepage_start>`/`<timepage_end>`, never `<at>`.

---

## Single-request endpoint

Small reference tables — omit `pagination` entirely.

```yaml
request:
  method: GET
  endpoint: "/currencies"

response:
  path: [data]
  primary_key: [code]

fields:
  - name: code
    type: string
  - name: name
    type: string
  - name: rate
    type: float
```

---

## POST with a JSON body

`json` is a **string**, not a YAML mapping. The `>` folded scalar lets you paste the body verbatim.

```yaml
request:
  method: POST
  endpoint: "/search"
  json: >
    {"limit": 100, "after": "<page>",
     "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
     "filters": [
       {"propertyName": "lastmodifieddate", "operator": "GTE", "value": "<lastmodifieddate>"}
     ]}

response:
  path: [results]
  primary_key: [id]
  pagination:
    type: token
    path: [paging, next, after]

fields:
  - name: id
    type: string
  - name: lastmodifieddate
    type: timestamp
    cursor: true
    cursor_timestamp_format: "%Y-%m-%dT%H:%M:%S.000Z"
```

---

## GraphQL

`query` is a string too. Paste the query as-is — braces need no escaping.

```yaml
request:
  method: POST
  endpoint: "/graphql"
  query: >
    {
      issues(first: 250 <page> filter: {updatedAt: {gte: "<updatedAt>"}}) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id title number createdAt updatedAt
          state { id name }
          assignee { id email }
        }
      }
    }

response:
  path: [data, issues, nodes]
  primary_key: [id]
  pagination:
    type: token
    path: [data, issues, pageInfo, endCursor]

fields:
  - name: id
    type: string
  - name: title
    type: string
    nullable: true
  - name: number
    type: integer
  - name: updatedAt
    type: timestamp
    cursor: true
    cursor_timestamp_format: "%Y-%m-%dT%H:%M:%S.000Z"
  - name: state.name
    type: string
    nullable: true
  - name: assignee.id
    type: string
    nullable: true
```

The `fields:` list must mirror what the query actually selects — GraphQL returns exactly what you
ask for, so a field listed here but absent from the query is always null, and one selected in the
query but missing here is silently dropped.

---

## Parent/child (`dependency`)

For records reachable only through a parent record's ID. The child is called once per parent record.

**`orders.yml`** — an ordinary endpoint:

```yaml
request:
  method: GET
  endpoint: "/orders"
  params:
    page: "<page>"

response:
  path: [results]
  primary_key: [id]
  pagination:
    type: page

fields:
  - name: id
    type: string
  - name: status
    type: string
```

**`order_line_items.yml`** — the child:

```yaml
dependency:
  endpoint: orders          # parent's filename without .yml
  field: id                 # must be spelled as in orders.yml's fields: list
  as: order_id              # referenced below as <order_id>

request:
  method: GET
  endpoint: "/orders/<order_id>/line_items"

response:
  path: [results]
  primary_key: [id]

fields:
  - name: id
    type: string
  - name: sku
    type: string
  - name: quantity
    type: integer
```

Child endpoints still need their own non-empty `fields:` list. `as` can't collide with an
authentication field name or a reserved token.

Cost is worth thinking about: a child endpoint makes one request per parent record, so a parent with
100k records means 100k child requests. If the API has a bulk endpoint that returns the same data
filtered by date, prefer it.

---

## Custom fields

For APIs where each account defines its own extra fields on an entity.

**`custom_field_endpoints/company_properties.yml`** — describes the metadata endpoint. No `fields:`
list; the schema is discovered at sync time.

```yaml
request:
  method: GET
  endpoint: "/properties/companies"

response:
  path: [results]
  custom_field:
    field_key: name          # required
    field_name: label        # required
    field_type: type         # required
    is_nullable: ~           # optional — `~` when the API has no such concept
    description: description # optional
```

**`companies.yml`** — links to it and asks for the discovered fields via `<fields>`:

```yaml
request:
  method: POST
  endpoint: "/companies/search"
  json: >
    {"limit": 100, "after": "<page>",
     "properties": [<fields>]}

response:
  path: [results]
  primary_key: [id]
  custom_field_endpoint: company_properties
  pagination:
    type: token
    path: [paging, next, after]
  http_codes:
    500:
      ignore: true
      message: "Reached last page (API returns 500 past the final page)."

fields:
  - name: id
    type: string
  - name: name
    type: string
    nullable: true
  - name: domain
    type: string
    nullable: true
```

`<fields>` expands to `"id", "name", "domain"` plus every discovered custom field. If the API wants
a different shape, set `request.fields_format`.

---

## Per-tenant subdomain

When the base URL varies per customer, declare the variable part as a `variable` credential.

```yaml
# _default.yml
name: "Acme"

authentication:
  - name: subdomain
    type: variable        # shown in the clear — it isn't a secret
    required: true
  - name: api_token
    type: secret
    required: true

request:
  url_base: "https://<subdomain>.acme.com/api/v2"
  method: GET
  headers:
    Authorization: "Bearer <api_token>"

validation:
  endpoint: "/users/me"
  request:
    method: GET
```

Only authentication placeholders are allowed in `url_base` and `request.endpoint`.

---

## Timestamp format cheatsheet

`cursor_timestamp_format` is a `strftime` format converting the stored ISO timestamp into whatever
the API's filter expects. Match it to a real filter value the API accepts, not to the format the API
*returns*, which is often different.

| API expects | Format |
|---|---|
| `2024-03-01T12:30:00Z` | `"%Y-%m-%dT%H:%M:%SZ"` |
| `2024-03-01T12:30:00.000Z` | `"%Y-%m-%dT%H:%M:%S.000Z"` |
| `2024-03-01 12:30:00` | `"%Y-%m-%d %H:%M:%S"` |
| `2024-03-01` | `"%Y-%m-%d"` |
| `1709296200` (unix seconds) | `"%s"` |
| `1709296200000` (unix millis) | `"%s000"` |
