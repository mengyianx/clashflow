"""One mapping from apply-pipeline failures to the panel's HTTP answers.

The save, switch, copy and Overlay-save surfaces all run the same pipeline and
must answer the same way: a missing file is 404, a render-stage or Kernel
refusal is 400 (render messages already begin with the stage name; the
Kernel's output is verbatim), a refused reload is 502. Keeping one function
means a new surface cannot invent a fourth shape.
"""

from typing import NoReturn

from fastapi import HTTPException

from clashflow.apply import (
    ConfigFileMissing,
    HotApplyError,
    KernelRejected,
    RenderedConfigInvalid,
)
from clashflow.render import RenderFailed

type CommitFailure = (
    ConfigFileMissing | RenderFailed | RenderedConfigInvalid | KernelRejected | HotApplyError
)


def raise_for_commit_failure(exc: CommitFailure) -> NoReturn:
    """Raise the HTTPException for a pipeline failure, chained to it."""
    if isinstance(exc, ConfigFileMissing):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (RenderFailed, RenderedConfigInvalid)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, KernelRejected):
        raise HTTPException(status_code=400, detail=exc.output) from exc
    raise HTTPException(status_code=502, detail=str(exc)) from exc
