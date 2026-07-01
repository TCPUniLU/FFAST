"""
ffast-server — headless FFAST environment wrapped in a WebSocket RPC server.

Start with: ffast-server --port <port>

Control messages are msgpack-encoded dicts:
    {"event": str, "args": list, "kwargs": dict}

Client → server events:
    LOAD_DATASET          args=[path, datasetType]
                          kwargs={selected_energy_key, selected_force_key,
                                  prediction_keys, slice_num}
    LOAD_MODEL            args=[path, modelType]
    DELETE_OBJECT         args=[fingerprint]
    PROBE_DATASET_LENGTH  args=[path]  → DATASET_LENGTH_RESPONSE
    LIST_DIR              args=[path?] → DIR_LISTING   (server-side filesystem browse)
    GRACEFUL_DISCONNECT   (no args) — signals intentional client shutdown
    OPEN_VIEW             kwargs={view_id?, dataset_ref?}  → SCENE_SNAPSHOT
    CLOSE_VIEW            kwargs={view_id}
    VIEW_COMMAND          kwargs=<ViewCommand fields>  → COMMAND_RESULT [+ SCENE_PATCH]

Server → client events (auto-forwarded):
    TASK_CREATED, TASK_PROGRESS, TASK_DONE, TASK_FAILED,
    DATA_UPDATED, DATASET_LOADED, MODEL_LOADED,
    DATASET_DELETED, MODEL_DELETED

Server → client events (view lifecycle):
    SCENE_SNAPSHOT        kwargs={scene: RenderScene dict}
    SCENE_PATCH           kwargs={patch: ScenePatch dict}
    COMMAND_RESULT        kwargs={result: CommandResult dict}

Handshake sequence (after WebSocket upgrade):
    client → "ping" (text)
    server → "pong" (text)
    client → HELLO  (binary msgpack, ClientCapabilities)
    server → HELLO_ACK (binary msgpack, ServerCapabilities with role)
    server → state replay

Token auth (managed mode only):
    Pass --token-hash <sha256_hex> to restrict CONTROLLING role to the
    client that sends the matching plaintext in the HELLO message.
    Without --token-hash every first-connecting client becomes CONTROLLING.

Recovery window (managed mode only):
    Pass --recovery-window N (seconds, default 0 = disabled).
    When the CONTROLLING client disconnects without GRACEFUL_DISCONNECT,
    the server stays alive for N seconds so it can reconnect.

Log file: server.log (same directory as this file).
"""
import argparse
import asyncio
import logging
import os

logger = logging.getLogger("FFAST")

_SERVER_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "server.log"
)


def _setupServerLogger():
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(_SERVER_LOG),
            logging.StreamHandler(),
        ],
    )


async def _auto_snapshot_loop(
    env, job_id: str, interval_minutes: int
) -> None:
    """Periodically save server state to ~/.ffast/snapshots/<job_id>/."""
    snapshot_dir = os.path.expanduser(
        os.path.join("~", ".ffast", "snapshots", job_id)
    )
    os.makedirs(snapshot_dir, exist_ok=True)
    logger.info(
        "Auto-snapshot: every %d min → %s", interval_minutes, snapshot_dir
    )
    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            loop = asyncio.get_event_loop()
            # env.persistence.save is blocking I/O — run in a thread executor
            await loop.run_in_executor(None, env.persistence.save, snapshot_dir)
            logger.info("Auto-snapshot saved to %s", snapshot_dir)
        except Exception as exc:
            logger.warning("Auto-snapshot failed: %s", exc)


