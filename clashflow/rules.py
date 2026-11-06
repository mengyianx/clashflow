"""Pure rule and object helpers for render stages 2 and 4 (ADR-0009).

M1 ships them UNWIRED: nothing produces custom rules or object edits yet, so
the pipeline's stages two to four stay pass-through and no endpoint reaches
this module. They ship now because the shapes they encode — where a rule goes,
what a rule may point at, how a bare address is repaired, what an empty group
becomes, which duplicate survives — are the parts later slices would otherwise
have to reverse-engineer out of the kernel's silence.

Each function takes the parsed Config document (a mapping, as
`clashflow.render.load_document` returns) and returns a NEW document: inputs
are never mutated, so a stage can hand its argument on and still fail without
having half-edited it. Behaviour follows the reference implementation the
gateway is replacing (OpenClash's `yml_rules_change.sh`) where that behaviour
is observable in the Kernel: the three bottom anchors, `/32` and `/128`
padding, dropping rules that point at a policy that does not exist, and the
Kernel's own field grammar for which field of a rule is its policy.

M1's scope (issue #39) is exactly these five: anchor insertion, rule-target
validation, `IP-CIDR` padding, empty-group fallback and name de-duplication.
Renaming linkage, provider/group forms and rule filtering land with the
slices that produce their input.
"""

import ipaddress
import re
from collections.abc import Mapping, Sequence
from typing import Any

# Policies the Kernel resolves itself; a rule may point at these without any
# object in the Config carrying the name.
BUILTIN_TARGETS: frozenset[str] = frozenset(
    {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "GLOBAL", "COMPATIBLE"}
)

# Rule types whose second field is an address or a CIDR. `IP-SUFFIX` shares
# the padding repair: the Kernel expects a prefix length there too.
IP_RULE_TYPES: frozenset[str] = frozenset(
    {"IP-CIDR", "IP-CIDR6", "SRC-IP-CIDR", "SRC-IP-CIDR6", "IP-SUFFIX", "SRC-IP-SUFFIX"}
)

# Types whose payload contains commas and which take no params: mihomo parses
# them by taking the LAST field as the target and joining the rest as payload.
_COMMA_PAYLOAD_TYPES: frozenset[str] = frozenset(
    {
        "NOT",
        "OR",
        "AND",
        "SUB-RULE",
        "DOMAIN-REGEX",
        "PROCESS-NAME-REGEX",
        "PROCESS-PATH-REGEX",
    }
)

# `MATCH` (and the legacy `FINAL` alias) has no payload: the target is second.
_TARGET_ONLY_TYPES: frozenset[str] = frozenset({"MATCH", "FINAL"})

ANCHORS: tuple[str, ...] = ("top", "bottom")

# The three bottom anchors, tried in this order: the BT/P2P port rule first,
# then the terminating MATCH/FINAL, then the GEOIP rule that sits above local
# traffic rules. A custom rule list inserted "at the bottom" belongs below the
# gateway's own rules but still above the catch-all, which is the only place
# the Kernel still consults it.
BOTTOM_ANCHORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dst-port-80", re.compile(r"^DST-PORT,\s*80(?:,|$)")),
    ("match", re.compile(r"^(?:MATCH|FINAL)(?:,|$)")),
    ("geoip", re.compile(r"^GEOIP,[A-Za-z]{2}(?:,|$)")),
)


def insert_rules(
    document: Mapping[str, Any], rules: Sequence[str], *, anchor: str = "top"
) -> dict[str, Any]:
    """Insert rules into the document's `rules` list at `anchor`.

    `top` puts them before every existing rule (custom rules win over the
    provider's own); `bottom` puts them before the first of the three anchors
    that exists (see BOTTOM_ANCHORS), or appends them when none does. The
    returned document is new; the input list is untouched."""
    if anchor not in ANCHORS:
        raise ValueError(f"unknown rule anchor: {anchor!r} (expected one of {', '.join(ANCHORS)})")
    existing = list(document.get("rules") or [])
    incoming = [str(rule) for rule in rules]
    if not incoming:
        return dict(document)
    if anchor == "top":
        return {**document, "rules": incoming + existing}
    index = bottom_insert_index(existing)
    return {**document, "rules": existing[:index] + incoming + existing[index:]}


def bottom_insert_index(rules: Sequence[str]) -> int:
    """Where a bottom-anchored rule goes: the index of the last rule matching
    the highest-priority anchor that exists, `len(rules)` when none does."""
    for _, pattern in BOTTOM_ANCHORS:
        for position in range(len(rules) - 1, -1, -1):
            if pattern.match(str(rules[position]).strip()):
                return position
    return len(rules)


