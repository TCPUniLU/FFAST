import asyncio
import argparse
import logging
import os
import sys
from pathlib import Path
import site

# Redirect Python's bytecode cache out of the (iCloud-synced) repo BEFORE any
# project module is imported. Writing __pycache__ next to source makes iCloud
# sync-churn the tree, which intermittently locks/evicts source files mid-import
# -- surfaced as `PermissionError: Operation not permitted` on e.g.
# ffast/session/__init__.py when starting the local server. sys.pycache_prefix
# takes effect at runtime for all subsequent imports; honour an explicit
# PYTHONPYCACHEPREFIX if the user set one, else default outside iCloud.
os.environ.setdefault("PYTHONPYCACHEPREFIX", os.path.expanduser("~/.cache/ffast-pyc"))
sys.pycache_prefix = os.environ["PYTHONPYCACHEPREFIX"]

# Fix Qt plugin path issue - must be set before importing PySide6
if "QT_PLUGIN_PATH" not in os.environ:
    # Find PySide6 without importing it (to avoid triggering Qt init)
    for site_dir in site.getsitepackages():
        pyside6_path = Path(site_dir) / "PySide6"
        if pyside6_path.exists():
            plugin_path = pyside6_path / "Qt" / "plugins"
            lib_path = pyside6_path / "Qt" / "lib"
            if plugin_path.exists():
                # Set multiple Qt environment variables for maximum compatibility
                os.environ["QT_PLUGIN_PATH"] = str(plugin_path)
                os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugin_path / "platforms")
                os.environ["DYLD_LIBRARY_PATH"] = str(lib_path)
                os.environ["DYLD_FRAMEWORK_PATH"] = str(lib_path)
                # Explicitly set platform to cocoa on macOS
                os.environ["QT_QPA_PLATFORM"] = "cocoa"
                break

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from ffast.core.environment import Environment
from ffast.core.events import EventClass
from utils import loadModules, setupLogger

logger = logging.getLogger("FFAST")


class NathHorthath(EventClass):
    """
    Purely for debugging/testing purposes
    """

    def __init__(self, env):
        super().__init__()
        self.env = env

    async def countingTask(self, taskID=None):
        for i in range(10):
            self.eventPush(
                "TASK_PROGRESS",
                taskID,
                progMax=20,
                prog=i,
                message="Counting a bit",
                quiet=True,
            )
            await asyncio.sleep(0.2)
            print(i)

        for i in range(10, 20):
            self.eventPush(
                "TASK_PROGRESS",
                taskID,
                progMax=20,
                prog=i,
                message="Counting some more",
                quiet=True,
            )
            await asyncio.sleep(0.2)
            print(i)

    async def predictTask(self, taskID=None):
        env = self.env

        while True:
            await asyncio.sleep(1)

            if (len(env.models) < 1) or (len(env.datasets) < 1):
                continue

            dataset = env.datasets[next(iter(env.datasets))]
            model = env.models[next(iter(env.models))]

            env.data.taskGenerateData(
                "energyError", model=model, dataset=dataset, visual=True
            )
            break

    async def taskWatchDog(self, taskID=None):
        while True:
            await asyncio.sleep(1)
            print(len(self.env.tm.runningTasks))


async def eventLoop(UI, env):
    nh = NathHorthath(env)
    taskManager = env.tm

    loop = asyncio.get_event_loop()
    env.remote._event_loop = loop
    env.tm.newTask(env.remote.startLocalServer, visual=True, name="Local server")

    while not UI.quitReady:
        await UI.eventHandle()
        await env.eventHandle()
        await nh.eventHandle()
        await env.data.handleGenerationQueue()
        await taskManager.eventHandle()
        await taskManager.handleTaskQueue()
        await asyncio.sleep(0.1)

    await UI.eventHandle()
    await env.eventHandle()
    await taskManager.eventHandle()
    await taskManager.quit()

    if env.remote._localServerListener is not None:
        env.remote._localServerListener.cancel()
    if env.remote.localServerHandle is not None and env.remote.localServerManager is not None:
        env.remote.localServerManager.stop(env.remote.localServerHandle)


def main(workdir=None):
    from UI.UIHandler import UIHandler

    app = QApplication(sys.argv)

    event_loop = QEventLoop(app)
    asyncio.set_event_loop(event_loop)

    UI = UIHandler(workdir=workdir)
    UI.launch(app)

    env = Environment(headless=False)
    UI.setEnvironment(env)

    loadModules(UI, env)

    # await eventLoop(UI, env)
    event_loop.run_until_complete(eventLoop(UI, env))
    event_loop.close()


def cli():
    parser = argparse.ArgumentParser(
        description="FFAST - Force Field Analysis and Visualization Tool"
    )
    parser.add_argument(
        "--workdir",
        type=str,
        default=None,
        help="Set working directory for file dialogs (default: current directory)",
    )
    args = parser.parse_args()

    workdir = None
    if args.workdir:
        workdir = os.path.abspath(os.path.expanduser(args.workdir))
        if not os.path.isdir(workdir):
            print(
                f"Warning: Working directory '{workdir}' does not exist. Using current directory."
            )
            workdir = None

    main(workdir=workdir)


if __name__ == "__main__":
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='FFAST - Force Field Analysis and Visualization Tool')
    parser.add_argument('--workdir', type=str, default=None,
                        help='Set working directory for file dialogs (default: current directory)')
    args = parser.parse_args()

    # Validate and convert workdir to absolute path
    workdir = None
    if args.workdir:
        workdir = os.path.abspath(os.path.expanduser(args.workdir))
        if not os.path.isdir(workdir):
            print(f"Warning: Working directory '{workdir}' does not exist. Using current directory.")
            workdir = None

    # add logging filters
    class VispyNoiseFilter(logging.Filter):
        def filter(self, record):
            msg = record.getMessage()
            return "QPyDesignerCustomWidgetCollection" not in msg

    vispyLogger = logging.getLogger("vispy")
    vispyLogger.addFilter(VispyNoiseFilter())

    mplLogger = logging.getLogger("matplotlib")
    mplLogger.setLevel(logging.WARN)

    setupLogger()
    logger = logging.getLogger("FFAST")

    if workdir:
        logger.info(f"Working directory set to: {workdir}")

    try:
        main(workdir=workdir)
    except RuntimeError as e:
        if str(e) == "Event loop stopped before Future completed.":
            sys.exit()
        else:
            logger.error(e)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
        sys.exit()

    except Exception as e:
        logger.exception(e)
        sys.exit()

