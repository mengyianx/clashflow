"""The Overlay: the panel-owned gateway settings applied over every Source
Config at render time (ADR-0004).

One global record, not per Config — TUN, DNS, ports and the log level are
properties of the gateway, not of a Subscription. The record is SPARSE: a key
that is absent is not owned, so the Source Config's own value passes through.
That is what lets M1 ship an Overlay that changes nothing until the user sets
a value.

M1 exposes `log-level`, `mixed-port` / `allow-lan` / `bind-address`, and
`dns.nameserver` / `dns.fallback`. The remaining keys of the decided set (the
`tun` block, `dns.enable` / `listen` / `enhanced-mode` / `fake-ip-range`) are
in the schema so the renderer can express them, but their panel controls land
with the data-plane slice (2.0 M2) — the API accepts them, the M1 panel does
not offer them.

Validation is total and canonical: unknown keys are refused (a typo must not
silently stop overriding), types and ranges are checked, and the returned
mapping follows the schema's key order so every export is byte-identical for
the same state.
"""

from collections.abc import Iterator, Mapping
from typing import Any

import yaml

# The M1 panel surface, in display order. Everything else in SCHEMA is
# accepted by the API (the renderer must be able to express the decided key
# set) but has no control until M2.
EDITABLE_KEYS = (
    "log-level",
    "mixed-port",
    "allow-lan",
    "bind-address",
    "dns.nameserver",
    "dns.fallback",
)

LOG_LEVELS = ("silent", "error", "warning", "info", "debug")
ENHANCED_MODES = ("fake-ip", "redir-host")
TUN_STACKS = ("system", "gvisor", "mixed")


class OverlayError(Exception):
    """The Overlay is malformed; the message names the offending key and is
    safe to show to the user."""


type Spec = dict[str, Any]


def _leaf(kind: str, **constraints: Any) -> Spec:
    return {"kind": kind, **constraints}


def _is_leaf(spec: Spec) -> bool:
    return "kind" in spec


# ADR-0004's decided key set. Nested mappings are gateways for more keys; a
# nested node with no child means "no such key here".
SCHEMA: dict[str, Any] = {
    "log-level": _leaf("enum", values=LOG_LEVELS),
    "mixed-port": _leaf("port"),
    "allow-lan": _leaf("bool"),
    "bind-address": _leaf("text"),
    "tun": {
        "enable": _leaf("bool"),
        "stack": _leaf("enum", values=TUN_STACKS),
        "auto-route": _leaf("bool"),
        "auto-redirect": _leaf("bool"),
        "dns-hijack": _leaf("text-list"),
        "strict-route": _leaf("bool"),
    },
    "dns": {
        "enable": _leaf("bool"),
        "listen": _leaf("text"),
        "enhanced-mode": _leaf("enum", values=ENHANCED_MODES),
        "fake-ip-range": _leaf("text"),
        "nameserver": _leaf("text-list"),
        "fallback": _leaf("text-list"),
    },
}


def validate(values: object) -> dict[str, Any]:
    """Check an Overlay against the schema and return its canonical form.

    Absent keys mean "not owned"; an empty nested mapping collapses to
    absence so `{"dns": {}}` and `{}` are the same state. Every key is emitted
    in schema order, which makes the YAML export stable across saves."""
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise OverlayError("overlay must be a YAML mapping")
    return _validate_node(values, SCHEMA, "")


def _validate_node(values: Mapping[Any, Any], node: dict[str, Any], prefix: str) -> dict[str, Any]:
    for key in values:
        if not isinstance(key, str) or key not in node:
            raise OverlayError(f"{prefix}{key}: not an overlay key")
    out: dict[str, Any] = {}
    for key, spec in node.items():
        if key not in values:
            continue
        value = values[key]
        path = f"{prefix}{key}"
        if _is_leaf(spec):
            out[key] = _validate_leaf(value, spec, path)
            continue
        if not isinstance(value, dict):
            raise OverlayError(f"{path}: expected a mapping")
        child = _validate_node(value, spec, f"{path}.")
        if child:
            out[key] = child
    return out