async def _do_hello_handshake(websocket, addr, registry, token_hash: str):
    """Ping/pong then HELLO/HELLO_ACK. Returns the assigned ClientRole."""
    from cluster.rpc import pack, unpack
    from ffast.session.token import ClientRole, SessionToken
    from ffast.visualization.protocol import ClientCapabilities, negotiate

    # ── ping/pong ────────────────────────────────────────────────────────
    try:
        msg = await asyncio.wait_for(websocket.recv(), timeout=30)
    except asyncio.TimeoutError:
        logger.warning("Client %s: no ping in 30s — assigning READ_ONLY", addr)
        return registry.claim(websocket, False)

    if msg != "ping":
        logger.warning("Client %s: expected 'ping', got %r", addr, msg)
        return registry.claim(websocket, False)

    await websocket.send("pong")
    logger.debug("Pong sent to %s", addr)

    # ── HELLO (short timeout for backward compat) ─────────────────────────
    try:
        msg = await asyncio.wait_for(websocket.recv(), timeout=5)
    except asyncio.TimeoutError:
        logger.info("Client %s: no HELLO in 5s — READ_ONLY (backward compat)", addr)
        return registry.claim(websocket, False)

    if not isinstance(msg, bytes):
        logger.info("Client %s: expected binary HELLO, got text — READ_ONLY", addr)
        return registry.claim(websocket, False)

    try:
        event, _args, kwargs = unpack(msg)
    except Exception as exc:
        logger.warning("Client %s: HELLO decode error: %s — READ_ONLY", addr, exc)
        return registry.claim(websocket, False)

    if event != "HELLO":
        logger.info("Client %s: expected HELLO, got %r — READ_ONLY", addr, event)
        return registry.claim(websocket, False)

    # ── token validation ──────────────────────────────────────────────────
    token_ok = False
    if token_hash:
        candidate = kwargs.get("session_token") or ""
        if candidate:
            token_ok = SessionToken.from_hash(token_hash).verify(candidate)
    else:
        # No token required — first client gets CONTROLLING automatically
        token_ok = True

    role = registry.claim(websocket, token_ok)

    # ── HELLO_ACK ─────────────────────────────────────────────────────────
    try:
        # Build ClientCapabilities without session_token for negotiate()
        caps_kwargs = {k: v for k, v in kwargs.items() if k != "session_token"}
        client_caps = ClientCapabilities(**caps_kwargs)
        server_caps = negotiate(client_caps)
        ack_dict = server_caps.model_dump()
        ack_dict["role"] = role.value
        ack = pack("HELLO_ACK", [], ack_dict)
        await websocket.send(ack)
        logger.info("HELLO_ACK → %s: role=%s", addr, role.value)
    except Exception as exc:
        logger.warning("Client %s: HELLO_ACK error: %s", addr, exc)

    return role


async def _recovery_window_task(registry, recovery_window: int, quit_event: asyncio.Event):
    """Wait N seconds; shut down if no CONTROLLING client reconnected."""
    logger.info("Recovery window started: %ds", recovery_window)
    await asyncio.sleep(recovery_window)
    if not registry.has_controlling:
        logger.info("Recovery window expired — no reconnect, shutting down")
        quit_event.set()
    else:
        logger.info("Recovery window: CONTROLLING client reconnected, staying alive")


