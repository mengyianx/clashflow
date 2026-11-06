"""Config summary (issue #43): what a Config contains, counted from the bytes
the Kernel would run.

The panel shows a summary only for bytes that passed validation, so the
counts describe the Config the Kernel actually accepted — not a document the
user is still editing. Counting is deliberately shallow: `proxies`,
`proxy-groups` and `rules` are top-level lists in mihomo's schema, so the
lengths of those lists are the node, group and rule counts. Provider-backed
nodes are NOT counted (a `proxy-providers` entry resolves its nodes at
runtime), which is why a config can legitimately carry providers and report
zero nodes.

`age_key_missing` answers "would this Config need an Age key the panel does
not have?". The observable signal is the Age armor header mihomo decrypts
(`component/age`: a file that does not start with it is used as-is), and the
panel has no Age key store yet — ADR-0009 assigns that to the subscription
stage, together with passing the key to `mihomo -t` and the Kernel process.
Until then the flag is true for any armored Config, which is exactly the
state a self-hoster needs to see; a Config that is already plain YAML reports
false. The flag is part of the API shape from M1 so the panel does not change
when the key store lands.

Pure and I/O-free: the caller passes bytes in and gets a value out. A
document that does not parse yields zeros instead of raising — the render and
validation pipeline is what refuses unusable bytes, and a summary is never
worth an exception on a read path.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

# mihomo's Age armor header (`component/age.FileHeader`): a Config starting
# with it is decrypted before YAML parsing, and decrypting needs a key.
AGE_ARMOR_HEADER = "-----BEGIN AGE ENCRYPTED FILE-----"


@dataclass(frozen=True)
class ConfigSummary:
    """The counts the panel shows for a Config, and whether it needs an Age
    key the panel does not hold."""

    nodes: int
    groups: int
    rules: int
    age_key_missing: bool

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "nodes": self.nodes,
            "groups": self.groups,
            "rules": self.rules,
            "age_key_missing": self.age_key_missing,
        }


def _count(document: Mapping[str, Any], key: str) -> int:
    """The length of a top-level list; absent, null or a non-list counts as
    zero (an empty or minimal Config reports zeros, never an error)."""
    value = document.get(key)
    return len(value) if isinstance(value, list) else 0


def summarize(source: bytes, rendered: bytes) -> ConfigSummary:
    """Count `rendered` and report whether `source` is Age-encrypted."""
    try:
        document = yaml.safe_load(rendered)
    except yaml.YAMLError:
        document = None
    if not isinstance(document, Mapping):
        document = {}
    return ConfigSummary(
        nodes=_count(document, "proxies"),
        groups=_count(document, "proxy-groups"),
        rules=_count(document, "rules"),
        age_key_missing=source.lstrip().startswith(AGE_ARMOR_HEADER.encode()),
    )
