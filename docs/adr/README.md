# Architecture decision records

Fifty-four records of why the code looks like this. Each one states the problem
it hit, what was chosen, and what that cost. They are dated documents: a record
that has since been superseded stays as written, with a pointer to whatever
replaced it. Nothing here is edited to look right in hindsight.

Grouped by area rather than by number, since numbers only tell you the order
things came up.

## Start here

| ADR | What it settles |
|-----|-----------------|
| [0011](0011-pure-metrics-with-configuration-driven-presentation.md) | A metric is a pure function of arrays. It never sees the Environment, a Dataset or a Model. |
| [0021](0021-client-side-2d-panels-with-transform-metrics.md) | Plots are declared in config, drawn by the client. The server stays plot-ignorant. |
| [0026](0026-headless-core-migration-direction.md) | `ffast/` must install and run with no Qt, no display, no client baggage. |
| [0045](0045-web-client-replaces-qt.md) | The default client became a web page, because the Qt install failed on real users' machines. |

## Client and server protocol

- [0001](0001-remote-rpc-protocol.md) WebSocket plus msgpack, and why not HTTP or gRPC
- [0006](0006-pydantic-protocol-and-configuration-schemas.md) Pydantic models define both protocol and config schemas
- [0004](0004-prediction-only-array-channel.md) A separate channel for prediction arrays
- [0012](0012-hello-handshake-for-session-token.md) HELLO handshake carrying session token and capabilities
- [0013](0013-graceful-disconnect-event.md) Explicit disconnect event for managed shutdown
- [0032](0032-inbound-event-router-client-handler-table.md) One handler table for server-to-client messages
- [0033](0033-complete-typed-control-messages.md) Finish typing the control messages
- [0031](0031-result-buffers-and-zstd-codec-deferred.md) Result buffers and zstd stay dormant, and why that is right

## The headless core

- [0026](0026-headless-core-migration-direction.md) The direction: move into `ffast/`, never out
- [0047](0047-headless-environment-keystone.md) The keystone move, the Environment graph itself
- [0048](0048-plugin-server-desktop-split.md) Splitting plugins along the server/desktop line
- [0025](0025-plugin-module-discovery-layout.md) How plugin modules are discovered and ordered
- [0027](0027-desktop-auto-starts-local-ffast-server.md) The desktop always talks to a server, even locally
- [0049](0049-demote-the-pipeline-executor.md) Deleting an executor that ordered exactly one edge
- [0008](0008-no-legacy-session-migration.md) No migration path for old sessions, and why

## Metrics and analysis

- [0011](0011-pure-metrics-with-configuration-driven-presentation.md) Pure metrics, presentation in config
- [0019](0019-metric-watcher-replaces-datatype-system.md) MetricWatcher and InputResolver replace the DataType system
- [0018](0018-metric-adapter-bridge-for-legacy-plots.md) The transitional bridge (superseded by 0019)
- [0021](0021-client-side-2d-panels-with-transform-metrics.md) Panels, panel kinds, reductions as transform metrics
- [0022](0022-incremental-keyed-panel-refresh.md) Refresh by diffing series instead of clear-and-rebuild
- [0023](0023-dataset-fields-key-in-ref.md) Any extxyz key becomes a metric, no Python
- [0042](0042-expression-metrics.md) Element-wise algebra over metrics, in the config file
- [0035](0035-metric-execution-context.md) Resolve metric inputs once instead of three times
- [0046](0046-finish-metric-execution-context.md) Finishing that: one seam, one injected executor
- [0036](0036-datawatcher-configure-once.md) DataWatcher configures once (proposed)
- [0005](0005-auto-compute-on-selection.md) Compute plots when a dataset and prediction are both selected
- [0007](0007-toml-for-user-and-project-configuration.md) TOML as the config format
- [0029](0029-display-overrides-client-local-cosmetic-state.md) Renamed labels are client-local, not scientific state
- [0037](0037-display-override-state-presenter-split.md) Pure override state behind thin presenters (proposed)

## The 3D view

- [0010](0010-server-owned-visualization-state.md) The server owns visualization state and ships neutral scenes
- [0016](0016-atom-color-values-plus-descriptor-client-maps.md) Colours travel as values plus a colormap, the client maps them
- [0052](0052-stop-presentation-leaking-across-the-render-scene-seam.md) Plugging the leaks in that seam
- [0014](0014-vispy-scene-adapter-replaces-loupe-render-path.md) The vispy adapter replaces the old render path
- [0015](0015-client-side-ray-cast-picking.md) Picking is a client-side ray cast that commits a server selection
- [0017](0017-client-feature-descriptor-replaces-load-hooks.md) Descriptors replace the old load hooks
- [0009](0009-replace-legacy-module-extension-hooks.md) Retiring the legacy extension hooks
- [0002](0002-per-dataset-settings-in-loupe.md) Per-dataset settings, not per-module state dicts
- [0003](0003-force-vector-atom-filter-dedicated-selection-tool.md) A dedicated selection tool for the force-vector filter
- [0039](0039-loupe-single-pick-toolbar.md) One toolbar, one owner, a contextual strip
- [0040](0040-loupe-sidebar-regroup.md) Regrouping the sidebar and separating the two filters
- [0041](0041-displacement-geometry-trajectory-overlay.md) Displacement as geometry (proposed)
- [0038](0038-pipeline-stage-output-contracts.md) Stage output contracts, rejected, with the measurements
- [0054](0054-no-setting-to-parameter-map.md) Deleting a stage descriptor nobody read
- [0055](0055-metrics-run-in-parallel-and-scripts-wait-on-a-signal.md) A worker pool instead of one shared pipe, and a gate instead of a poll

## The web client

- [0045](0045-web-client-replaces-qt.md) The full replacement, in six phases
- [0043](0043-browser-mvp-analysis-workflow.md) The earlier, smaller MVP (superseded by 0045)
- [0044](0044-multi-client-view-controllers.md) Several clients on one session, and who is allowed to mutate
- [0050](0050-web-client-seams.md) Carving modules out of a 1300-line app object
- [0051](0051-retire-hollowed-shells.md) Deleting three abstractions that had stopped abstracting
- [0053](0053-compare-predictions-in-the-web-analysis-tabs.md) Multiple predictions per panel

## Running on a cluster

- [0028](0028-cluster-server-auto-bootstrap.md) The server installs itself on the cluster on first connect
- [0024](0024-cluster-session-reconnect-lifecycle.md) Reconnecting to a job that is still running
- [0030](0030-server-side-live-inference.md) Inference runs server-side, the client holds a ghost model

## Environment structure

- [0020](0020-environment-decomposed-into-composed-managers.md) Breaking up the god object by composition
- [0034](0034-loading-coordinator.md) One owner for all dataset, model and prediction loading

## Format

There is no template file. Copy a recent one. What matters is that it opens with
the concrete problem (an error, a measurement, a complaint from a user), names
the alternatives that were live at the time, and says plainly what the decision
gives up. An ADR that only argues for its own conclusion is not worth writing.