async def _handler(
    websocket, session, registry, token_hash: str,
    recovery_window: int, quit_event: asyncio.Event,
):
    """Handle one WebSocket connection against the shared ServerSession."""
    addr = websocket.remote_address
    logger.info("Client connected: %s", addr)

    # ── handshake ─────────────────────────────────────────────────────────
    from ffast.session.token import ClientRole
    role = await _do_hello_handshake(websocket, addr, registry, token_hash)

    # ── state replay ──────────────────────────────────────────────────────
    session.replay()

    # ── receive / send loops ──────────────────────────────────────────────
    from cluster.rpc import unpack
    graceful = False

    async def receive_loop():
        nonlocal graceful
        async for message in websocket:
            if isinstance(message, bytes):
                try:
                    event, args, kwargs = unpack(message)
                    if event == "GRACEFUL_DISCONNECT":
                        graceful = True
                        logger.info("GRACEFUL_DISCONNECT from %s", addr)
                    elif role == ClientRole.CONTROLLING:
                        await session.dispatch(event, args, kwargs)
                    else:
                        logger.debug(
                            "READ_ONLY client %s sent %s — ignored", addr, event
                        )
                except Exception as exc:
                    logger.warning("RPC decode error: %s", exc)
            else:
                logger.debug("Unexpected text message from %s: %r", addr, message)

    async def send_loop():
        while True:
            data = await session.outbound.get()
            try:
                await websocket.send(data)
            except Exception as exc:
                logger.warning("Send error to %s: %s", addr, exc)

    receive_task = asyncio.create_task(receive_loop())
    send_task = asyncio.create_task(send_loop())

    try:
        await receive_task
    except Exception as exc:
        logger.warning("Connection error from %s: %s", addr, exc)
    finally:
        send_task.cancel()
        try:
            await send_task
        except asyncio.CancelledError:
            pass

        released_role = registry.release(websocket)
        logger.info(
            "Client disconnected: %s role=%s graceful=%s",
            addr, released_role and released_role.value, graceful,
        )

        if (
            released_role == ClientRole.CONTROLLING
            and not graceful
            and recovery_window > 0
        ):
            asyncio.create_task(
                _recovery_window_task(registry, recovery_window, quit_event)
            )


async def _serve(
    env,
    outbound,
    port: int,
    token_hash: str = "",
    recovery_window: int = 0,
):
    """Run the WebSocket server until the environment signals quit or quit_event fires."""
    import websockets

    from ffast.session import ConnectionRegistry, ServerSession

    registry = ConnectionRegistry()
    quit_event = asyncio.Event()
    # One ServerSession per server process — owns the open Visualization Views
    # and dispatches client events; connections attach to it (one CONTROLLING).
    session = ServerSession(env, outbound)

    async def handler(websocket):
        await _handler(
            websocket, session,
            registry=registry,
            token_hash=token_hash,
            recovery_window=recovery_window,
            quit_event=quit_event,
        )

    logger.info("Starting ffast-server on port %d", port)
    async with websockets.serve(
        handler, "", port,
        max_size=None,
        ping_interval=30,
        ping_timeout=60,
    ):
        logger.info("ffast-server listening on ws://0.0.0.0:%d", port)
        while not env.quitReady and not quit_event.is_set():
            await asyncio.sleep(1)
    logger.info("ffast-server shut down")


def _load_project_metric_modules(config_arg: str | None = None) -> None:
    """Load external Trusted Metric Modules from the project config at startup,
    so their metrics register server-side (ADR 0011) and appear in METRIC_CATALOG.

    Resolves an explicit ``--config`` path, else discovers the nearest
    ``ffast.toml`` from the server's working directory. A missing config is fine
    (built-ins still work); a bad config or module is logged and skipped rather
    than crashing the server.

    Built-ins are registered first so external metrics may reference them.

    NOTE: "trusted" here means "declared and enabled in the project config".
    Content-hash / explicit-approval gating (CONTEXT "Trusted Metric Module") is
    future work, as is discovering the config from an opened dataset/session
    directory rather than the server CWD.
    """
    from pathlib import Path
    try:
        import ffast.metrics.builtin  # noqa: F401 — register built-ins first
        from ffast.config.loader import (
            discover_config, load_metric_modules, load_project_config,
        )
    except Exception as exc:
        logger.warning("Config/metric loader unavailable; external metrics skipped: %s", exc)
        return

    config_path = Path(config_arg) if config_arg else discover_config(Path.cwd())
    if config_path is None or not config_path.exists():
        logger.info("No project ffast.toml found; using built-in metrics only")
        return

    try:
        config = load_project_config(config_path)
    except Exception as exc:
        logger.warning("Invalid project config %s: %s; external metrics skipped", config_path, exc)
        return

    enabled = [m for m in config.metrics.modules if m.enabled]
    if not enabled:
        logger.info("Project config %s declares no enabled metric modules", config_path)
        return

    try:
        load_metric_modules(config, config_path)
        sources = [m.import_path or m.path for m in enabled]
        logger.info(
            "Loaded %d external metric module(s) from %s: %s",
            len(enabled), config_path, sources,
        )
    except Exception as exc:
        logger.warning("Failed loading external metric modules from %s: %s", config_path, exc)


