"""Issue #39: the pure rule/object helpers stages 2 and 4 will use.

Unit tests only: in M1 nothing produces custom rules or object edits, so the
helpers are library code with no API surface (see test_render_stages.py for
the pass-through guarantee that keeps them unwired).
"""

import copy
from typing import Any

import pytest

from clashflow.rules import (
    BUILTIN_TARGETS,
    bottom_insert_index,
    dedupe_objects,
    fill_empty_groups,
    insert_rules,
    known_targets,
    pad_ip_rule,
    pad_ip_rules,
    rule_target,
    validate_rule_targets,
)


def _document(**extra: Any) -> dict[str, Any]:
    return {
        "mixed-port": 7890,
        "proxies": [
            {"name": "n1", "type": "http", "server": "127.0.0.1", "port": 1080},
            {"name": "n2", "type": "http", "server": "127.0.0.1", "port": 1081},
        ],
        "proxy-groups": [
            {"name": "Proxy", "type": "select", "proxies": ["n1", "n2"]},
            {"name": "Auto", "type": "url-test", "proxies": ["n1"]},
        ],
        "rules": ["DOMAIN-SUFFIX,provider.example,Proxy", "MATCH,DIRECT"],
        **extra,
    }


# --- anchor placement ------------------------------------------------------


def test_top_anchor_puts_the_rules_before_every_existing_one() -> None:
    document = _document()
    before = copy.deepcopy(document)

    out = insert_rules(document, ["DOMAIN,ads.example,REJECT"])

    assert out["rules"] == [
        "DOMAIN,ads.example,REJECT",
        "DOMAIN-SUFFIX,provider.example,Proxy",
        "MATCH,DIRECT",
    ]
    # The input document is never mutated.
    assert document == before


def test_bottom_anchor_prefers_the_port_rule_over_the_later_catch_all() -> None:
    rules = ["GEOIP,CN,DIRECT", "DST-PORT,80,Proxy", "MATCH,DIRECT"]

    out = insert_rules({"rules": rules}, ["DOMAIN,x.example,Proxy"], anchor="bottom")

    # Inserted before DST-PORT,80 — not before MATCH, which comes later in the
    # list but ranks below the port anchor.
    assert out["rules"] == [
        "GEOIP,CN,DIRECT",
        "DOMAIN,x.example,Proxy",
        "DST-PORT,80,Proxy",
        "MATCH,DIRECT",
    ]


def test_bottom_anchor_falls_back_to_match_then_geoip_then_the_end() -> None:
    assert insert_rules(
        {"rules": ["GEOIP,CN,DIRECT", "MATCH,DIRECT"]}, ["DOMAIN,a,Proxy"], anchor="bottom"
    )["rules"] == ["GEOIP,CN,DIRECT", "DOMAIN,a,Proxy", "MATCH,DIRECT"]
    assert insert_rules(
        {"rules": ["GEOIP,CN,DIRECT", "DOMAIN-SUFFIX,x,Proxy"]}, ["DOMAIN,a,Proxy"], anchor="bottom"
    )["rules"] == ["DOMAIN,a,Proxy", "GEOIP,CN,DIRECT", "DOMAIN-SUFFIX,x,Proxy"]
    # No anchor at all: append, so the rules still exist somewhere valid.
    assert insert_rules({"rules": ["DOMAIN-SUFFIX,x,Proxy"]}, ["DOMAIN,a,Proxy"], anchor="bottom")[
        "rules"
    ] == ["DOMAIN-SUFFIX,x,Proxy", "DOMAIN,a,Proxy"]
    # No rules key at all: the document gains one.
    assert insert_rules({}, ["MATCH,DIRECT"], anchor="bottom")["rules"] == ["MATCH,DIRECT"]


def test_bottom_insert_index_looks_at_the_last_matching_rule() -> None:
    assert bottom_insert_index([]) == 0
    assert bottom_insert_index(["MATCH,DIRECT"]) == 0
    assert bottom_insert_index(["MATCH,DIRECT", "GEOIP,CN,DIRECT"]) == 0
    assert bottom_insert_index(["DOMAIN,x,DIRECT", "FINAL,Proxy"]) == 1
    assert bottom_insert_index(["DOMAIN,x,DIRECT"]) == 1  # append


def test_an_unknown_anchor_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown rule anchor"):
        insert_rules(_document(), ["MATCH,DIRECT"], anchor="middle")


def test_inserting_nothing_keeps_the_document_as_it_is() -> None:
    document = _document()
    assert insert_rules(document, [], anchor="top") == document


# --- rule targets ----------------------------------------------------------


def test_a_rule_target_is_read_the_way_the_kernel_reads_it() -> None:
    assert rule_target("MATCH,DIRECT") == "DIRECT"
    assert rule_target("FINAL,Proxy") == "Proxy"
    # The policy is the third field; what follows is params, not a policy.
    assert rule_target("IP-CIDR,10.0.0.0/8,DIRECT,no-resolve") == "DIRECT"
    assert rule_target("IP-CIDR,1.1.1.1/32,DIRECT,foo") == "DIRECT"
    assert rule_target("PROCESS-NAME,curl,DIRECT,src") == "DIRECT"
    # Comma-payload types carry the policy as their LAST field.
    assert rule_target("AND,((DOMAIN,a),(DOMAIN,b)),Proxy") == "Proxy"
    assert rule_target("SUB-RULE,(NETWORK,tcp),tail") == "tail"
    assert rule_target("DOMAIN-REGEX,^abc.*com,Proxy") == "Proxy"
    # Too short to carry a policy: the Kernel refuses these as malformed
    # instead of reading the value as one.
    assert rule_target("DOMAIN,a.example") is None
    assert rule_target("IP-CIDR,10.0.0.0/8") is None
    assert rule_target("MATCH") is None
    assert rule_target("") is None


