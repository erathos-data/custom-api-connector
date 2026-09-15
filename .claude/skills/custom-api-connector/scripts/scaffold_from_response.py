"""
Turn a saved API response into a draft connector endpoint block.

Reads a real JSON response, finds where the record array lives, and emits a
`response.path` suggestion plus a full `fields:` list with dot-notation names,
inferred types, and nullability — the transcription work that is tedious and
easy to get subtly wrong by hand.

Usage:
    python scaffold_from_response.py response.json
    python scaffold_from_response.py response.json --path data,results
    curl ... | python scaffold_from_response.py -

Output is a draft, not an answer: it is right about structure and names because
it read the actual JSON, but it can only guess at intent. Review which fields
you want synced, and confirm the primary key and cursor before committing.
"""

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# Types the connector notation accepts. `text` is NOT one of them.
VALID_TYPES = {
    "boolean",
    "date",
    "float",
    "integer",
    "json",
    "string",
    "time",
    "timestamp",
    "unix_timestamp",
}

ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?(\.\d+)?$")

PRIMARY_KEY_HINTS = ("id", "uuid", "guid", "key", "code", "number", "slug")
CURSOR_HINTS = ("updated", "modified", "changed", "last_", "edited", "timestamp")
# Plausible unix-second range: 2001-09-09 through 2065-ish. Used only to flag
# integers that are probably timestamps rather than counts.
UNIX_SECONDS_RANGE = (1_000_000_000, 3_000_000_000)
UNIX_MILLIS_RANGE = (1_000_000_000_000, 3_000_000_000_000)

MAX_FLATTEN_DEPTH = 3
MAX_SEARCH_DEPTH = 6


# --------------------------------------------------------------------------
# Finding the record array
# --------------------------------------------------------------------------


def find_record_arrays(
    node: Any, path: Optional[List[Any]] = None, depth: int = 0
) -> List[Tuple[List[Any], int]]:
    """Every list-of-objects in the document, as (path, record_count)."""
    path = path or []
    found: List[Tuple[List[Any], int]] = []
    if depth > MAX_SEARCH_DEPTH:
        return found

    if isinstance(node, list):
        dict_items = [item for item in node if isinstance(item, dict)]
        if dict_items:
            found.append((path, len(dict_items)))
        for i, item in enumerate(node[:3]):  # only the first few branches matter
            found.extend(find_record_arrays(item, path + [i], depth + 1))
    elif isinstance(node, dict):
        for key, value in node.items():
            found.extend(find_record_arrays(value, path + [key], depth + 1))

    return found


def rank_candidates(
    candidates: List[Tuple[List[Any], int]]
) -> List[Tuple[List[Any], int]]:
    """Most records first, shallowest path breaking ties."""
    return sorted(candidates, key=lambda c: (-c[1], len(c[0])))


def resolve_path(document: Any, path: List[Any]) -> Any:
    node = document
    for segment in path:
        node = node[segment]
    return node


# --------------------------------------------------------------------------
# Type inference
# --------------------------------------------------------------------------


def infer_scalar_type(value: Any, field_name: str = "") -> str:
    if isinstance(value, bool):  # bool before int — bool is an int subclass
        return "boolean"
    if isinstance(value, int):
        looks_temporal = any(hint in field_name.lower() for hint in CURSOR_HINTS) or (
            field_name.lower().endswith(("_at", "_date", "_time"))
        )
        in_unix_range = (
            UNIX_SECONDS_RANGE[0] <= value <= UNIX_SECONDS_RANGE[1]
            or UNIX_MILLIS_RANGE[0] <= value <= UNIX_MILLIS_RANGE[1]
        )
        if looks_temporal and in_unix_range:
            return "unix_timestamp"
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        if ISO_DATETIME_RE.match(value):
            return "timestamp"
        if DATE_RE.match(value):
            return "date"
        if TIME_RE.match(value):
            return "time"
        return "string"
    return "json"


def merge_types(types: set) -> str:
    """Reconcile types seen across records for the same key."""
    types = {t for t in types if t is not None}
    if not types:
        return "string"
    if len(types) == 1:
        return next(iter(types))
    if "json" in types:
        return "json"
    if types <= {"integer", "float"}:
        return "float"
    if types <= {"timestamp", "date", "time", "string"}:
        # Mixed temporal formats are safer stored as text than mis-parsed.
        return "string" if "string" in types else "timestamp"
    return "string"


