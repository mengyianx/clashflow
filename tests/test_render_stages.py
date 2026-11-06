"""Issue #39: the pipeline keeps its stage seam and stages two to four stay
pass-through in M1.

The helpers in `clashflow.rules` are library code: no endpoint exposes them
and the app never imports the module, so a later slice can wire them in
without reshaping the seam (ADR-0009's staged rollout).
"""

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from clashflow.render import DEFAULT_STAGES, RenderContext, key_merge, render

REPO_ROOT = Path(__file__).resolve().parent.parent

SOURCE = b"""\
mixed-port: 7890
proxies:
  - name: n1
    type: http
    server: 127.0.0.1
    port: 1080
proxy-groups:
  - name: Proxy
    type: select
    proxies: [n1]
rules:
  - MATCH,Proxy
"""


def test_stages_two_to_four_return_their_input_byte_for_byte() -> None:
    # Nothing produces rule, sniffer or object input in M1, so each stage must
    # hand its argument on unchanged — including a document that already has
    # rules and groups.
    for name, stage in DEFAULT_STAGES[1:]:
        assert stage(SOURCE, {"log-level": "debug"}, RenderContext()) == SOURCE, name


def test_a_render_with_no_rule_or_object_input_only_merges_the_overlay() -> None:
    merged = key_merge(SOURCE, {"log-level": "debug"}, RenderContext())
    assert render(SOURCE, {"log-level": "debug"}) == merged


def test_the_stage_signature_is_fixed_and_a_stage_appends_without_reshaping() -> None:
    assert [name for name, _ in DEFAULT_STAGES] == [
        "key-merge",
        "rule-injection",
        "domain-sniff",
        "objects",
    ]
    calls: list[str] = []

    def later(data: bytes, overlay: Mapping[str, Any], ctx: RenderContext) -> bytes:
        calls.append("later")
        return data

    out = render(SOURCE, {}, stages=(*DEFAULT_STAGES, ("later", later)))
    assert calls == ["later"]
    assert out == render(SOURCE, {})


def test_the_app_never_imports_the_rule_helpers() -> None:
    # Unwired means unwired: booting the module graph must not pull the helper
    # module in, so nothing can reach it before its stage exists.
    code = "import sys, clashflow.main; print('clashflow.rules' in sys.modules)"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == "False"


def test_no_panel_route_exposes_the_helpers() -> None:
    from clashflow.main import create_app

    app = create_app(data_dir=REPO_ROOT / "tests" / "no-such-volume", kernel_bin="mihomo")
    paths = [getattr(route, "path", "") for route in app.routes]
    assert not [path for path in paths if "rule" in path or "object" in path]
