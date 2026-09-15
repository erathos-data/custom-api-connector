# Troubleshooting

Two classes of problem: things `validate_connector.py` catches, and things it can't. The second
class is where connectors actually fail in production, so check those even on a clean run.

---

## Validator messages

Run `python validate_connector.py .` from the connector directory. It imports PyYAML — if you get
`ModuleNotFoundError: No module named 'yaml'`, run `pip install pyyaml`; the repo doesn't declare
the dependency.

Errors exit non-zero; warnings don't fail the run but are almost always real bugs. The validator
warns rather than errors when it can't be *certain* (an unknown key might be a future feature; an
unmatched placeholder might be intentional), not because the issue is minor. An unknown key is
silently ignored, so a misspelled `validation.endpoint_url` warns once and leaves the connector with
no credential check while still exiting 0.

| Message | Cause | Fix |
|---|---|---|
| `'text' is not a valid type` | There is no `text` type. | Use `string`. Valid: `boolean`, `date`, `float`, `integer`, `json`, `string`, `time`, `timestamp`, `unix_timestamp`. |
| `request.json: expected str, got dict` | `json:` written as nested YAML. | It's a string. Use a `>` folded scalar and paste the JSON body verbatim. Same for `query:`. |
| `request.params: expected dict, got str` | The inverse — `params` *is* a mapping. | Write it as YAML key/value pairs. |
| `unknown key (allowed: endpoint, request)` on `validation.endpoint_url` | The key is `endpoint`. | Rename to `validation.endpoint`. |
| `unknown key` on `_default.yml` `request.params` | Connector-level `request` allows only `url_base`, `method`, `headers`, `cursor_timestamp_format`. | Move params onto the individual endpoints. |
| `placeholder <x> doesn't match any field with cursor: true…` | Cursor placeholder spelling. | It must match the field's `name` character-for-character — `<updatedAt>` for a field named `updatedAt`, not `<updated_at>`. |
| `placeholder <x> … only auth placeholders are valid here` | A `<page>` or cursor token inside `request.endpoint` / `url_base`. | Only auth and `dependency.as` placeholders work in URL-shaped strings. Move pagination into `params`/`json`/`query`. |
| `missing required field: response.path` | Every endpoint needs to say where its records live. | Add `path:` — a list of keys/indices, e.g. `[data, results]`. |
| `fields: must declare at least one field` | Often a child endpoint, assumed to inherit. | Nothing inherits `fields`. Every endpoint declares its own, including children. |
| `'x' does not match any other endpoint file` | `dependency.endpoint` typo, or the parent file was renamed/deleted. | Use the parent's filename without `.yml`. |
| `'x' does not match any file in custom_field_endpoints/` | `custom_field_endpoint` names a file that isn't there. | Filename without `.yml`, inside `custom_field_endpoints/`. |
| `custom field endpoints must not declare a fields: list` | A `fields:` block in a `custom_field_endpoints/*.yml`. | Delete it — that schema is discovered at sync time via `response.custom_field`. |
| `only one type: oauth2 entry is allowed` | Two oauth2 blocks. | One per connector. Multiple `secret`/`variable` entries are fine alongside it. |
| `'x' is a reserved placeholder name` | `dependency.as` set to `page`, `fields`, `timepage_start`, or `timepage_end`. | Pick another name. |
| `'x' is already used by a declared authentication field` | `dependency.as` collides with a credential name. | Rename one of them. |
| `unknown key` inside `pagination` | Key belongs to a different pagination type (e.g. `offset_size` under `type: page`). | Check the allowed key set for that type in `schema.md` § pagination. |
| `status code key should be a number` | `"500":` quoted in YAML. | Unquote it — `500:`. |
| `cursor_timestamp_format … set but 'cursor' is not true` | Format on a non-cursor field. | Either add `cursor: true` or drop the format; it does nothing as written. |
| `file is empty` / `expected a mapping at the top level` | Empty or list-shaped YAML file. | Every file is a mapping. Delete the file if it was a placeholder. |
| `no endpoint .yml files found` | All endpoints are in subdirectories. | Endpoint files live directly at the connector root. Only `custom_field_endpoints/` is read as a subdirectory. |

---

## Validates clean but behaves wrong

The validator never makes an HTTP request, so none of the following are visible to it. Each maps to
a specific claim you made about the API — go back to a real response to settle it.

**Syncs zero rows.** `response.path` doesn't match the actual body. The most common version is a
missing envelope: docs describe the payload, the API wraps it in `{"data": ...}`. Print the top-level
keys of a saved response and walk down from there.

**Some columns are always null.** The field names in `fields:` don't match the response — a
casing difference (`createdAt` vs `created_at`), a nesting level missed, or a GraphQL field that
isn't in the query's selection set. Names must match exactly; use dot notation for nesting.

**Sync fails on some rows but not others.** A field the API sometimes omits or nulls isn't marked
`nullable: true`. Sample more than one record — the first is often the most complete.

**Sync runs forever, or re-reads everything each time.** Either pagination never terminates, or the
cursor isn't being applied. Check that the placeholder in your request matches a `cursor: true`
field's name exactly, and that `cursor_timestamp_format` produces a value the API's filter actually
accepts — try that literal string in a curl and confirm the result set shrinks.

**Duplicate rows across syncs.** Missing `primary_key`, so every sync mints new synthetic keys. Set
it to whatever genuinely identifies a record, composite if needed.

**Pagination stops one page early, or errors on the last page.** Some APIs return a non-2xx once you
page past the end instead of an empty page. Add that status under `http_codes` with `ignore: true` —
it's end-of-pagination, not a failure.

**An endpoint you never configured is syncing (or failing).** A template `example_*.yml` was left at
the repo root. Every `*.yml` there except `_default.yml` is a live endpoint. Delete the unadapted
ones.

**Credentials rejected at connection setup.** `validation.endpoint` is wrong, needs a param the
check doesn't send, or the API validates credentials through a different auth scheme than normal
requests. `validation.request` can carry its own `headers`/`params`/`json` for that case.

**Child endpoint makes an enormous number of requests.** That's inherent — one request per parent
record. If the API offers a bulk endpoint filtered by date, use it instead of a `dependency`.
