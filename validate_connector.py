"""
Validates a connector folder written in the user-friendly notation described in
README.md (_default.yml, one file per endpoint, and
custom_field_endpoints/*.yml).

Usage:
    python validate_connector_yaml.py [path/to/connector/folder]

Defaults to ./ relative to this script if no path is given.
Exits with status 1 if any errors were found (warnings alone don't fail).
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

VALID_FIELD_TYPES = {
    "boolean",
    "date",
    "float",
    "integer",
    "json",
    "string",
    "timestamp",
    "time",
    "unix_timestamp",
}
VALID_PAGINATION_TYPES = {"offset", "page", "token", "time"}
VALID_AUTH_TYPES = {"secret", "variable", "oauth2"}
STATIC_AUTH_KEYS = {"name", "type", "required"}
OAUTH_KEYS = {
    "name",
    "type",
    "required",
    "access_token_expires_in_seconds",
    "refresh_token_expires_in_seconds",
    "authorization",
    "grant",
    "refresh",
    "revoke",
}
OAUTH_REQUEST_KEYS = {"method", "url", "headers", "params", "json"}
RESERVED_PLACEHOLDERS = {"page", "timepage_start", "timepage_end", "fields"}
OAUTH_AUTHORIZATION_URL_RESERVED_PLACEHOLDERS = {"redirect_url", "state"}
OAUTH_REQUEST_RESERVED_PLACEHOLDERS = {"redirect_url", "code"}
# Plain `<name>` (secret/variable auth fields, cursor fields, reserved tokens) or
# dotted `<name.access_token>` / `<name.refresh_token>` (the connector's oauth2 entry).
PLACEHOLDER_RE = re.compile(r"<([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)?)>")


@dataclass
class Issue:
    file: str
    field: str
    message: str
    level: str = "error"


def err(issues: List[Issue], file: str, field_path: str, message: str) -> None:
    issues.append(Issue(file=file, field=field_path, message=message, level="error"))


def warn(issues: List[Issue], file: str, field_path: str, message: str) -> None:
    issues.append(Issue(file=file, field=field_path, message=message, level="warning"))


def _type_name(expected_type: Any) -> str:
    if isinstance(expected_type, tuple):
        return " or ".join(t.__name__ for t in expected_type)
    return expected_type.__name__


def _join(field_path: str, key: str) -> str:
    return f"{field_path}.{key}" if field_path else key


def require(
    d: Dict,
    key: str,
    expected_type: Any,
    file: str,
    field_path: str,
    issues: List[Issue],
) -> Optional[Any]:
    full_path = _join(field_path, key)
    if key not in d or d[key] is None:
        err(issues, file, full_path, "missing required field")
        return None
    value = d[key]
    if not isinstance(value, expected_type):
        err(
            issues,
            file,
            full_path,
            f"expected {_type_name(expected_type)}, got {type(value).__name__}",
        )
        return None
    return value


def optional(
    d: Dict,
    key: str,
    expected_type: Any,
    file: str,
    field_path: str,
    issues: List[Issue],
) -> Optional[Any]:
    full_path = _join(field_path, key)
    if key not in d or d[key] is None:
        return None
    value = d[key]
    if not isinstance(value, expected_type):
        err(
            issues,
            file,
            full_path,
            f"expected {_type_name(expected_type)}, got {type(value).__name__}",
        )
        return None
    return value


def check_unknown_keys(
    d: Dict, allowed: set, file: str, field_path: str, issues: List[Issue]
) -> None:
    for key in d:
        if key not in allowed:
            warn(
                issues,
                file,
                _join(field_path, str(key)),
                f"unknown key (allowed: {', '.join(sorted(allowed))})",
            )


def validate_path_segments(
    path: List[Any], file: str, field_path: str, issues: List[Issue]
) -> None:
    for i, segment in enumerate(path):
        if not isinstance(segment, (str, int)):
            err(
                issues,
                file,
                f"{field_path}[{i}]",
                f"expected a string or integer, got {type(segment).__name__}",
            )


def load_yaml(path: Path, issues: List[Issue]) -> Optional[Dict]:
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f.read())
    except yaml.YAMLError as e:
        err(issues, path.name, "-", f"could not parse YAML: {e}")
        return None
    if data is None:
        err(issues, path.name, "-", "file is empty")
        return None
    if not isinstance(data, dict):
        err(
            issues,
            path.name,
            "-",
            f"expected a mapping at the top level, got {type(data).__name__}",
        )
        return None
    return data


def extract_placeholders(value: Any) -> set:
    found: set = set()
    if isinstance(value, str):
        found.update(PLACEHOLDER_RE.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            found |= extract_placeholders(v)
    elif isinstance(value, list):
        for v in value:
            found |= extract_placeholders(v)
    return found


def _allowed_oauth_tokens(oauth_name: Optional[str]) -> set:
    if oauth_name is None:
        return set()
    return {f"{oauth_name}.access_token", f"{oauth_name}.refresh_token"}


def validate_auth_only_placeholders(
    value: str,
    auth_field_names: set,
    oauth_name: Optional[str],
    file: str,
    field_path: str,
    issues: List[Issue],
    extra_reserved: Optional[set] = None,
) -> None:
    """For URL-shaped strings (endpoint/url_base/oauth url) - only auth placeholders make
    sense there, not pagination or cursor tokens. `extra_reserved` additionally licenses
    the oauth2-only reserved tokens for authorization.url."""
    unknown = (
        extract_placeholders(value)
        - auth_field_names
        - _allowed_oauth_tokens(oauth_name)
        - (extra_reserved or set())
    )
    for token in sorted(unknown):
        warn(
            issues,
            file,
            field_path,
            f"placeholder <{token}> doesn't match any declared authentication field - "
            f"only auth placeholders are valid here (not pagination/cursor tokens) - typo?",
        )


def validate_placeholders(
    request: Dict,
    cursor_field_names: set,
    auth_field_names: set,
    oauth_name: Optional[str],
    file: str,
    issues: List[Issue],
    field_path: str = "request",
    extra_reserved: Optional[set] = None,
) -> None:
    found: set = set()
    for key in ("params", "json", "query", "headers"):
        if request.get(key) is not None:
            found |= extract_placeholders(request[key])

    allowed_oauth_tokens = _allowed_oauth_tokens(oauth_name)
    reserved = RESERVED_PLACEHOLDERS | (extra_reserved or set())

    unknown = (
        found - reserved - cursor_field_names - auth_field_names - allowed_oauth_tokens
    )
    for token in sorted(unknown):
        warn(
            issues,
            file,
            field_path,
            f"placeholder <{token}> doesn't match any field with cursor: true, any declared "
            f"authentication field, and isn't a reserved token "
            f"({', '.join(sorted(reserved))}) - typo?",
        )

    # endpoint/url_base/url only support auth placeholders (e.g. a per-tenant subdomain) -
    # pagination tokens and cursor fields don't make sense in a URL base or path.
    for key in ("endpoint", "url_base", "url"):
        value = request.get(key)
        if isinstance(value, str):
            validate_auth_only_placeholders(
                value,
                auth_field_names,
                oauth_name,
                file,
                f"{field_path}.{key}",
                issues,
                extra_reserved=extra_reserved,
            )

    fields_format = request.get("fields_format")
    if isinstance(fields_format, dict):
        item = fields_format.get("item")
        if isinstance(item, str):
            unknown_item = extract_placeholders(item) - {"name"}
            for token in sorted(unknown_item):
                warn(
                    issues,
                    file,
                    f"{field_path}.fields_format.item",
                    f"placeholder <{token}> isn't valid here - only <name> is substituted "
                    f"per field",
                )


def validate_http_codes(
    http_codes: Any, file: str, field_path: str, issues: List[Issue]
) -> None:
    if not isinstance(http_codes, dict):
        err(
            issues,
            file,
            field_path,
            f"expected a mapping of status code -> config, got {type(http_codes).__name__}",
        )
        return
    for code, config in http_codes.items():
        code_path = _join(field_path, str(code))
        if not isinstance(code, int):
            warn(
                issues,
                file,
                code_path,
                f"status code key should be a number, got {code!r}",
            )
        if not isinstance(config, dict):
            err(
                issues,
                file,
                code_path,
                f"expected a mapping, got {type(config).__name__}",
            )
            continue
        check_unknown_keys(config, {"ignore", "message"}, file, code_path, issues)
        optional(config, "ignore", bool, file, code_path, issues)
        message = config.get("message")
        if message is not None:
            if isinstance(message, dict):
                check_unknown_keys(
                    message, {"path"}, file, _join(code_path, "message"), issues
                )
                path = require(
                    message, "path", list, file, _join(code_path, "message"), issues
                )
                if path is not None:
                    validate_path_segments(
                        path, file, _join(code_path, "message.path"), issues
                    )
            elif not isinstance(message, str):
                err(
                    issues,
                    file,
                    _join(code_path, "message"),
                    f"expected a string or a {{path: [...]}} mapping, got {type(message).__name__}",
                )


def validate_pagination(
    pagination: Dict, file: str, field_path: str, issues: List[Issue]
) -> None:
    ptype = require(pagination, "type", str, file, field_path, issues)
    if ptype is not None and ptype not in VALID_PAGINATION_TYPES:
        err(
            issues,
            file,
            _join(field_path, "type"),
            f"'{ptype}' is not a valid pagination type (expected one of: {', '.join(sorted(VALID_PAGINATION_TYPES))})",
        )
        return

    allowed = {"type"}
    path: Optional[List[Any]] = None
    if ptype == "offset":
        allowed |= {"offset_size", "initial_value", "path"}
        optional(pagination, "offset_size", int, file, field_path, issues)
        optional(pagination, "initial_value", int, file, field_path, issues)
        path = optional(pagination, "path", list, file, field_path, issues)
    elif ptype == "page":
        allowed |= {"initial_value", "path"}
        optional(pagination, "initial_value", int, file, field_path, issues)
        path = optional(pagination, "path", list, file, field_path, issues)
    elif ptype == "token":
        allowed |= {"path"}
        path = require(pagination, "path", list, file, field_path, issues)
    elif ptype == "time":
        allowed |= {
            "cursor_timestamp_format",
            "history_size_in_days",
            "window_size_in_days",
            "sort",
        }
        require(pagination, "cursor_timestamp_format", str, file, field_path, issues)
        optional(pagination, "history_size_in_days", int, file, field_path, issues)
        require(pagination, "window_size_in_days", int, file, field_path, issues)
        sort = require(pagination, "sort", str, file, field_path, issues)
        if sort is not None and sort not in {"asc", "desc"}:
            err(
                issues,
                file,
                _join(field_path, "sort"),
                f"'{sort}' is not valid (expected 'asc' or 'desc')",
            )

    if path is not None:
        validate_path_segments(path, file, _join(field_path, "path"), issues)

    check_unknown_keys(pagination, allowed, file, field_path, issues)


def validate_request_block(
    request: Any, file: str, issues: List[Issue], require_endpoint: bool = True
) -> Optional[Dict]:
    if not isinstance(request, dict):
        err(
            issues, file, "request", f"expected a mapping, got {type(request).__name__}"
        )
        return None

    allowed = {
        "method",
        "endpoint",
        "params",
        "json",
        "query",
        "headers",
        "cursor_timestamp_format",
        "fields_format",
    }
    check_unknown_keys(request, allowed, file, "request", issues)

    require(request, "method", str, file, "request", issues)
    if require_endpoint:
        require(request, "endpoint", str, file, "request", issues)
    else:
        optional(request, "endpoint", str, file, "request", issues)
    optional(request, "params", dict, file, "request", issues)
    optional(request, "json", str, file, "request", issues)
    optional(request, "query", str, file, "request", issues)
    optional(request, "headers", dict, file, "request", issues)
    optional(request, "cursor_timestamp_format", str, file, "request", issues)

    body_keys = [k for k in ("params", "json", "query") if request.get(k) is not None]
    if len(body_keys) > 1:
        warn(
            issues,
            file,
            "request",
            f"more than one of params/json/query is set ({', '.join(body_keys)}) - only one is normally used",
        )

    fields_format = request.get("fields_format")
    if fields_format is not None:
        if isinstance(fields_format, dict):
            check_unknown_keys(
                fields_format,
                {"item", "separator"},
                file,
                "request.fields_format",
                issues,
            )
            optional(fields_format, "item", str, file, "request.fields_format", issues)
            optional(
                fields_format, "separator", str, file, "request.fields_format", issues
            )
        else:
            err(
                issues,
                file,
                "request.fields_format",
                f"expected a mapping, got {type(fields_format).__name__}",
            )

    return request


def validate_fields(fields: Any, file: str, issues: List[Issue]) -> set:
    cursor_field_names: set = set()
    if not isinstance(fields, list):
        err(issues, file, "fields", f"expected a list, got {type(fields).__name__}")
        return cursor_field_names
    if len(fields) == 0:
        err(issues, file, "fields", "must declare at least one field")
        return cursor_field_names

    seen_names: set = set()
    for i, item in enumerate(fields):
        field_path = f"fields[{i}]"
        if not isinstance(item, dict):
            err(
                issues,
                file,
                field_path,
                f"expected a mapping, got {type(item).__name__}",
            )
            continue

        check_unknown_keys(
            item,
            {"name", "type", "nullable", "cursor", "cursor_timestamp_format"},
            file,
            field_path,
            issues,
        )

        name = require(item, "name", str, file, field_path, issues)
        if name is not None:
            field_path = f"fields[{i}] ({name})"
            if name in seen_names:
                err(issues, file, field_path, f"duplicate field name '{name}'")
            seen_names.add(name)

        ftype = require(item, "type", str, file, field_path, issues)
        if ftype is not None and ftype not in VALID_FIELD_TYPES:
            err(
                issues,
                file,
                _join(field_path, "type"),
                f"'{ftype}' is not a valid type (expected one of: {', '.join(sorted(VALID_FIELD_TYPES))})",
            )

        optional(item, "nullable", bool, file, field_path, issues)
        is_cursor = optional(item, "cursor", bool, file, field_path, issues)
        optional(item, "cursor_timestamp_format", str, file, field_path, issues)
        if item.get("cursor_timestamp_format") is not None and not is_cursor:
            warn(
                issues,
                file,
                _join(field_path, "cursor_timestamp_format"),
                "set but 'cursor' is not true - has no effect",
            )

        if is_cursor and name is not None:
            cursor_field_names.add(name)

    return cursor_field_names


def validate_path_map(
    d: Dict, key: str, file: str, field_path: str, issues: List[Issue]
) -> None:
    sub_path = _join(field_path, key)
    value = d.get(key)
    if value is None:
        err(issues, file, sub_path, "missing required field")
        return
    if not isinstance(value, dict):
        err(
            issues,
            file,
            sub_path,
            f"expected a {{path: [...]}} mapping, got {type(value).__name__}",
        )
        return
    check_unknown_keys(value, {"path"}, file, sub_path, issues)
    path = require(value, "path", list, file, sub_path, issues)
    if path is not None:
        validate_path_segments(path, file, _join(sub_path, "path"), issues)


def validate_oauth_request_block(
    request: Any,
    auth_field_names: set,
    oauth_name: Optional[str],
    file: str,
    field_path: str,
    issues: List[Issue],
) -> None:
    if not isinstance(request, dict):
        err(
            issues,
            file,
            field_path,
            f"expected a mapping, got {type(request).__name__}",
        )
        return
    check_unknown_keys(request, OAUTH_REQUEST_KEYS, file, field_path, issues)
    require(request, "method", str, file, field_path, issues)
    require(request, "url", str, file, field_path, issues)
    optional(request, "headers", dict, file, field_path, issues)
    optional(request, "params", dict, file, field_path, issues)
    optional(request, "json", str, file, field_path, issues)
    validate_placeholders(
        request,
        set(),
        auth_field_names,
        oauth_name,
        file,
        issues,
        field_path,
        extra_reserved=OAUTH_REQUEST_RESERVED_PLACEHOLDERS,
    )


def validate_authentication(
    authentication: Any, file: str, issues: List[Issue]
) -> Tuple[set, Optional[str]]:
    auth_field_names: set = set()
    oauth_name: Optional[str] = None

    if not isinstance(authentication, list):
        err(
            issues,
            file,
            "authentication",
            f"expected a list, got {type(authentication).__name__}",
        )
        return auth_field_names, oauth_name

    # Collected up front so grant/refresh/revoke placeholder checks below can see every
    # declared field regardless of where it's listed relative to the oauth2 entry.
    all_auth_field_names = {
        item["name"]
        for item in authentication
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item.get("type") in ("secret", "variable")
    }
    all_oauth_name = next(
        (
            item["name"]
            for item in authentication
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and item.get("type") == "oauth2"
        ),
        None,
    )

    oauth_entries = 0
    seen_names: set = set()
    for i, item in enumerate(authentication):
        field_path = f"authentication[{i}]"
        if not isinstance(item, dict):
            err(
                issues,
                file,
                field_path,
                f"expected a mapping, got {type(item).__name__}",
            )
            continue

        name = require(item, "name", str, file, field_path, issues)
        if name is not None:
            field_path = f"authentication[{i}] ({name})"
            if name in seen_names:
                err(issues, file, field_path, f"duplicate authentication name '{name}'")
            seen_names.add(name)

        atype = require(item, "type", str, file, field_path, issues)
        if atype is not None and atype not in VALID_AUTH_TYPES:
            err(
                issues,
                file,
                _join(field_path, "type"),
                f"'{atype}' is not a valid type (expected one of: {', '.join(sorted(VALID_AUTH_TYPES))})",
            )
            continue

        optional(item, "required", bool, file, field_path, issues)

        if atype in ("secret", "variable"):
            check_unknown_keys(item, STATIC_AUTH_KEYS, file, field_path, issues)
            if name is not None:
                auth_field_names.add(name)
        elif atype == "oauth2":
            oauth_entries += 1
            check_unknown_keys(item, OAUTH_KEYS, file, field_path, issues)
            require(
                item, "access_token_expires_in_seconds", int, file, field_path, issues
            )
            require(
                item, "refresh_token_expires_in_seconds", int, file, field_path, issues
            )

            authorization = require(
                item, "authorization", dict, file, field_path, issues
            )
            if authorization is not None:
                auth_path = _join(field_path, "authorization")
                check_unknown_keys(
                    authorization, {"url", "callback"}, file, auth_path, issues
                )
                authorization_url = require(
                    authorization, "url", str, file, auth_path, issues
                )
                if authorization_url is not None:
                    validate_auth_only_placeholders(
                        authorization_url,
                        all_auth_field_names,
                        all_oauth_name,
                        file,
                        f"{auth_path}.url",
                        issues,
                        extra_reserved=OAUTH_AUTHORIZATION_URL_RESERVED_PLACEHOLDERS,
                    )
                callback = require(
                    authorization, "callback", dict, file, auth_path, issues
                )
                if callback is not None:
                    callback_path = _join(auth_path, "callback")
                    check_unknown_keys(
                        callback, {"code", "state"}, file, callback_path, issues
                    )
                    validate_path_map(callback, "code", file, callback_path, issues)
                    validate_path_map(callback, "state", file, callback_path, issues)

            grant = require(item, "grant", dict, file, field_path, issues)
            if grant is not None:
                grant_path = _join(field_path, "grant")
                check_unknown_keys(
                    grant, {"request", "response"}, file, grant_path, issues
                )
                grant_request = require(
                    grant, "request", dict, file, grant_path, issues
                )
                if grant_request is not None:
                    validate_oauth_request_block(
                        grant_request,
                        all_auth_field_names,
                        all_oauth_name,
                        file,
                        _join(grant_path, "request"),
                        issues,
                    )
                grant_response = require(
                    grant, "response", dict, file, grant_path, issues
                )
                if grant_response is not None:
                    resp_path = _join(grant_path, "response")
                    check_unknown_keys(
                        grant_response,
                        {"access_token", "refresh_token"},
                        file,
                        resp_path,
                        issues,
                    )
                    validate_path_map(
                        grant_response, "access_token", file, resp_path, issues
                    )
                    validate_path_map(
                        grant_response, "refresh_token", file, resp_path, issues
                    )

            refresh = require(item, "refresh", dict, file, field_path, issues)
            if refresh is not None:
                refresh_path = _join(field_path, "refresh")
                check_unknown_keys(
                    refresh, {"request", "response"}, file, refresh_path, issues
                )
                refresh_request = require(
                    refresh, "request", dict, file, refresh_path, issues
                )
                if refresh_request is not None:
                    validate_oauth_request_block(
                        refresh_request,
                        all_auth_field_names,
                        all_oauth_name,
                        file,
                        _join(refresh_path, "request"),
                        issues,
                    )
                refresh_response = require(
                    refresh, "response", dict, file, refresh_path, issues
                )
                if refresh_response is not None:
                    resp_path = _join(refresh_path, "response")
                    check_unknown_keys(
                        refresh_response,
                        {"access_token", "refresh_token"},
                        file,
                        resp_path,
                        issues,
                    )
                    validate_path_map(
                        refresh_response, "access_token", file, resp_path, issues
                    )
                    validate_path_map(
                        refresh_response, "refresh_token", file, resp_path, issues
                    )

            revoke = require(item, "revoke", dict, file, field_path, issues)
            if revoke is not None:
                revoke_path = _join(field_path, "revoke")
                check_unknown_keys(revoke, {"request"}, file, revoke_path, issues)
                revoke_request = require(
                    revoke, "request", dict, file, revoke_path, issues
                )
                if revoke_request is not None:
                    validate_oauth_request_block(
                        revoke_request,
                        all_auth_field_names,
                        all_oauth_name,
                        file,
                        _join(revoke_path, "request"),
                        issues,
                    )

            if name is not None:
                oauth_name = name

    if oauth_entries > 1:
        err(
            issues,
            file,
            "authentication",
            f"only one type: oauth2 entry is allowed per connector, found {oauth_entries}",
        )

    return auth_field_names, oauth_name


def validate_default_file(
    data: Dict, file: str, issues: List[Issue]
) -> Tuple[set, Optional[str]]:
    check_unknown_keys(
        data,
        {"name", "authentication", "request", "response", "validation"},
        file,
        "",
        issues,
    )

    require(data, "name", str, file, "", issues)

    auth_field_names: set = set()
    oauth_name: Optional[str] = None
    authentication = data.get("authentication")
    if authentication is not None:
        auth_field_names, oauth_name = validate_authentication(
            authentication, file, issues
        )

    request = require(data, "request", dict, file, "", issues)
    if request is not None:
        check_unknown_keys(
            request,
            {"url_base", "method", "headers", "cursor_timestamp_format"},
            file,
            "request",
            issues,
        )
        require(request, "url_base", str, file, "request", issues)
        optional(request, "method", str, file, "request", issues)
        optional(request, "headers", dict, file, "request", issues)
        optional(request, "cursor_timestamp_format", str, file, "request", issues)
        validate_placeholders(
            request, set(), auth_field_names, oauth_name, file, issues
        )

    response = optional(data, "response", dict, file, "", issues)
    if response is not None:
        check_unknown_keys(response, {"http_codes"}, file, "response", issues)
        http_codes = response.get("http_codes")
        if http_codes is not None:
            validate_http_codes(http_codes, file, "response.http_codes", issues)

    validation = optional(data, "validation", dict, file, "", issues)
    if validation is not None:
        check_unknown_keys(
            validation, {"endpoint", "request"}, file, "validation", issues
        )
        endpoint = optional(validation, "endpoint", str, file, "validation", issues)
        if endpoint is not None:
            validate_auth_only_placeholders(
                endpoint,
                auth_field_names,
                oauth_name,
                file,
                "validation.endpoint",
                issues,
            )
        val_request = validation.get("request")
        if val_request is not None:
            if isinstance(val_request, dict):
                check_unknown_keys(
                    val_request,
                    {"method", "headers", "params", "json"},
                    file,
                    "validation.request",
                    issues,
                )
                optional(val_request, "method", str, file, "validation.request", issues)
                optional(
                    val_request, "headers", dict, file, "validation.request", issues
                )
                optional(
                    val_request, "params", dict, file, "validation.request", issues
                )
                optional(val_request, "json", str, file, "validation.request", issues)
                validate_placeholders(
                    val_request,
                    set(),
                    auth_field_names,
                    oauth_name,
                    file,
                    issues,
                    field_path="validation.request",
                )
            else:
                err(
                    issues,
                    file,
                    "validation.request",
                    f"expected a mapping, got {type(val_request).__name__}",
                )

    return auth_field_names, oauth_name


def validate_dependency(
    dependency: Any,
    file: str,
    own_endpoint_name: str,
    all_endpoint_names: set,
    auth_field_names: set,
    issues: List[Issue],
) -> Optional[str]:
    field_path = "dependency"
    if not isinstance(dependency, dict):
        err(
            issues,
            file,
            field_path,
            f"expected a mapping, got {type(dependency).__name__}",
        )
        return None

    check_unknown_keys(
        dependency, {"endpoint", "field", "as"}, file, field_path, issues
    )

    endpoint = require(dependency, "endpoint", str, file, field_path, issues)
    if endpoint is not None:
        if endpoint == own_endpoint_name:
            err(
                issues,
                file,
                _join(field_path, "endpoint"),
                "an endpoint can't depend on itself",
            )
        elif endpoint not in all_endpoint_names:
            err(
                issues,
                file,
                _join(field_path, "endpoint"),
                f"'{endpoint}' does not match any other endpoint file "
                f"({', '.join(sorted(all_endpoint_names)) or 'none found'})",
            )

    require(dependency, "field", str, file, field_path, issues)

    as_name = require(dependency, "as", str, file, field_path, issues)
    if as_name is not None:
        if as_name in RESERVED_PLACEHOLDERS:
            err(
                issues,
                file,
                _join(field_path, "as"),
                f"'{as_name}' is a reserved placeholder name "
                f"({', '.join(sorted(RESERVED_PLACEHOLDERS))}) - choose another",
            )
        elif as_name in auth_field_names:
            err(
                issues,
                file,
                _join(field_path, "as"),
                f"'{as_name}' is already used by a declared authentication field - choose "
                f"another name",
            )

    return as_name


def validate_endpoint_file(
    data: Dict,
    file: str,
    own_endpoint_name: str,
    all_endpoint_names: set,
    custom_field_endpoint_names: set,
    auth_field_names: set,
    oauth_name: Optional[str],
    issues: List[Issue],
) -> None:
    check_unknown_keys(
        data, {"request", "response", "fields", "dependency"}, file, "", issues
    )

    dependency_field_name: Optional[str] = None
    dependency = data.get("dependency")
    if dependency is not None:
        dependency_field_name = validate_dependency(
            dependency,
            file,
            own_endpoint_name,
            all_endpoint_names,
            auth_field_names,
            issues,
        )

    request = require(data, "request", dict, file, "", issues)
    if request is not None:
        request = validate_request_block(request, file, issues)

    response = require(data, "response", dict, file, "", issues)
    if response is not None:
        check_unknown_keys(
            response,
            {
                "path",
                "primary_key",
                "pagination",
                "custom_field_endpoint",
                "http_codes",
            },
            file,
            "response",
            issues,
        )

        path = require(response, "path", list, file, "response", issues)
        if path is not None:
            validate_path_segments(path, file, "response.path", issues)

        primary_key = optional(response, "primary_key", list, file, "response", issues)
        if primary_key is not None:
            for i, pk in enumerate(primary_key):
                if not isinstance(pk, str):
                    err(
                        issues,
                        file,
                        f"response.primary_key[{i}]",
                        f"expected a string, got {type(pk).__name__}",
                    )

        pagination = response.get("pagination")
        if pagination is not None:
            if isinstance(pagination, dict):
                validate_pagination(pagination, file, "response.pagination", issues)
            else:
                err(
                    issues,
                    file,
                    "response.pagination",
                    f"expected a mapping, got {type(pagination).__name__}",
                )

        cf_endpoint = optional(
            response, "custom_field_endpoint", str, file, "response", issues
        )
        if cf_endpoint is not None and cf_endpoint not in custom_field_endpoint_names:
            err(
                issues,
                file,
                "response.custom_field_endpoint",
                f"'{cf_endpoint}' does not match any file in custom_field_endpoints/ "
                f"({', '.join(sorted(custom_field_endpoint_names)) or 'none found'})",
            )

        http_codes = response.get("http_codes")
        if http_codes is not None:
            validate_http_codes(http_codes, file, "response.http_codes", issues)

    fields = require(data, "fields", list, file, "", issues)
    cursor_field_names: set = set()
    if fields is not None:
        cursor_field_names = validate_fields(fields, file, issues)

    if request is not None:
        allowed_names = auth_field_names
        if dependency_field_name is not None:
            allowed_names = auth_field_names | {dependency_field_name}
        validate_placeholders(
            request, cursor_field_names, allowed_names, oauth_name, file, issues
        )


def validate_custom_field_endpoint_file(
    data: Dict, file: str, issues: List[Issue]
) -> None:
    check_unknown_keys(data, {"request", "response"}, file, "", issues)

    if "fields" in data:
        err(
            issues,
            file,
            "fields",
            "custom field endpoints must not declare a fields: list - the schema is discovered "
            "at sync time via response.custom_field",
        )

    request = require(data, "request", dict, file, "", issues)
    if request is not None:
        validate_request_block(request, file, issues, require_endpoint=True)

    response = require(data, "response", dict, file, "", issues)
    if response is not None:
        check_unknown_keys(response, {"path", "custom_field"}, file, "response", issues)

        path = require(response, "path", list, file, "response", issues)
        if path is not None:
            validate_path_segments(path, file, "response.path", issues)

        custom_field = require(response, "custom_field", dict, file, "response", issues)
        if custom_field is not None:
            check_unknown_keys(
                custom_field,
                {"field_key", "field_name", "field_type", "is_nullable", "description"},
                file,
                "response.custom_field",
                issues,
            )
            require(
                custom_field, "field_key", str, file, "response.custom_field", issues
            )
            require(
                custom_field, "field_name", str, file, "response.custom_field", issues
            )
            require(
                custom_field, "field_type", str, file, "response.custom_field", issues
            )


def validate_connector(directory: Path) -> List[Issue]:
    issues: List[Issue] = []

    auth_field_names: set = set()
    oauth_name: Optional[str] = None

    default_file = directory / "_default.yml"
    if not default_file.is_file():
        err(issues, "_default.yml", "-", f"file not found in {directory}")
    else:
        data = load_yaml(default_file, issues)
        if data is not None:
            auth_field_names, oauth_name = validate_default_file(
                data, default_file.name, issues
            )

    custom_field_dir = directory / "custom_field_endpoints"
    custom_field_files = (
        sorted(custom_field_dir.glob("*.yml")) if custom_field_dir.is_dir() else []
    )
    custom_field_endpoint_names = {p.stem for p in custom_field_files}

    for path in custom_field_files:
        data = load_yaml(path, issues)
        if data is not None:
            validate_custom_field_endpoint_file(
                data, f"custom_field_endpoints/{path.name}", issues
            )

    endpoint_files = sorted(
        p for p in directory.glob("*.yml") if p.name != "_default.yml"
    )
    all_endpoint_names = {p.stem for p in endpoint_files}
    for path in endpoint_files:
        data = load_yaml(path, issues)
        if data is not None:
            validate_endpoint_file(
                data,
                path.name,
                path.stem,
                all_endpoint_names,
                custom_field_endpoint_names,
                auth_field_names,
                oauth_name,
                issues,
            )

    if not endpoint_files:
        warn(
            issues, "-", "-", f"no endpoint .yml files found directly under {directory}"
        )

    return issues


def print_report(issues: List[Issue], directory: Path) -> None:
    print(f"Validating connector YAML in: {directory}\n")

    if not issues:
        print("No issues found.")
        return

    by_file: Dict[str, List[Issue]] = {}
    for issue in issues:
        by_file.setdefault(issue.file, []).append(issue)

    for file in sorted(by_file):
        print(file)
        for issue in by_file[file]:
            tag = "ERROR" if issue.level == "error" else "WARN "
            print(f"  [{tag}] {issue.field}: {issue.message}")
        print()

    error_count = sum(1 for i in issues if i.level == "error")
    warning_count = sum(1 for i in issues if i.level == "warning")
    print(
        f"Summary: {error_count} error(s), {warning_count} warning(s) across {len(by_file)} file(s)."
    )


def main() -> int:
    default_target = Path(__file__).parent / "."
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else default_target

    if not directory.is_dir():
        print(f"Not a directory: {directory}")
        return 2

    issues = validate_connector(directory)
    print_report(issues, directory)
    return 1 if any(i.level == "error" for i in issues) else 0


if __name__ == "__main__":
    sys.exit(main())
