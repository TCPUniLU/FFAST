"""A stand-in for LAMMPS that does what FFAST's include file makes LAMMPS do.

Reads ``-var ffast_on``, ``-var ffast_dir`` and ``-log``; writes ``include.txt``
and copies the dump in ``FAKE_LMP_DUMP`` into ``frames.lammpstrj`` one frame
at a time. Its log has the lines FFAST reads: the processor grid and the
``run`` line. Like ``fix halt … error soft`` it checks for ``STOP`` before
each frame and, finding it, logs the halt line and ends cleanly. SIGTERM ends
it at once (Python's default).

Environment: FAKE_LMP_BUNDLE, FAKE_LMP_DUMP, FAKE_LMP_SLEEP (s per frame),
FAKE_LMP_EXIT (exit code), FAKE_LMP_NO_INCLUDE (input lacks the include),
FAKE_LMP_ARGS (file to record the argv in), FAKE_LMP_PROCS (process count in
the grid line, default 1), FAKE_LMP_REQUEST_AT (frame index after which a
stop request "by user" appears, as the viewer would write it),
FAKE_LMP_INCLUDE_EXTRA (more include.txt lines), FAKE_LMP_WAIT_SCORER (after the
first frame, wait until the scorer is scoring: a test about what the scorer does
mid-run must not depend on how fast it starts).
"""

import json
import os
import re
import sys
import time


def _wait_for_scorer(run_dir, limit_s=120.0):
    import glob

    t0 = time.time()
    while time.time() - t0 < limit_s:
        for p in glob.glob(os.path.join(run_dir, "analysis", "*", "progress.json")):
            try:
                if json.load(open(p)).get("state") == "scoring":
                    return
            except ValueError:
                pass
        time.sleep(0.02)
    raise SystemExit("fake lmp: the scorer never started scoring")


def main():
    argv = sys.argv[1:]
    if os.environ.get("FAKE_LMP_ARGS"):
        with open(os.environ["FAKE_LMP_ARGS"], "w") as fh:
            json.dump(argv, fh)
    var, log = {}, None
    i = 0
    while i < len(argv):
        if argv[i] == "-var":
            var[argv[i + 1]] = argv[i + 2]
            i += 3
        elif argv[i] == "-log":
            log = argv[i + 1]
            i += 2
        else:
            i += 1
    logf = open(log, "w") if log else None

    def say(line):
        if logf:
            logf.write(line + "\n")
            logf.flush()

    say("LAMMPS (fake build)")
    procs = int(os.environ.get("FAKE_LMP_PROCS", "1"))
    say("  1 by %d by 1 MPI processor grid" % procs)
    on = var.get("ffast_on", "0") == "1" and not os.environ.get("FAKE_LMP_NO_INCLUDE")
    if on:
        d = var["ffast_dir"]
        with open(os.path.join(d, "include.txt"), "w") as fh:
            fh.write("ffast-include 1\nbundle %s\nevery 100\n%selements C H O\nend\n"
                     % (os.environ["FAKE_LMP_BUNDLE"], os.environ.get("FAKE_LMP_INCLUDE_EXTRA", "")))
        text = open(os.environ["FAKE_LMP_DUMP"]).read()
        starts = [m.start() for m in re.finditer(r"^ITEM: TIME\n", text, re.M)]
        starts[0] = 0
        pieces = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
        say("run %d" % (100 * (len(pieces) - 1)))
        request_at = int(os.environ.get("FAKE_LMP_REQUEST_AT", "-1"))
        with open(os.path.join(d, "frames.lammpstrj"), "w") as fh:
            for k, p in enumerate(pieces):
                if os.path.exists(os.path.join(d, "STOP")):
                    say("WARNING: Fix halt condition for fix-id ffast_halt met on step %d with "
                        "value 1 (src/fix_halt.cpp:237)" % (100 * k))
                    say("AFTER_RUN_EXECUTED")
                    break
                fh.write(p)
                fh.flush()
                if k == 0 and os.environ.get("FAKE_LMP_WAIT_SCORER"):
                    _wait_for_scorer(d)
                if k == request_at:
                    with open(os.path.join(d, "stop_request.json"), "w") as req:
                        json.dump({"by": "user", "reason": "Stop now", "utc": "t"}, req)
                time.sleep(float(os.environ.get("FAKE_LMP_SLEEP", "0")))
    else:
        print("FFAST: not run through ffast-lammps; monitoring off")
        time.sleep(float(os.environ.get("FAKE_LMP_SLEEP", "0")) * 3)
    sys.exit(int(os.environ.get("FAKE_LMP_EXIT", "0")))


if __name__ == "__main__":
    main()