def test_known_targets_covers_builtins_groups_nodes_and_sub_rules() -> None:
    document = _document(**{"sub-rules": {"tail": ["MATCH,DIRECT"]}})

    targets = known_targets(document)

    assert BUILTIN_TARGETS <= targets
    assert {"Proxy", "Auto", "n1", "n2", "tail"} <= targets


def test_missing_rule_targets_are_reported_by_position_and_name() -> None:
    document = _document()

    problems = validate_rule_targets(
        document,
        [
            "MATCH,Proxy",  # an existing group
            "DOMAIN,a.example,Ghost",  # no such group or node
            "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",  # builtin, with modifier
            "DOMAIN,b.example",  # no policy field at all
            "RULE-SET,missing,n1",  # a node is a legal target too
        ],
    )

    assert problems == [
        "rule 2 points at unknown policy 'Ghost': DOMAIN,a.example,Ghost",
        "rule 4 has no policy: DOMAIN,b.example",
    ]


def test_a_clean_rule_list_reports_nothing() -> None:
    document = _document()
    assert validate_rule_targets(document, ["MATCH,Proxy", "DOMAIN-SUFFIX,x.example,REJECT"]) == []


# --- IP-CIDR padding -------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("IP-CIDR,192.168.1.5,DIRECT", "IP-CIDR,192.168.1.5/32,DIRECT"),
        ("IP-CIDR6,2001:db8::1,Proxy", "IP-CIDR6,2001:db8::1/128,Proxy"),
        ("SRC-IP-CIDR,10.1.2.3,DIRECT", "SRC-IP-CIDR,10.1.2.3/32,DIRECT"),
        ("IP-SUFFIX,8.8.8.8,Proxy", "IP-SUFFIX,8.8.8.8/32,Proxy"),
        # Already a prefix: untouched.
        ("IP-CIDR,10.0.0.0/8,DIRECT", "IP-CIDR,10.0.0.0/8,DIRECT"),
        ("IP-CIDR6,2001:db8::/32,DIRECT", "IP-CIDR6,2001:db8::/32,DIRECT"),
        # Not an IP rule type: untouched, even with a bare address.
        ("DOMAIN-SUFFIX,1.2.3.4,DIRECT", "DOMAIN-SUFFIX,1.2.3.4,DIRECT"),
        # Not an address: untouched.
        ("IP-CIDR,not-an-address,DIRECT", "IP-CIDR,not-an-address,DIRECT"),
        ("IP-CIDR,DIRECT", "IP-CIDR,DIRECT"),
    ],
)
def test_bare_addresses_get_their_full_prefix(rule: str, expected: str) -> None:
    assert pad_ip_rule(rule) == expected


def test_padding_a_list_keeps_order_and_length() -> None:
    rules = ["IP-CIDR,1.2.3.4,DIRECT", "MATCH,Proxy", "IP-CIDR6,::1,Proxy"]
    assert pad_ip_rules(rules) == [
        "IP-CIDR,1.2.3.4/32,DIRECT",
        "MATCH,Proxy",
        "IP-CIDR6,::1/128,Proxy",
    ]


# --- empty groups ----------------------------------------------------------


def test_a_group_with_no_members_falls_back_to_direct() -> None:
    document = {
        "proxy-groups": [
            {"name": "empty", "type": "select", "proxies": []},
            {"name": "missing", "type": "select"},
            {"name": "from-provider", "type": "url-test", "use": ["remote"]},
            {"name": "full", "type": "select", "proxies": ["n1"]},
        ]
    }
    before = copy.deepcopy(document)

    groups = fill_empty_groups(document)["proxy-groups"]

    assert groups[0]["proxies"] == ["DIRECT"]
    assert groups[1]["proxies"] == ["DIRECT"]
    # A provider-backed group has its members elsewhere: not empty.
    assert "proxies" not in groups[2]
    assert groups[3]["proxies"] == ["n1"]
    assert document == before


def test_filling_groups_is_a_no_op_without_groups() -> None:
    assert fill_empty_groups({"mixed-port": 7890}) == {"mixed-port": 7890}


# --- de-duplication --------------------------------------------------------


def test_duplicate_names_keep_the_first_object() -> None:
    proxies: list[dict[str, object]] = [
        {"name": "a", "port": 1},
        {"name": "a", "port": 2},
        {"name": "b", "port": 3},
        {"type": "no-name"},
    ]
    document: dict[str, Any] = {
        "proxies": proxies,
        "proxy-groups": [
            {"name": "g", "proxies": ["a"]},
            {"name": "g", "proxies": ["b"]},
        ],
    }

    out = dedupe_objects(document)

    assert out["proxies"] == [
        {"name": "a", "port": 1},
        {"name": "b", "port": 3},
        {"type": "no-name"},
    ]
    assert out["proxy-groups"] == [{"name": "g", "proxies": ["a"]}]
    # The input is untouched.
    assert len(proxies) == 4