def _validate_metric_registry() -> None:
    """Freeze the metric graph at startup (Metric DX decision M1 / H2).

    Builds the dependency DAG and validates every registered metric's input
    refs, shape declarations, and dependency acyclicity *once*, before any
    client connects or the first metric runs. A validation failure here is a
    Configuration Failure (unknown ref, cycle, legacy string shape) — distinct
    from an isolated runtime Metric Failure — so the server refuses to start
    rather than serving a structurally broken registry.
    """
    try:
        from ffast.metrics.registry import default_registry
    except Exception as exc:
        logger.warning("Metric registry unavailable; skipping validation: %s", exc)
        return

    errors = default_registry.freeze()
    if errors:
        for mid, msg in errors:
            logger.error("Metric validation [%s]: %s", mid, msg)
        raise SystemExit(
            f"Metric registry validation failed with {len(errors)} error(s); "
            f"refusing to start (see log above)."
        )
    logger.info(
        "Metric registry validated and frozen: %d metric(s).",
        len(default_registry.list_metrics()),
    )


async def _main(
    port: int,
    snapshot_interval: int = 5,
    job_id: str = "local",
    token_hash: str = "",
    recovery_window: int = 0,
    web_port: int = 0,
    config: str | None = None,
):
    """Bootstrap env, wire RPC subscriptions, run server + event loop."""
    from client.environment import HeadlessEnvironment
    from cluster.rpc import SERVER_TO_CLIENT, pack
    from utils import loadModules

    env = HeadlessEnvironment()
    loadModules(None, env, headless=True)

    # Register external Trusted Metric Modules from project config (ADR 0011) so
    # they appear in METRIC_CATALOG and can be computed. Done before any client
    # connects / first metric runs, so the worker pool pickles the full registry.
    _load_project_metric_modules(config)

    # Validate the full metric graph once, after builtins + external modules are
    # registered. Refuses to start on unknown refs, cycles, or legacy shapes.
    _validate_metric_registry()

    # Queue for server→client events (events dropped when full / no client)
    outbound: asyncio.Queue = asyncio.Queue(maxsize=200)

    for evt in SERVER_TO_CLIENT:

        def make_sender(e: str):
            def handler(*args, **kwargs):
                try:
                    data = pack(e, args, kwargs)
                    outbound.put_nowait(data)
                except asyncio.QueueFull:
                    logger.debug(
                        "Outbound queue full, dropping %s event", e
                    )

            return handler

        env.eventSubscribe(evt, make_sender(evt))

    # Send lightweight dataset metadata to the client when any dataset loads.
    # This lets the client create a RemoteDatasetProxy for Loupe without
    # transferring the full arrays.
    def _on_dataset_loaded_meta(fingerprint):
        from ffast.protocol import DatasetMeta
        dataset = env.datasets.get(fingerprint)
        if dataset is None:
            return
        try:
            data = pack(
                "REMOTE_DATASET_META",
                (fingerprint,),
                DatasetMeta.model_validate(dataset.toMetaDict()).model_dump(),
            )
            outbound.put_nowait(data)
            logger.debug("REMOTE_DATASET_META sent for %r", fingerprint)
        except Exception as exc:
            logger.warning("REMOTE_DATASET_META error: %s", exc)

    env.eventSubscribe("DATASET_LOADED", _on_dataset_loaded_meta)

    # Send lightweight model metadata when a ghost model is registered.
    # MODEL_LOADED fires AFTER _loadPredictionsFromKeys + lookForGhosts(),
    # so prediction arrays are already in env.cache at this point.
    def _on_model_loaded_meta(model_fp):
        from ffast.protocol import ModelMeta
        model = env.models.get(model_fp)
        if model is None:
            return
        # Stage 2: real server-side models are metaed too (not just ghosts) — the
        # client holds a proxy and the server generates predictions on demand.
        try:
            name = getattr(model, "name", None) or model_fp[:8]

            # Find which dataset fingerprints this model has predictions for
            dataset_fps = []
            from ffast.cache import CacheKey
            for cache_key in list(env.cache.keys()):
                ck = CacheKey.try_parse(cache_key)
                if ck is not None and ck.matches_model(model_fp) and ck.dataset_fp:
                    if ck.dataset_fp not in dataset_fps:
                        dataset_fps.append(ck.dataset_fp)

            data = pack(
                "REMOTE_MODEL_META",
                (model_fp,),
                ModelMeta(name=name, dataset_fingerprints=dataset_fps).model_dump(),
            )
            outbound.put_nowait(data)
            logger.info(
                "REMOTE_MODEL_META sent: model=%r name=%r datasets=%r",
                model_fp[:8], name, dataset_fps,
            )
        except Exception as exc:
            logger.warning("REMOTE_MODEL_META error: %s", exc)

    env.eventSubscribe("MODEL_LOADED", _on_model_loaded_meta)

    logger.info("Environment ready (job_id=%s)", job_id)

    if web_port > 0:
        from ffast.renderers.web.serve import start_static_server
        start_static_server(web_port)
        logger.info("Web app served at http://0.0.0.0:%d/?port=%d", web_port, port)

    coros = [
        env.headlessEventLoop(),
        _serve(env, outbound, port, token_hash=token_hash, recovery_window=recovery_window),
    ]
    if snapshot_interval > 0:
        coros.append(_auto_snapshot_loop(env, job_id, snapshot_interval))
        logger.info(
            "Snapshot loop scheduled: interval=%d min job_id=%s",
            snapshot_interval, job_id,
        )
    else:
        logger.info("Auto-snapshot disabled (interval=0)")

    await asyncio.gather(*coros)


