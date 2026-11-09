"""Start practice runs on the server machine, using the ordinary wrapper."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from ffast.live import run_folder as rf
from ffast.live.references import record_reference
from ffast.live.runs_list import forget_run
from ffast.session.examples import lammps_example_dir


def practice_folder(run_id, environ=None):
    """Resolve a practice pointer, refusing ordinary runs and outside folders."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise ValueError("not a run id")
    pointer = rf.read_json(rf.live_runs_dir(environ) / f"{run_id}.json") or {}
    folder = Path(pointer.get("run_dir") or ".").resolve()
    base = rf.practice_runs_dir(environ).resolve()
    if folder == base or base not in folder.parents:
        raise ValueError("not under the practice-runs folder")
    run = rf.read_json(folder / rf.RUN_JSON) or {}
    if not run.get("practice") or run.get("run_id") != run_id:
        raise ValueError("not a practice run")
    return folder, run


def heat_practice_run(run_id, environ=None):
    folder, run = practice_folder(run_id, environ)
    if not rf.practice_paused(folder, run):
        raise ValueError("the practice run is not paused before the heat")
    (folder / rf.GO).touch()
    rf.append_event(folder, "practice_heated")


def delete_practice_run(run_id, environ=None):
    folder, run = practice_folder(run_id, environ)
    if not run.get("end"):
        raise ValueError("stop the practice run before deleting it")
    shutil.rmtree(folder)
    forget_run(rf.live_runs_dir(environ), run_id)


async def start_practice_run(kind="replay", environ=None):
    """Return the new run ID and folder once its pointer exists; report wrapper errors.

    The wrapper watches this server's PID, so even an abrupt server exit
    interrupts its producer and leaves the wrapper time to finish run.json.
    """
    if kind != "replay":
        raise ValueError("unknown practice run kind")
    env = dict(os.environ if environ is None else environ)
    root = lammps_example_dir()
    settings = rf.read_json(root / "tutorial.json")
    if not settings:
        raise ValueError(
            "LAMMPS tutorial examples are not installed; run FFAST from its repository"
        )
    gate = settings["gate_step"]
    from ffast.live.include_file import include_text

    # Keep the generated include outside the repository/package. Its absolute
    # bundle path must belong to this server, not the machine that recorded it.
    reference = (
        rf.practice_runs_dir(env).resolve() / "reference" / "toy.bundle"
    )
    if not reference.exists():
        reference.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root / "toy.bundle", reference, dirs_exist_ok=True)
    reference.with_suffix(".lmp").write_text(
        include_text(str(reference.resolve()), ["C", "H", "O"])
    )
    record_reference(reference, env, name="toy aspirin (tutorial)")
    env.setdefault(
        "FFAST_LIVE_SCORER_CMD",
        json.dumps([sys.executable, "-m", "ffast.live.scorer"]),
    )
    command = [
        sys.executable,
        "-m",
        "ffast.live.wrapper",
        "--exit-with-parent",
        str(os.getpid()),
        "--practice-gate",
        str(gate),
        "--replay",
        str(root / "recorded-alarm"),
        "--gate-step",
        str(gate),
        "--sleep",
        str(env.get("FFAST_PRACTICE_SLEEP") or settings["sleep_s"]),
    ]
    existing_pointers = set(rf.live_runs_dir(env).glob("*.json"))
    errors = tempfile.TemporaryFile()
    try:
        process = subprocess.Popen(
            command, env=env, stdout=subprocess.DEVNULL, stderr=errors
        )
    except OSError:
        errors.close()
        raise

    def reap():
        process.wait()
        errors.close()

    def error_text():
        errors.seek(0)
        return errors.read().decode("utf-8", "replace").strip()

    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        for p in rf.live_runs_dir(env).glob("*.json"):
            if p in existing_pointers:
                continue
            pointer = rf.read_json(p) or {}
            run = (
                rf.read_json(Path(pointer.get("run_dir") or ".") / rf.RUN_JSON)
                or {}
            )
            # Run IDs contain the wrapper PID, assigned by its one writer.
            if run.get("practice") and f"-{process.pid}-" in pointer.get(
                "run_id", ""
            ):
                threading.Thread(target=reap, daemon=True).start()
                return pointer["run_id"], pointer["run_dir"]
        if process.poll() is not None:
            message = error_text()
            errors.close()
            raise ValueError(
                message or "ffast-lammps exited before registering the run"
            )
        await asyncio.sleep(0.02)
    process.terminate()
    await asyncio.to_thread(process.wait)
    message = error_text()
    errors.close()
    raise ValueError(
        "ffast-lammps did not register a practice run within 30 seconds: "
        + message
    )