def _validate_leaf(value: object, spec: Spec, path: str) -> Any:
    kind = spec["kind"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise OverlayError(f"{path}: expected true or false")
        return value
    if kind == "port":
        if isinstance(value, bool) or not isinstance(value, int):
            raise OverlayError(f"{path}: expected a port number")
        if not 1 <= value <= 65535:
            raise OverlayError(f"{path}: port out of range 1-65535: {value}")
        return value
    if kind == "enum":
        if not isinstance(value, str):
            raise OverlayError(f"{path}: expected one of {', '.join(spec['values'])}")
        if value not in spec["values"]:
            raise OverlayError(f"{path}: expected one of {', '.join(spec['values'])}: {value!r}")
        return value
    if kind == "text":
        if not isinstance(value, str):
            raise OverlayError(f"{path}: expected text")
        stripped = value.strip()
        if not stripped:
            raise OverlayError(f"{path}: must not be empty")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in stripped):
            raise OverlayError(f"{path}: must not contain control characters")
        return stripped
    if kind == "text-list":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise OverlayError(f"{path}: expected a list of strings")
        items = [item.strip() for item in value]
        if any(not item or any(ord(ch) < 0x20 for ch in item) for item in items):
            raise OverlayError(f"{path}: entries must be non-empty and single-line")
        return items
    raise AssertionError(f"unknown overlay value kind: {kind}")


def leaf_paths(values: Mapping[str, Any], prefix: str = "") -> Iterator[str]:
    """Every owned leaf as a dotted path, in schema order."""
    for key, value in values.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from leaf_paths(value, f"{path}.")
        else:
            yield path


def export_yaml(values: Mapping[str, Any]) -> str:
    """The Overlay as YAML (canonical order), the backup format the panel's
    export button and its import round-trip."""
    return yaml.safe_dump(
        validate(dict(values)),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=4096,
    )


def import_yaml(text: str) -> dict[str, Any]:
    """Parse an exported Overlay back into a validated state."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OverlayError(f"overlay is not valid YAML: {exc}") from exc
    return validate(parsed)


_ABSENT = object()


def resolve(document: Mapping[str, Any], path: str) -> object:
    """The leaf at a dotted path, or `_ABSENT` when the Source Config does
    not carry that key at all."""
    current: object = document
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _ABSENT
        current = current[part]
    return current


def overridden_keys(source: Mapping[str, Any], overlay: Mapping[str, Any]) -> list[str]:
    """The Source Config keys the Overlay wins: owned leaf paths that exist in
    the Source Config. This is the set the editor marks as overridden — a
    Runtime-only key (e.g. `log-level` absent from the source) is not in the
    editor, so it is not listed."""
    return [path for path in leaf_paths(overlay) if resolve(source, path) is not _ABSENT]


def diff(source: Mapping[str, Any], runtime: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per-key differences between a Source and its Runtime Config.

    Leaves are compared whole (a `nameserver` list is one key); a mapping that
    turns into a scalar — or the reverse — is reported at that path. Keys are
    emitted in source order, then Runtime-only keys, so the same pair always
    produces the same list."""
    rows: list[dict[str, Any]] = []
    _diff_node(source, runtime, "", rows)
    return rows


def _diff_node(left: object, right: object, prefix: str, rows: list[dict[str, Any]]) -> None:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        keys = list(left) + [key for key in right if key not in left]
        for key in keys:
            path = f"{prefix}{key}"
            in_source = key in left
            in_runtime = key in right
            if in_source and in_runtime:
                _diff_node(left[key], right[key], f"{path}.", rows)
            else:
                rows.append(
                    {
                        "path": path,
                        "source": left.get(key),
                        "runtime": right.get(key),
                        "in_source": in_source,
                        "in_runtime": in_runtime,
                    }
                )
        return
    if left != right:
        rows.append(
            {
                "path": prefix.rstrip("."),
                "source": left,
                "runtime": right,
                "in_source": True,
                "in_runtime": True,
            }
        )