def rule_target(rule: str) -> str | None:
    """The policy field, read exactly the way the Kernel reads it
    (`rules/common.ParseRulePayload`): `MATCH`/`FINAL` carry it second, the
    comma-payload types (`AND`/`OR`/`NOT`/`SUB-RULE`/`*-REGEX`) carry it last,
    and every other type carries its payload second and the policy third —
    what follows is params (`no-resolve`, `src`, ...), not a policy.

    None when the rule is too short to carry a policy at all: the Kernel
    refuses such a line as malformed instead of reading a value as a policy."""
    fields = [field.strip() for field in str(rule).split(",")]
    rule_type = fields[0].upper() if fields else ""
    if rule_type in _TARGET_ONLY_TYPES:
        return fields[1] if len(fields) > 1 and fields[1] else None
    if rule_type in _COMMA_PAYLOAD_TYPES:
        return fields[-1] if len(fields) > 1 and fields[-1] else None
    return fields[2] if len(fields) > 2 and fields[2] else None


def known_targets(document: Mapping[str, Any]) -> set[str]:
    """Every policy a rule may legally point at in this document: the
    built-ins, the proxy-group names, the node names and the sub-rule keys."""
    targets = set(BUILTIN_TARGETS)
    targets |= _names(document.get("proxy-groups"))
    targets |= _names(document.get("proxies"))
    sub_rules = document.get("sub-rules")
    if isinstance(sub_rules, Mapping):
        targets |= {str(key) for key in sub_rules}
    return targets


def _names(objects: object) -> set[str]:
    if not isinstance(objects, list):
        return set()
    names: set[str] = set()
    for entry in objects:
        if isinstance(entry, Mapping):
            name = entry.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return names


def validate_rule_targets(document: Mapping[str, Any], rules: Sequence[str]) -> list[str]:
    """One problem string per rule whose policy names nothing that exists.

    A rule pointing at a missing group is not a YAML error: the Kernel loads
    the Config and then routes that traffic nowhere, which reads as "the
    gateway is broken" with no line to look at. Stage 2 turns these into a
    render failure that names the rule (ADR-0009), so nothing is written."""
    targets = known_targets(document)
    problems: list[str] = []
    for position, rule in enumerate(rules, start=1):
        target = rule_target(rule)
        if target is None:
            problems.append(f"rule {position} has no policy: {rule}")
        elif target not in targets:
            problems.append(f"rule {position} points at unknown policy {target!r}: {rule}")
    return problems


def pad_ip_rule(rule: str) -> str:
    """Append the full-length prefix to an IP rule whose address has none:
    `/32` for IPv4, `/128` for IPv6 (`IP-CIDR,192.168.1.5,DIRECT` becomes
    `IP-CIDR,192.168.1.5/32,DIRECT`). Anything that is not an IP rule, already
    carries a prefix, or is not an address is returned unchanged."""
    fields = [field.strip() for field in str(rule).split(",")]
    if len(fields) < 2 or fields[0].upper() not in IP_RULE_TYPES:
        return str(rule)
    address = fields[1]
    if not address or "/" in address:
        return str(rule)
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return str(rule)
    fields[1] = f"{address}/{parsed.max_prefixlen}"
    return ",".join(fields)


def pad_ip_rules(rules: Sequence[str]) -> list[str]:
    """`pad_ip_rule` over a list, order and length unchanged."""
    return [pad_ip_rule(rule) for rule in rules]


def fill_empty_groups(document: Mapping[str, Any]) -> dict[str, Any]:
    """Give every group with neither nodes nor providers the `DIRECT` member.

    An empty `proxies` list is a group the Kernel accepts and cannot use: it
    shows up empty in the panel and every rule pointing at it blackholes.
    `DIRECT` is the safe fallback — the traffic keeps flowing and the group
    stays selectable once a node arrives. A group that receives its members
    from providers (`use`) is NOT empty and is left alone."""
    groups = document.get("proxy-groups")
    if not isinstance(groups, list):
        return dict(document)
    filled: list[Any] = []
    for group in groups:
        if isinstance(group, Mapping) and not _has_members(group):
            filled.append({**group, "proxies": ["DIRECT"]})
        else:
            filled.append(group)
    return {**document, "proxy-groups": filled}


def _has_members(group: Mapping[str, Any]) -> bool:
    proxies = group.get("proxies")
    if isinstance(proxies, list) and proxies:
        return True
    use = group.get("use")
    if isinstance(use, str) and use:
        return True
    return isinstance(use, list) and bool(use)


def dedupe_objects(document: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the first object for each name among `proxies` and
    `proxy-groups`.

    Two nodes under one name make the Kernel refuse the whole Config, and two
    groups under one name make it ambiguous which one a rule selects; both
    arrive through merging provider and hand-written sources. Entries with no
    usable name cannot collide and are kept as they are — dropping them would
    be silent data loss, and the Kernel reports its own error for them."""
    updated = dict(document)
    for key in ("proxies", "proxy-groups"):
        objects = document.get(key)
        if isinstance(objects, list):
            updated[key] = _dedupe_list(objects)
    return updated


def _dedupe_list(objects: Sequence[Any]) -> list[Any]:
    seen: set[str] = set()
    kept: list[Any] = []
    for entry in objects:
        name = entry.get("name") if isinstance(entry, Mapping) else None
        if isinstance(name, str) and name:
            if name in seen:
                continue
            seen.add(name)
        kept.append(entry)
    return kept
