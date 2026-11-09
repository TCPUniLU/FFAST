# LAMMPS tutorials use practice runs that wait for the user

Status: Phase A built 2026-11-09 on branch `lammps-live`; awaiting user hand-test.
Phase B remains proposed. Decided in a
grilling session (13 questions). Clickable mockup of both tours
(https://claude.ai/artifact/5DD39R5Nxk3Jt18ZmTU2XH; source
`docs/research/lammps-tutorials-2026-11-08/mockup.html`). Plan:
`docs/implementation/2026-11-08-lammps-tutorials.md`. Builds on ADR 0088 (guided tours), ADR 0086
(wrapper, include, STOP file) and the "Run with FFAST" dialog.

## The problem

FFAST has tours for loading data and reading errors (ADR 0088), but none for
live LAMMPS runs. Three things make a LAMMPS tour hard:

- **The user's LAMMPS is unknown.** It may be missing, named `lmp_serial` or
  `lmp_mpi`, in a conda environment, or a build that lacks a command FFAST adds.
- **What to edit in the input is the hard part to explain.** The `include` line
  must come after the atoms exist; placed wrong, monitoring is silently off.
- **A live run does not wait for the reader.** The toy input does about 400,000
  steps/s, so it is over before a frame shows; and once paced, an alarm can fire
  while the user is still reading an earlier step.

A tour must stay predictable: every sentence it shows must be true when shown.

## What was decided

1. **Two tutorials.**
   - **"Watch a LAMMPS run"** needs no LAMMPS. A recorded run is replayed by a
     stand-in for LAMMPS, inside the real `ffast-lammps` wrapper and `ffast-live`
     scorer. Same result for every user.
   - **"Run your own LAMMPS"** teaches the edit and runs the user's LAMMPS on a
     shipped toy input. If no LAMMPS passes the check, it says why and offers the
     first tutorial instead.
2. **FFAST makes the edit; the user reads the diff.** Tutorial 2 walks through
   the Run with FFAST dialog. Its Review step lights each added line (include,
   start snapshot, data copy) with one sentence on why it is there. Editing by
   hand stays a short section in `docs/usage.md`.
3. **Run with FFAST gains a "This computer" target**, a real feature and not a
   tutorial-only one: it starts `ffast-lammps <lmp> -in <input>.ffast.in` as a
   local process. One process only; `mpirun` is refused with a reason.
4. **Finding the user's LAMMPS: search, then ask.** The Check step looks on PATH
   for `lmp`, `lmp_serial`, `lmp_mpi`, `lmp_*`, runs the existing check
   (`ffast/live/lammps_check.py`) on each, and lists what passes and what fails
   with the reason. Nothing found: a path field. Nothing passes: offer
   Tutorial 1. FFAST never installs LAMMPS.
5. **The toy input paces itself with a standard command:**
   `run N every 100 "shell sleep 0.3"`: a frame about every 0.3 s on any
   machine. It is one command, not a loop, so `lammps_input.py` does not refuse
   it. It carries the comment `# tutorial only: slows the run so you can watch
   it` and is never added to a user's own input.
6. **The run waits for the user before the anomaly.** The calm part plays, then
   the run pauses before the heat (300 K → 3000 K) until the tour's
   **Heat it up** button writes a gate file. The replay stand-in waits for that
   file; the tutorial input waits with a `shell` line before its second `run`.
   So the alarm comes only when the user asks for it. **Heat it up** is greyed
   out until LAMMPS reaches the pause; meanwhile the tour shows how far the calm
   part is ("Calm part: step 4 300 of 10 000 · about 17 s to the pause"), so a
   click never seems to do nothing.
7. **Each stop control is taught once.** Tutorial 1: the user switches
   **Stop on alarm** on before the heat; the run stops itself. Tutorial 2: the
   user presses **Stop now**; LAMMPS halts through `fix halt`. Both are the
   app's real controls (the Run panel's Stop on alarm tick box; Stop now in
   the Live Runs dialog), and a practice run obeys them through the real
   wrapper and STOP file. The tour lights them; it never stands in for them.
8. **Tour text names no cluster.** The recorded run is "recorded on an HPC
   cluster", not by the cluster's name.
