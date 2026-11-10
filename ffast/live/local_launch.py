"""Start local LAMMPS and practice runs using the ordinary wrapper."""

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


def tutorial_reference(environ=None):
    """Register the shipped toy reference from a server-local working copy."""
    env = dict(os.environ if environ is None else environ)
    root = lammps_example_dir()
    settings = rf.read_json(root / "tutorial.json")
    if not settings:
        raise ValueError(
            "LAMMPS tutorial examples are not installed; run FFAST from its repository"
        )
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
    return root, settings, reference


def prepare_tutorial_input(environ=None):
    """Copy the tutorial into a marked working folder, never over a user's folder."""
    env = dict(os.environ if environ is None else environ)
    root, settings, reference = tutorial_reference(env)
    base = rf.practice_runs_dir(env).resolve()
    folder = base / "tutorial-input"
    marker = folder / ".ffast-tutorial"
    if folder.is_symlink() or (folder.exists() and not marker.is_file()):
        raise ValueError(
            f"{folder} was not made by FFAST; choose another practice folder"
        )
    for pointer in rf.live_runs_dir(env).glob("*.json"):
        record = rf.read_json(pointer) or {}
        run = (
            rf.read_json(Path(record.get("run_dir") or ".") / rf.RUN_JSON)
            or {}
        )
        if run.get("cwd") == str(folder) and not run.get("end"):
            raise ValueError(
                "stop the existing tutorial run before copying its input again"
            )
    if folder.exists():
        shutil.rmtree(folder)
    shutil.copytree(root / "tutorial", folder)
    marker.write_text("Written by FFAST for the LAMMPS tutorial\n")
    shutil.copyfile(root / "aspirin.data", folder / "aspirin.data")
    source = folder / "in.tutorial"
    text = source.read_text().replace(
        "read_data ../aspirin.data", "read_data aspirin.data"
    )
    pace = env.get("FFAST_PRACTICE_SLEEP") or settings["sleep_s"]
    text = text.replace(
        'run 10000 every 100 "shell sleep 0.3"',
        f'run {settings["gate_step"]} every 100 "shell sleep {pace}"',
    )
    text = text.replace("shell sleep 0.3", f"shell sleep {pace}")
    source.write_text(text)
    return {
        "folder": str(folder),
        "input": str(source),
        "reference": str(reference),
    }


async def start_local_run(
    executable, input_path, *, practice=False, environ=None
):
    """Start an already reviewed local input through the ordinary wrapper."""
    env = dict(os.environ if environ is None else environ)
    if os.name == "nt":
        raise ValueError("local LAMMPS launch is supported on Linux and macOS")
    if not Path(executable).is_file() or not os.access(executable, os.X_OK):
        raise ValueError("choose a LAMMPS executable file, one process only")
    inp = Path(input_path).resolve()
    command = [
        sys.executable,
        "-m",
        "ffast.live.wrapper",
        "--exit-with-parent",
        str(os.getpid()),
    ]
    if practice:
        folder = rf.practice_runs_dir(env).resolve() / "tutorial-input"
        if inp.parent != folder or not (folder / ".ffast-tutorial").is_file():
            raise ValueError("not FFAST's copied tutorial input")
        _, settings, _ = tutorial_reference(env)
        command += [
            "--practice-gate",
            str(settings["gate_step"]),
            "--sleep",
            str(env.get("FFAST_PRACTICE_SLEEP") or settings["sleep_s"]),
        ]
    command += [executable, "-in", str(inp)]
    return await _launch(command, env, cwd=str(inp.parent))


async def start_practice_run(kind="replay", environ=None):
    """Return the new run ID and folder once its pointer exists; report wrapper errors.

    The wrapper watches this server's PID, so even an abrupt server exit
    interrupts its producer and leaves the wrapper time to finish run.json.
    """
    if kind != "replay":
        raise ValueError("unknown practice run kind")
    env = dict(os.environ if environ is None else environ)
    root, settings, _ = tutorial_reference(env)
    gate = settings["gate_step"]
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
    return await _launch(command, env)


async def _launch(command, env, *, cwd=None):
    env.setdefault(
        "FFAST_LIVE_SCORER_CMD",
        json.dumps([sys.executable, "-m", "ffast.live.scorer"]),
    )
    existing_pointers = set(rf.live_runs_dir(env).glob("*.json"))
    errors = tempfile.TemporaryFile()
    try:
        process = subprocess.Popen(
            command, env=env, cwd=cwd, stdout=subprocess.DEVNULL, stderr=errors
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
            if f"-{process.pid}-" in pointer.get("run_id", ""):
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
        "ffast-lammps did not register a local run within 30 seconds: "
        + message
    )
