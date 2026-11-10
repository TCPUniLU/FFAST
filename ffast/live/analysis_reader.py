"""Reading one analysis: the half of a live run that scored items share (ADR 0093).

A live run and a scored item both own an ``analysis/<analysis id>/`` folder of
scores. This base class tails that folder (``read_analysis``), turns its rows
into analysis values for metrics (ADR 0091) and explains a frame with the
bundle (attribution). A subclass supplies ``dataset``, ``run_id``,
``fingerprint``, and ``frame_count`` / ``step_of`` for its frames.

A row with ``refused`` is a frame the reference could not score: it stays a
gap (NaN) and ``analysis_reason`` gives the reference's own words.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ffast.live import run_folder as rf


def _v1_exceedance(row: dict, settings: dict) -> tuple[float | None, list | None]:
    """A step-2 row (one track, T² only): T² ÷ its centroid threshold."""
    name, track = next(iter((row.get("tracks") or {}).items()), (None, {}))
    limit = ((settings.get("thresholds") or {}).get(name) or {}).get("centroid")
    t2 = track.get("t2")
    if t2 is None or not limit:
        return None, None
    return t2 / limit, [name, "T2"]


class AnalysisReader:
    kind_word = "run"            # how an error names the thing ("this run has no scores yet")

    def _init_analysis(self) -> None:
        self.analysis_id: str | None = None
        self.settings: dict = {}
        self.rows: list[dict] = []
        self._tail: rf.ScoresTail | None = None

    def read_analysis(self, root: Path) -> list[dict]:
        """New score rows of the first analysis under ``root`` (any thread; touches no
        server state). Finds the analysis and reads its settings once they exist."""
        if self._tail is None:
            adir = Path(root) / rf.ANALYSIS_DIR
            ids = sorted(p.name for p in adir.iterdir() if p.is_dir()) if adir.is_dir() else []
            if ids:
                self.analysis_id = ids[0]
                self._tail = rf.ScoresTail(adir / ids[0] / rf.SCORES)
        rows = self._tail.poll() if self._tail is not None else []
        if self.analysis_id and not self.settings:
            self.settings = rf.read_json(Path(root) / rf.ANALYSIS_DIR / self.analysis_id / rf.SETTINGS) or {}
        return rows

    def frame_count(self) -> int:
        return self.dataset.N

    def step_of(self, frame: int):
        return self.dataset.steps[frame]

    def analysis_reason(self, frame: int) -> str | None:
        """Why the reference did not score ``frame``, in its own words; None if it did
        or the frame has no row yet."""
        for r in list(self.rows):
            if r.get("frame") == frame:
                return r.get("refused")
        return None

    # ── analysis values (ADR 0091; any thread) ─────────────────────────────
    def analysis_value_problem(self, key) -> str | None:
        """Why this run has no value for ``key`` (see ``parse_run_ref``), or None."""
        if key == ("worst",):
            return None
        if not self.settings:
            return "it has no scores yet"
        if self.settings.get("scores_format") != 2:
            return "its scores were written before per-test scores were kept"
        if key[0] != "score":
            return None
        _, track, stat = key
        name = f"{track} {'T²' if stat == 'T2' else stat}"
        if [track, stat] not in [list(t) for t in self.settings.get("tests") or []]:
            return f"its reference has no {name} test"
        if not self._threshold(track, stat):
            # A missing or zero threshold cannot divide a score: a note, not an empty line.
            return f"its reference has no usable {self._role()} threshold for {name}"
        return None

    def _role(self) -> str:
        return self.settings.get("role", "centroid")

    def _threshold(self, track, stat):
        return (((self.settings.get("thresholds") or {}).get(track) or {}).get(stat) or {}).get(self._role())

    def analysis_value(self, key) -> np.ndarray | None:
        """One number per frame of the run, NaN where no score row has the frame
        (a gap: the viewer never scores a frame itself), or None when the run
        has no such value. ``key``: ``("score", track, stat)``, ``("worst",)`` or
        ``("chem", "broken" | "close")``."""
        if self.analysis_value_problem(key):
            return None
        out = np.full(self.frame_count(), np.nan)
        rows = list(self.rows)          # the event loop appends while a worker reads
        limit = None
        if key[0] == "score" and key != ("worst",):
            _, track, stat = key
            limit = self._threshold(track, stat)      # non-zero: checked by analysis_value_problem
        for r in rows:
            if "refused" in r:
                continue                # the reference could not score this frame: a gap
            frame = r.get("frame")
            if not isinstance(frame, int) or not 0 <= frame < len(out):
                continue
            if key == ("worst",):
                value = r["exceedance"] if "exceedance" in r else _v1_exceedance(r, self.settings)[0]
            elif key[0] == "chem":
                value = (r.get("chem") or {}).get(key[1])
            else:
                score = ((r.get("tracks") or {}).get(track) or {}).get(stat)
                value = None if score is None else score / limit
            if value is not None:
                out[frame] = value
        return out

    # ── attribution (any thread) ────────────────────────────────────────────
    def _attribution_bundle(self):
        """The run's bundle, loaded once; ``ValueError`` saying why it cannot be used."""
        if getattr(self, "_bundle", None) is not None:
            return self._bundle
        from ffast.live.data_file import bundle_sha256
        from ffast.live.detectana_api import DetectAnaMissing, require_detectana

        path = self.settings.get("bundle")
        if not path:
            raise ValueError(f"this {self.kind_word} has no scores yet, so no bundle to explain them with")
        if not Path(path).is_dir():
            raise ValueError(f"the reference bundle {path} is gone; attribution needs it")
        want = self.settings.get("bundle_sha256")
        if want and bundle_sha256(Path(path)) != want:
            raise ValueError(f"the bundle at {path} changed after this {self.kind_word} was scored; "
                             "attribution would explain other scores")
        try:
            detectana = require_detectana()
        except DetectAnaMissing as exc:
            raise ValueError(str(exc)) from exc
        try:
            self._bundle = detectana.load(path)
        except detectana.BundleError as exc:
            raise ValueError(f"DetectAna {detectana.__version__} cannot use the bundle {path}: {exc}") from exc
        return self._bundle

    def attribution(self, frame: int, test: tuple[str, str] | None = None) -> dict:
        """What drove frame ``frame``'s score (ADR 0087): computed now, never stored."""
        from ffast.live.attribution import frame_attribution

        if not 0 <= frame < self.frame_count():
            raise ValueError(f"frame {frame} is not read yet ({self.frame_count()} frames)")
        reason = self.analysis_reason(frame)
        if reason:
            raise ValueError(f"frame {frame} was not scored: {reason}")
        bundle = self._attribution_bundle()
        positions = np.array(self.dataset.getCoordinates(frame), dtype=np.float64)
        out = frame_attribution(bundle, positions, self.settings.get("role", "centroid"), test)
        out.update(run_id=self.run_id, frame=frame, step=self.step_of(frame),
                   fingerprint=self.fingerprint)
        return out
