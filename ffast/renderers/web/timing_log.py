"""The timing log file (ADR 0072).

The page measures where the time of each click goes (``static/timing.js``)
and posts the lines to the ``ffast-web`` launcher, which appends them here:
one file per launcher run, ``~/.ffast/logs/timing-<date-time>.log``, made when
the first lines arrive. The newest ten are kept.
"""

from __future__ import annotations

import datetime
import threading
from pathlib import Path

from ffast.session.trace import default_dir

KEEP = 10
# What one request may carry: the page sends a few blocks every two seconds.
MAX_LINES = 5000
MAX_LINE = 2000


class TimingLogFile:
    def __init__(self, directory: Path | None = None) -> None:
        self._dir = Path(directory) if directory else default_dir()
        self._lock = threading.Lock()
        self.path: Path | None = None

    def write(self, lines) -> bool:
        """Append ``lines`` (a list of strings). False when they are not one."""
        if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
            return False
        lines = [x[:MAX_LINE].replace("\r", " ") for x in lines[:MAX_LINES]]
        with self._lock:
            if self.path is None:
                self._dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                self.path = self._dir / f"timing-{stamp}.log"
                self.path.touch()
                self._prune()
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("".join(x + "\n" for x in lines))
        return True

    def _prune(self) -> None:
        for old in sorted(self._dir.glob("timing-*.log"))[:-KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
