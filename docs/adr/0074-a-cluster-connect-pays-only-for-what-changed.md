# A cluster connect pays only for what changed

Status: Accepted 2026-11-01. Changes how ADR 0028's job command decides to
reinstall and how it installs.

## The problem

Connecting the web page to MeluXina took 2 min 40 s on 2026-10-30 (job
5404195) and 1 min 49 s on 2026-10-31 (job 5405515), with no code change in
between. Queue time was 27 s and 4 s. The rest was inside the job: 1 min 55 s
and 89 s until `ffast-server listening`.

Measured in test jobs on 2026-11-01 (same profile, same venv):

| Step inside the job | Time |
|---|---|
| module loads | 2.8 s |
| pip reinstall of the ffast wheel | 7–13 s |
| pip install of the light dependencies (nothing to do) | 1.9 s |
| `ffast-server --help` check | 3–9 s cold, 0.4 s warm |
| `import torch`, cold | 58 s |

Three causes:

- **Every connect reinstalled.** The job compared the built wheel's sha256
  with a marker in the venv. The wheel's `dist-info` files carry the build
  time, so two builds of the same code differed (284 files, the same bytes,
  6 timestamps apart). The marker never matched.
- **Every server start imported torch.** The MACE, NequIP and SpookyNet
  plugins had `import torch` on their first line, and the server imports every
  bundled plugin at start. torch is 13,979 files on the project file system,
  where opening each small file is slow.
- **pip is slow there.** pip took 7–13 s to reinstall a 284-file wheel that
  `uv` installs in 1.3–1.8 s.

## The decision

1. **The install marker is read from the checkout, not from a built wheel.**
   It hashes the code fingerprint (the code the server runs, now including
   `modules/`), the JSON files the wheel ships, and the light dependency list
   (`install_marker` in `cluster/bootstrap.py`). It ignores file times. When
   it matches the venv's marker, the client builds nothing, copies nothing,
   and the job only starts the server.
2. **The job log says how long each step took.** Each step prints
   `ffast-bootstrap step <name> <seconds> s` into `~/slurm-<job>.out`, for
   good, so the next slow connect can be split without new code.
3. **The server starts without torch.** The three plugins import torch inside
   the methods that use it. A test checks that a headless start leaves torch
   unimported.
4. **The job installs with `uv`.** It adds `uv` to the venv once
   (`pip install uv`, ~10–12 s, first connect only), then installs the wheel
   and the light dependencies with it.
5. **The `--help` check stays.** It catches a broken install with a clear line
   in the job log.

Result on MeluXina, click to connected, queue under 2 s:

| Case | Before | After |
|---|---|---|
| Code unchanged | 1 min 49 s | 33 s |
| Code changed, first `uv` install | 2 min 40 s | 43 s |

## Alternatives

- **Make the wheel reproducible** (`SOURCE_DATE_EPOCH`). The smallest change,
  but it still builds on every connect, and a checkout that touches file times
  still changes the hash.
- **Hash the wheel's file contents, ignoring times.** Exact for everything the
  wheel ships, but it builds on every connect (~4 s) and adds a second kind of
  code hash next to the code fingerprint. The fingerprint leaves out
  `cluster/`, `UI/` and `client/` on purpose: the server never imports them.
- **Skip the light-dependency install when the list is unchanged.** Saved
  1.9 s with pip; with `uv` the no-op install is 0.2 s, so it was left out.
- **Skip the `--help` check when the marker matches.** Measured at 0.4 s warm
  when this was decided; the hand test later showed 4.8–9 s cold. Still open.
- **A container image (ADR 0059) with Apptainer.** One `.sif` file instead of
  thousands of small files is likely to cut the cold-import cost itself. Not
  measured. `module load Apptainer` (1.3.6) works on MeluXina compute nodes,
  not on the login node.

## What it gives up

- **The torch cost moved, it did not go away.** The first model load in a
  session still pays the cold import (~1 min on MeluXina).
- **The rest of the server's imports are still cold.** With the install
  skipped, the job spends 2.8 s on modules, 9 s on the `--help` check and
  ~17 s starting the server, which is cold imports of numpy, scipy, ase and
  the others. These are the next things to cut.
- **The marker trusts the fingerprint's file list.** A file the server runs
  from outside `ffast/`, `modules/` and `server.py` would not trigger a
  reinstall. `tests/test_packaging.py` checks that a built wheel and the
  checkout give the same fingerprint.
- **`uv` is one more tool in the venv.** If it cannot be installed (no
  network on the compute node), the job fails at `install uv`, where pip would
  have failed at the dependencies anyway.

## Also fixed while measuring

- The light dependencies went into the job unquoted, so bash read
  `msgpack>=1.0.0` as a redirect: version limits were ignored and empty files
  named `=1.0.0`, `=12.0` … appeared in the cluster home folder.
- The Connect dialog offered "Reconnect" for jobs that had ended. It now asks
  SLURM once (`squeue --me`) when it opens and drops those records.

## Related

- ADR 0028: the job command this changes
- ADR 0059: the container image, the next candidate for the import cost
- ADR 0072: the page's timing log, the same idea for clicks