# --------------------------------------------------------------------------
# Flattening
# --------------------------------------------------------------------------


def flatten(
    record: Dict[str, Any], prefix: str = "", depth: int = 0
) -> Dict[str, Any]:
    """Nested objects become dot-notation keys; lists stay whole as json."""
    flat: Dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and depth < MAX_FLATTEN_DEPTH:
            if not value:
                flat[name] = {}
                continue
            flat.update(flatten(value, prefix=f"{name}.", depth=depth + 1))
        else:
            flat[name] = value
    return flat


def resolve_prefix_collisions(
    profile: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """Drop parent keys that are also flattened into children.

    A field that is an object in one record and null in another shows up twice:
    once as a bare leaf (`assignee`, from the null) and once flattened
    (`assignee.id`). Only the flattened form is real — but the null parent is
    what tells us the children are nullable, so carry that over before dropping
    it, and keep the children where the parent sat so related fields stay together.
    """
    names = list(profile)
    parents = {
        name
        for name in names
        if any(other.startswith(name + ".") for other in names)
    }
    if not parents:
        return profile

    for parent in parents:
        if profile[parent]["nullable"]:
            for name in names:
                if name.startswith(parent + "."):
                    profile[name]["nullable"] = True

    resolved: Dict[str, Dict[str, Any]] = {}
    for name in names:
        if name in parents:
            for child in names:
                if child.startswith(name + ".") and child not in resolved:
                    resolved[child] = profile[child]
        elif name not in resolved:
            resolved[name] = profile[name]
    return resolved


def profile_records(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-field: merged type, nullability, uniqueness — across all records."""
    flattened = [flatten(r) for r in records if isinstance(r, dict)]
    all_names: List[str] = []
    for record in flattened:
        for name in record:
            if name not in all_names:
                all_names.append(name)  # preserve first-seen order

    profile: Dict[str, Dict[str, Any]] = {}
    for name in all_names:
        types = set()
        nullable = False
        values = []
        for record in flattened:
            if name not in record:
                nullable = True
                continue
            value = record[name]
            if value is None:
                nullable = True
                continue
            types.add(infer_scalar_type(value, name))
            if isinstance(value, (str, int, float, bool)):
                values.append(value)

        profile[name] = {
            "type": merge_types(types),
            "nullable": nullable,
            "unique": len(values) == len(flattened) and len(set(values)) == len(values),
            "present_in_all": not nullable,
        }
    return resolve_prefix_collisions(profile)


# --------------------------------------------------------------------------
# Suggestions
# --------------------------------------------------------------------------


def suggest_primary_key(profile: Dict[str, Dict[str, Any]]) -> Optional[str]:
    for name, info in profile.items():
        leaf = name.split(".")[-1].lower()
        if leaf == "id" and info["present_in_all"]:
            return name
    for name, info in profile.items():
        leaf = name.split(".")[-1].lower()
        if (
            info["present_in_all"]
            and info["unique"]
            and any(leaf == hint or leaf.endswith("_" + hint) for hint in PRIMARY_KEY_HINTS)
        ):
            return name
    return None


def suggest_cursor(profile: Dict[str, Dict[str, Any]]) -> Optional[str]:
    temporal = {"timestamp", "unix_timestamp", "date"}
    for name, info in profile.items():
        if info["type"] in temporal and any(
            hint in name.lower() for hint in CURSOR_HINTS
        ):
            return name
    return None


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_path(path: List[Any]) -> str:
    return "[" + ", ".join(str(segment) for segment in path) + "]"


def render(
    path: List[Any],
    profile: Dict[str, Dict[str, Any]],
    sample_size: int,
    candidates: List[Tuple[List[Any], int]],
    chosen_index: int,
) -> str:
    out: List[str] = []
    primary_key = suggest_primary_key(profile)
    cursor = suggest_cursor(profile)

    if len(candidates) > 1:
        out.append("# Other list-of-objects found in this response:")
        for i, (candidate_path, count) in enumerate(candidates):
            if i == chosen_index:
                continue
            out.append(
                f"#   {render_path(candidate_path)}  ({count} record"
                f"{'s' if count != 1 else ''})"
            )
        out.append("# Pass --path to scaffold from one of those instead.")
        out.append("")

    out.append("response:")
    if path:
        out.append(f"  path: {render_path(path)}")
    else:
        out.append("  path: []  # records sit at the top level of the response")
    if primary_key:
        out.append(f"  primary_key: [{primary_key}]")
    else:
        out.append(
            "  # primary_key: [???]  # no obvious unique identifier — set this if you can, "
            "otherwise rows get a new synthetic key every sync"
        )
    out.append(
        "  # pagination: add unless this endpoint returns everything in one request"
    )
    out.append("")

    out.append("fields:")
    for name, info in profile.items():
        out.append(f"  - name: {name}")
        line = f"    type: {info['type']}"
        if info["type"] == "json":
            line += "  # nested object/array kept whole — use dot notation instead to flatten it"
        elif info["type"] == "unix_timestamp":
            line += "  # integer in a plausible unix-time range — confirm it is a timestamp"
        out.append(line)
        if info["nullable"]:
            out.append("    nullable: true")
        if name == cursor:
            out.append("    cursor: true")
            out.append(
                "    # cursor_timestamp_format: set to the format the API's filter expects,"
            )
            out.append(
                f"    #   then reference it in the request as <{name}>"
            )

    notes: List[str] = []
    if sample_size < 3:
        notes.append(
            f"Only {sample_size} record(s) sampled — nullability is unreliable. "
            "Fetch a larger page and re-run."
        )
    if not cursor:
        notes.append(
            "No obvious 'last changed' field found. Without a cursor the endpoint "
            "re-reads everything each sync; check whether the API supports an updated-since filter."
        )
    if any(info["type"] == "json" for info in profile.values()):
        notes.append(
            "Some fields are nested objects/arrays typed as json. Flatten the ones you "
            "actually query with dot notation (e.g. `state.name`)."
        )
    if notes:
        out.append("")
        out.append("# --- review before committing ---")
        for note in notes:
            out.append(f"# - {note}")

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Draft a connector endpoint block from a real API response."
    )
    parser.add_argument("response", help="Path to a saved JSON response, or - for stdin")
    parser.add_argument(
        "--path",
        help="Comma-separated path to the record array (e.g. data,results). "
        "Bare integers are treated as list indices. Defaults to the largest "
        "list-of-objects found.",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=50,
        help="How many records to profile (default 50).",
    )
    args = parser.parse_args()

    try:
        raw = sys.stdin.read() if args.response == "-" else open(args.response).read()
    except OSError as e:
        print(f"Could not read response: {e}", file=sys.stderr)
        return 2

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Not valid JSON: {e}", file=sys.stderr)
        return 2

    candidates = rank_candidates(find_record_arrays(document))

    if args.path is not None:
        path: List[Any] = [
            int(segment) if segment.strip().lstrip("-").isdigit() else segment.strip()
            for segment in args.path.split(",")
            if segment.strip()
        ]
        try:
            records = resolve_path(document, path)
        except (KeyError, IndexError, TypeError):
            print(f"No such path in this response: {render_path(path)}", file=sys.stderr)
            if candidates:
                print("Lists of objects found:", file=sys.stderr)
                for candidate_path, count in candidates:
                    print(f"  {render_path(candidate_path)}  ({count})", file=sys.stderr)
            return 1
        if not isinstance(records, list):
            print(
                f"{render_path(path)} is a {type(records).__name__}, not a list of records",
                file=sys.stderr,
            )
            return 1
        chosen_index = next(
            (i for i, (p, _) in enumerate(candidates) if p == path), -1
        )
    else:
        if not candidates:
            print(
                "No list of objects found in this response. If the endpoint returns a "
                "single object rather than a collection, pass --path to point at it "
                "explicitly.",
                file=sys.stderr,
            )
            return 1
        path = candidates[0][0]
        records = resolve_path(document, path)
        chosen_index = 0

    records = [r for r in records if isinstance(r, dict)][: args.max_records]
    if not records:
        print(f"{render_path(path)} contains no objects to profile", file=sys.stderr)
        return 1

    profile = profile_records(records)
    print(render(path, profile, len(records), candidates, chosen_index))
    return 0


if __name__ == "__main__":
    sys.exit(main())