def cli():
    _setupServerLogger()

    parser = argparse.ArgumentParser(
        description="ffast-server — headless FFAST WebSocket RPC server"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to listen on (default: 8765)",
    )
    parser.add_argument(
        "--snapshot-interval",
        type=int,
        default=5,
        metavar="MINUTES",
        help="Auto-snapshot interval in minutes (0 = disabled, default: 5)",
    )
    parser.add_argument(
        "--job-id",
        type=str,
        default=None,
        metavar="JOB_ID",
        help="SLURM job ID for snapshot directory naming "
             "(auto-detected from SLURM_JOB_ID env var if not set)",
    )
    parser.add_argument(
        "--token-hash",
        type=str,
        default="",
        metavar="SHA256_HEX",
        help="SHA-256 hex digest of the session token. "
             "Only the client presenting the matching plaintext in HELLO "
             "gets CONTROLLING role. Omit to grant CONTROLLING to the "
             "first connecting client (standalone use).",
    )
    parser.add_argument(
        "--recovery-window",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Seconds to keep server alive after unexpected CONTROLLING "
             "client disconnect (0 = disabled, default: 0).",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=0,
        metavar="PORT",
        help="Serve the FFAST web renderer at this HTTP port (0 = disabled, default: 0). "
             "Opens the web app at http://0.0.0.0:PORT/?port=WS_PORT.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help="Project ffast.toml to load Trusted Metric Modules from. "
             "If omitted, the nearest ffast.toml is discovered from the working "
             "directory; built-in metrics are always available.",
    )
    args = parser.parse_args()

    # Auto-detect job_id from SLURM environment; fall back to CLI arg or "local"
    job_id = (
        os.environ.get("SLURM_JOB_ID")
        or args.job_id
        or "local"
    )

    try:
        asyncio.run(_main(
            args.port,
            args.snapshot_interval,
            job_id,
            token_hash=args.token_hash,
            recovery_window=args.recovery_window,
            web_port=args.web_port,
            config=args.config,
        ))
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
    except Exception:
        logger.exception("ffast-server crashed during startup")
        raise


if __name__ == "__main__":
    cli()
