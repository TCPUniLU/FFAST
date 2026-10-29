"""The trace: one file per server run of everything that happened, in order.

Off unless ``ffast-server --trace`` (or ``ffast-web --trace``) turns it on.
When on, the server writes one line for each message a browser sends, each
handler that runs (with how long it took, or the error and its traceback), each
message sent back, and each line the browser reports itself: what was clicked
or changed, and errors in the page. Reading the file top to bottom tells what
the user did and what the code ran in reply — the first place to look when the
interface does something unexpected.

Payloads are shortened so the file stays small: an array becomes its shape, a
long list its length, a long string its first characters.

Lines look like::

    10:42:01.120 ui     click "Duplicate"  (#metrics-drawer)
    10:42:01.121 in     DUPLICATE_METRIC {id: 'force_mae'}  [127.0.0.1:50812]
    10:42:01.310 done   DUPLICATE_METRIC 186 ms
    10:42:01.311 out    METRICS_CHANGED {count: 12}  [127.0.0.1:50812]
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

# How many trace files to keep; the oldest go when a new run starts.
KEEP = 10

_MAX_STR = 120
_MAX_MESSAGE = 300
_MAX_ITEMS = 8
_MAX_DEPTH = 4

_file = None
_lock = threading.Lock()


def default_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".ffast" / "logs"


def start(directory: Path | None = None) -> Path:
    """Open a new trace file and turn the trace on. Returns its path."""
    global _file
    directory = Path(directory) if directory else default_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"trace-{stamp}.log"
    _file = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered
    _prune(directory)
    logger.info("Trace on → %s", path)
    return path


def stop() -> None:
    global _file
    with _lock:
        if _file is not None:
            _file.close()
            _file = None


def enabled() -> bool:
    return _file is not None


def record(kind: str, text: str) -> None:
    """Write one entry: time, kind, text. A text of several lines (a stack)
    continues indented under it. Does nothing while the trace is off."""
    if _file is None:
        return
    now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    first, *rest = text.splitlines() or [""]
    with _lock:
        if _file is not None:
            _file.write(f"{now} {kind:<6} {first}\n")
            for line in rest:
                _file.write(f"             | {line}\n")


def record_error(text: str, exc: BaseException) -> None:
    """Write an error line followed by its traceback, indented."""
    if _file is None:
        return
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    record("error", f"{text}\n{tb.rstrip()}")


def message(event: str, args, kwargs) -> str:
    """One message, shortened: ``EVENT [args] {kwargs}``, at most one line."""
    parts = [event]
    if args:
        parts.append(summarize(list(args)))
    if kwargs:
        parts.append(summarize(kwargs))
    text = " ".join(parts).replace("\n", " ")
    return text if len(text) <= _MAX_MESSAGE else text[:_MAX_MESSAGE] + " …"


def summarize(value, depth: int = 0) -> str:
    """A short, readable form of ``value``: arrays as shapes, long things cut."""
    if isinstance(value, dict):
        if value.get("__ndarray__"):
            shape = "×".join(str(n) for n in value.get("shape") or [])
            return f"<array {shape} {value.get('dtype', '')}>"
        if depth >= _MAX_DEPTH:
            return f"{{…{len(value)} keys}}"
        items = list(value.items())
        shown = ", ".join(f"{k}: {summarize(v, depth + 1)}" for k, v in items[:_MAX_ITEMS])
        more = f", …{len(items) - _MAX_ITEMS} more" if len(items) > _MAX_ITEMS else ""
        return f"{{{shown}{more}}}"
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_ITEMS and all(isinstance(v, (int, float)) for v in value):
            return f"<{len(value)} numbers>"
        if depth >= _MAX_DEPTH:
            return f"[…{len(value)}]"
        shown = ", ".join(summarize(v, depth + 1) for v in value[:_MAX_ITEMS])
        more = f", …{len(value) - _MAX_ITEMS} more" if len(value) > _MAX_ITEMS else ""
        return f"[{shown}{more}]"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(value)} bytes>"
    shape = getattr(value, "shape", None)
    if shape is not None and hasattr(value, "dtype"):
        return f"<array {'×'.join(str(n) for n in shape)} {value.dtype}>"
    if isinstance(value, str):
        return repr(value if len(value) <= _MAX_STR else value[:_MAX_STR] + "…")
    return repr(value)


def _prune(directory: Path) -> None:
    files = sorted(directory.glob("trace-*.log"))
    for old in files[:-KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
