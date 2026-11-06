"""The render pipeline: Source Config bytes + Overlay -> Runtime Config bytes.

Four stages, each `stage(source: bytes, overlay: Overlay, ctx: RenderContext)
-> bytes` and pure (ADR-0009):

1. key merge — the Overlay's owned keys overwrite the source's; this is the
   only stage that changes bytes in M1;
2. rule injection — pass-through in M1 (nothing produces custom rules yet);
3. domain / sniffer / naming policy — pass-through in M1;
4. object add/remove — pass-through in M1 (no group/node/provider editing yet).

Stages are injectable so a test can supply stubs, and a failure is always a
`RenderFailed` carrying the offending stage's name: the panel can then say
"key-merge: ..." instead of dumping a whole YAML file. Later stages land
behind their own ticket (#39) without reshaping this seam.

Determinism: the same source bytes plus the same Overlay must produce
byte-identical output — no timestamps, no hash-ordered maps, source key order
preserved, Overlay-only keys appended in the Overlay schema's order. Comments
and the source's exact formatting are not preserved; a Runtime Config carries
no user content (its Source Config stays the editable truth).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from clashflow import overlay as overlay_module


class RenderFailed(Exception):
    """A render stage refused the input. `stage` is the stage that failed and
    `detail` its reason; `str(exc)` begins with the stage name so the API's
    error text is self-locating."""

    def __init__(self, stage: str, detail: str) -> None:
        super().__init__(f"{stage}: {detail}")
        self.stage = stage
        self.detail = detail


@dataclass(frozen=True)
class RenderContext:
    """Reserved for stages that need request context. M1's stages need none;
    the parameter exists so the stage signature never has to change."""


type Stage = Callable[[bytes, Mapping[str, Any], RenderContext], bytes]


def load_document(source: bytes) -> dict[str, Any]:
    """Parse Source Config bytes into the mapping the stages edit.

    A Source Config must be a YAML mapping: the Kernel refuses anything else,
    and a zero-byte/comment-only file is not the empty mapping either — the
    panel's empty Config is the explicit `{}` the Kernel loads as
    defaults-only. Refusing `null` here keeps that distinction alive now that
    the Kernel no longer validates the Source bytes themselves."""
    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise RenderFailed("key-merge", f"source is not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise RenderFailed("key-merge", "source must be a YAML mapping")
    return document


def dump_document(document: Mapping[str, Any]) -> bytes:
    """Canonical bytes: schema/source key order kept, block style, UTF-8."""
    return yaml.safe_dump(
        dict(document),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=4096,
    ).encode()


def key_merge(source: bytes, overlay: Mapping[str, Any], ctx: RenderContext) -> bytes:
    """Stage 1: the Overlay wins. Owned leaves overwrite the Source Config's
    value in place (source key order kept); owned keys the source lacks are
    appended in Overlay order. Nested mappings merge recursively, so an
    Overlay that owns only `dns.nameserver` leaves the source's other DNS
    keys alone."""
    document = load_document(source)
    # Validate here as well: a stage is a pure function of its arguments, and
    # an unvalidated mapping must fail as a render failure, not write a
    # malformed Runtime Config.
    merged = _merge(document, overlay_module.validate(dict(overlay)))
    return dump_document(merged)


def _merge(document: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, Mapping):
            target = document.get(key)
            if not isinstance(target, dict):
                target = {}
                document[key] = target
            _merge(target, value)
        else:
            document[key] = value
    return document


def passthrough(source: bytes, overlay: Mapping[str, Any], ctx: RenderContext) -> bytes:
    """Stages 2-4 in M1: nothing produces their input yet, so the bytes go
    through untouched (ADR-0009's staged rollout)."""
    return source


DEFAULT_STAGES: tuple[tuple[str, Stage], ...] = (
    ("key-merge", key_merge),
    ("rule-injection", passthrough),
    ("domain-sniff", passthrough),
    ("objects", passthrough),
)


def render(
    source: bytes,
    overlay: Mapping[str, Any],
    ctx: RenderContext | None = None,
    stages: Sequence[tuple[str, Stage]] = DEFAULT_STAGES,
) -> bytes:
    """Run the stages in order; the first failure names its stage.

    A stage that raises `RenderFailed` keeps its own attribution (it knows its
    name); anything else is attributed to the stage that raised it, so no
    failure escapes unnamed."""
    context = ctx or RenderContext()
    data = source
    for name, stage in stages:
        try:
            data = stage(data, overlay, context)
        except RenderFailed:
            raise
        except Exception as exc:
            raise RenderFailed(name, str(exc) or type(exc).__name__) from exc
    return data
