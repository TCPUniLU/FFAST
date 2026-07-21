/**
 * @file JSDoc types for the wire protocol (ADR 0045 decision #2).
 *
 * These mirror the renderer-neutral Pydantic models the server packs onto
 * the wire — the source of truth is `ffast/visualization/scene.py` and
 * `ffast/visualization/models.py`; keep this file in sync with them by hand
 * (there is no codegen — the zero-build stance rules out a build step, and a
 * "generate JSDoc from Pydantic" script would just be a differently-shaped
 * build step). No runtime code: importing this file is only useful for the
 * `@typedef` JSDoc it carries into other modules' `import('./protocol.js')`
 * type references, which VS Code (and `tsc --checkJs`) resolve without a
 * compile step.
 */

/**
 * @typedef {Object} CameraState
 * @property {[number, number, number]} center
 * @property {number} distance
 * @property {number} azimuth
 * @property {number} elevation
 * @property {number} fov
 * @property {'perspective'|'orthographic'} projection
 */

/**
 * Value-driven atom coloring (ADR 0016). vmin/vmax are resolved server-side
 * so all renderers and the colorbar agree.
 * @typedef {Object} AtomColorBy
 * @property {number[]} values one scalar per displayed atom
 * @property {string} colormap
 * @property {number} vmin
 * @property {number} vmax
 * @property {string} label
 * @property {string} unit
 */

/**
 * @typedef {Object} AtomScene
 * @property {number[][]} positions (N, 3)
 * @property {number[]} sizes (N,)
 * @property {number[][]} colors (N, 4) RGBA — element/default fallback
 * @property {number[]|null} [atom_ids] original structure index per displayed atom (ADR 0015)
 * @property {AtomColorBy|null} [color_by] present → map values to colors; absent → use colors
 */

/** @typedef {Object} BondScene
 * @property {number[][]} segments (2M, 3) line segment endpoints
 */

/** @typedef {Object} ForceScene
 * @property {number[][]} starts (N, 3)
 * @property {number[][]} vectors (N, 3)
 * @property {number[][]} colors (N, 4) RGBA
 */

/** @typedef {Object} LabelScene
 * @property {number[][]} positions
 * @property {string[]} texts
 * @property {number[][]} colors (K, 4) RGBA
 */

/** @typedef {Object} UnitCellScene
 * @property {number[][]} segments 12 unit-cell edges as 24 endpoint pairs
 */

/** @typedef {Object} SelectionOverlay
 * @property {string} name
 * @property {number[]} atom_indices
 * @property {number[]} color RGBA
 */

/**
 * Renderer-neutral description of one Visualization View's scene.
 * @typedef {Object} RenderScene
 * @property {string} view_id
 * @property {number} version
 * @property {AtomScene|null} atoms
 * @property {BondScene|null} bonds
 * @property {ForceScene|null} forces
 * @property {LabelScene|null} labels
 * @property {UnitCellScene|null} unit_cell
 * @property {SelectionOverlay[]} selections
 * @property {CameraState} camera
 */

/**
 * SCENE_SNAPSHOT kwargs — the full versioned scene, sent when opening or
 * recovering a view.
 * @typedef {Object} SceneSnapshotKwargs
 * @property {RenderScene} scene
 */

/**
 * SCENE_PATCH kwargs — the server packs `ScenePatch.model_dump()` directly as
 * kwargs (no `.patch` wrapper); fields absent from `changed` are `null` and
 * must be ignored, not treated as "clear this component".
 * @typedef {Object} ScenePatchKwargs
 * @property {string} view_id
 * @property {number} from_version
 * @property {number} to_version
 * @property {string[]} changed names of updated components
 * @property {AtomScene|null} [atoms]
 * @property {BondScene|null} [bonds]
 * @property {ForceScene|null} [forces]
 * @property {LabelScene|null} [labels]
 * @property {UnitCellScene|null} [unit_cell]
 * @property {SelectionOverlay[]|null} [selections]
 * @property {CameraState|null} [camera]
 */

/**
 * COMMAND_RESULT kwargs — the server packs `CommandResult.model_dump()`
 * directly as kwargs (no `.result` wrapper), the outcome of a VIEW_COMMAND.
 * @typedef {Object} CommandResultKwargs
 * @property {boolean} success
 * @property {number} new_version
 * @property {ScenePatchKwargs|null} [patch]
 * @property {string|null} [error]
 * @property {string|null} [error_code]
 */

/**
 * The msgpack envelope every wire message carries (ffast/protocol/rpc.py pack/unpack).
 * @typedef {Object} WireMessage
 * @property {string} event
 * @property {any[]} args
 * @property {Object<string, any>} kwargs
 */

/**
 * VIEW_COMMAND kwargs (ffast/visualization/commands.py `ViewCommand`) — a
 * discriminated union on `type`; only the variants app.js sends are typed
 * here (SET_FRAME/SET_CAMERA are sent directly, not through `_sendViewCommand`).
 * @typedef {Object} ToggleFeatureCommand
 * @property {'TOGGLE_FEATURE'} type
 * @property {string} view_id
 * @property {number} view_version
 * @property {string} feature
 * @property {boolean} enabled
 */

/**
 * @typedef {Object} SetParameterCommand
 * @property {'SET_PARAMETER'} type
 * @property {string} view_id
 * @property {number} view_version
 * @property {string} stage_id
 * @property {string} parameter
 * @property {any} value
 */

/**
 * @typedef {Object} SetSelectionCommand
 * @property {'SET_SELECTION'} type
 * @property {string} view_id
 * @property {number} view_version
 * @property {string} name
 * @property {'current_structure'|'stable_topology'|'element'|'per_structure'} scope
 * @property {number[]} indices
 */

/**
 * One tunable parameter in a {@link MetricCatalogEntry} (ffast/protocol/messages.py
 * `MetricCatalogParameter`).
 * @typedef {Object} MetricCatalogParameter
 * @property {'choice'|'float'|'bool'} type
 * @property {string[]} [choices]
 * @property {number} [min]
 * @property {number} [max]
 * @property {any} default
 */

/**
 * One metric in the METRIC_CATALOG message (ffast/protocol/messages.py
 * `MetricCatalogEntry`) — filtered to `shape` in `("N_atoms","N_elements")`
 * for atom-colorable metrics (ADR 0016).
 * @typedef {Object} MetricCatalogEntry
 * @property {string} id
 * @property {string} [label]
 * @property {string} shape
 * @property {string} [unit]
 * @property {Object<string, MetricCatalogParameter>} [parameters]
 */

/**
 * METRIC_CATALOG kwargs (ffast/protocol/messages.py `MetricCatalog`).
 * @typedef {Object} MetricCatalogKwargs
 * @property {MetricCatalogEntry[]} metrics
 */

export {};
