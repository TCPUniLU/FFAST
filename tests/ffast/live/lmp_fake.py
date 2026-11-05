"""A stand-in for LAMMPS that does what FFAST's include file makes LAMMPS do.

Reads ``-var ffast_on``, ``-var ffast_dir`` and ``-log``; writes ``include.txt``
and copies the dump in ``FAKE_LMP_DUMP`` into ``frames.lammpstrj`` one frame
at a time. Environment: FAKE_LMP_BUNDLE, FAKE_LMP_DUMP, FAKE_LMP_SLEEP (s per
frame), FAKE_LMP_EXIT (exit code), FAKE_LMP_NO_INCLUDE (input lacks the include),
FAKE_LMP_ARGS (file to record the argv in).
"""

import json
import os
import re
import sys
import time


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
    if log:
        with open(log, "w") as fh:
            fh.write("LAMMPS (fake build)\n")
    on = var.get("ffast_on", "0") == "1" and not os.environ.get("FAKE_LMP_NO_INCLUDE")
    if on:
        d = var["ffast_dir"]
        with open(os.path.join(d, "include.txt"), "w") as fh:
            fh.write("ffast-include 1\nbundle %s\nevery 100\nelements C H O\nend\n"
                     % os.environ["FAKE_LMP_BUNDLE"])
        text = open(os.environ["FAKE_LMP_DUMP"]).read()
        starts = [m.start() for m in re.finditer(r"^ITEM: TIME\n", text, re.M)]
        starts[0] = 0
        pieces = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
        with open(os.path.join(d, "frames.lammpstrj"), "w") as fh:
            for p in pieces:
                fh.write(p)
                fh.flush()
                time.sleep(float(os.environ.get("FAKE_LMP_SLEEP", "0")))
    else:
        print("FFAST: not run through ffast-lammps; monitoring off")
        time.sleep(float(os.environ.get("FAKE_LMP_SLEEP", "0")) * 3)
    sys.exit(int(os.environ.get("FAKE_LMP_EXIT", "0")))


if __name__ == "__main__":
    main()