9. **Files ship in the repo, `examples/lammps/`** (about 2 MB): toy data, the
   tutorial input, the toy reference bundle, one recorded alarm run. The replay
   stand-in moves from the laptop demo into FFAST
   (e.g. `ffast-lammps --replay <run>`). Tutorial 2 copies its input folder into
   the practice folder first, so Run with FFAST's `*.ffast.*` copies are never
   written into the repo or an installed package.
10. **Practice runs use the real lists, marked "practice".** Run folders go to
   `~/.ffast/practice-runs/`; the toy reference is registered as "toy aspirin
   (tutorial)". The mark is a field in `run.json`, shown in the Run row and the
   Live Runs dialog. Each tour ends with "Keep or delete the practice run?".
11. **Both start from a button, never a terminal.** The server starts the replay
    and the local LAMMPS through the same local-launch path.
12. **Where they can start.** Tutorial 1: anywhere, including a cluster server
    (the replay is light). Tutorial 2: only with a local server on Linux or
    macOS; otherwise greyed out with "Needs FFAST running on this computer".
    Both are listed in the LAMMPS menu and with the other tours in the File menu.

### Steps (agreed for now; the mockup may change wording)

Tutorial 1, "Watch a LAMMPS run" (13): Start a practice run · Run row · 3D view ·
score plot (below 1 = like the reference) · Events · switch Stop on alarm on ·
Heat it up · wait for the alarm (a lasting anomaly, not one stray frame) · wait
for the run to stop itself · click the onset, 3D jumps there · Attribution ·
chemistry alarm (a bond broke) · keep or delete.

Tutorial 2, "Run your own LAMMPS" (13): open Run with FFAST (tour copies the
tutorial folder) · Choose the copy · Where: This computer · Check (builds found)
· Review: include line · Review: start snapshot and the `every`/`sleep` line ·
Review: data copy · Start, wait for frames · Heat it up · wait for the alarm ·
Stop now, wait for the halt (alarm step vs stop step) · for your own runs (same
dialog, your reference from Make reference, no `sleep` line) · keep or delete.

## Considered and rejected

- **Only the user's LAMMPS:** results depend on build, speed and seed; the text
  could not promise anything.
- **Only the replay:** nobody learns to run their own LAMMPS.
- **Hand edit as the main path:** typos and a misplaced `include` fail silently.
- **FFAST installs a LAMMPS:** heavy, and a second LAMMPS beside the user's.
- **A long run with few frames** (2–6 min): still a race with the reader.
- **A `label`/`jump` loop with sleeps:** `lammps_input.py` refuses loops.
- **A separate practice list:** the user cannot find the run after the tour.
- **Tutorial 2 through Slurm on a cluster:** queue waits break predictability.

## Later work, not in this ADR

- **FFAST holds LAMMPS back** (pause it while it is more than a few frames
  ahead of the scorer). Would also fix the stop landing millions of steps after
  the alarm (`docs/research/lammps-live-2026-11-04/stop-proof.md`).
- **"This computer" with `mpirun`** (the stop then goes through the wrapper, not
  `fix halt`; untested on a laptop).

## Phase A evidence (2026-11-09)

The real replay tour uses the existing wrapper, scorer, Stop on alarm checkbox,
and score-point picking. This recording has **unverified** atom order: it passes
the first-frame bond check but has no local data-file proof. Attribution ranks
coordinate groups for the selected test; it does not promise a particular bond
will rank first. The mockup and tour text now say that. The toy reference has a
server-local working copy and generated include under `practice-runs/reference`.
A running practice run must stop before its folder can be deleted.

Phase B still waits for the user's Phase A browser hand-test. No LAMMPS was
installed in `ffast_env`, and nothing was pushed.

## Open before Phase B / public push

- The `shell` wait line and `run … every … "shell sleep"` are untested on a real
  LAMMPS; check both, and what other processes do while process 0 waits.
- The origin of `aspirin.data` (one 21-atom geometry) is not recorded; settle it
  before it ships in a public repo.
- This laptop has no LAMMPS. Testing Tutorial 2 needs one in a separate
  environment (e.g. conda-forge `lammps`), never in `ffast_env`.
