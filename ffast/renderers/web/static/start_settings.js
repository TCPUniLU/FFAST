/**
 * An independent 3D panel's starting look (ADR 0056 rule 13): the
 * `[tabs.panels.start]` keys of a tab file, turned into the view commands
 * that set it, and back. Colouring by a metric and force arrows use the
 * panel's own prediction; the file never names data. A key left out keeps
 * today's default. Pure functions, so they test without a server.
 */

/** Today's look of a new view, which unset keys keep. */
export const DEFAULT_LOOK = Object.freeze({
  source: 'element',
  colormap: 'viridis',
  atomSize: 1,
  bondWidth: 100,
  bondColour: '#404040',
  forces: Object.freeze({ show: false, length: 10, normalised: true }),
});

const COLOUR = 'ffast.atom_color';

/** Metric shapes that colour atoms: one value per atom or per element. */
export const COLORABLE_SHAPES = new Set(['N_atoms', 'N_elements']);

/** The keys that are set: the server sends unset ones as null. Null when
 * none are, so two starts compare equal however they were written. */
export function cleanStart(start) {
  const out = Object.fromEntries(Object.entries(start || {}).filter(([, v]) => v != null));
  return Object.keys(out).length ? out : null;
}

/**
 * The view commands that give a new independent panel its starting look.
 * @param {object|null} start the panel's `start` keys
 * @param {{modelFp: string|null, catalog: Array<{id: string}>}} ctx
 * @returns {{commands: object[], bondStyle: Array|null, note: string, settings: object}}
 *   `bondStyle` is drawn in the browser; `note` says why a colour metric was
 *   not used; `settings` is the look, for the settings sidebar.
 */
export function startCommands(start, { modelFp, catalog }) {
  const s = start || {};
  const commands = [];
  const set = (stage, parameter, value) =>
    commands.push({ type: 'SET_PARAMETER', stage_id: stage, parameter, value });
  const settings = { ...structuredClone(DEFAULT_LOOK), prediction: null };
  let note = '';

  if (s.colour_by != null) {
    let source = s.colour_by;
    if (source !== 'element' && source !== 'displacement') {
      const entry = (catalog || []).find((e) => e.id === source);
      if (entry && (entry.shape == null || COLORABLE_SHAPES.has(entry.shape))) source = `metric:${source}`;
      else {
        note = entry
          ? `Colour metric ${source} has no value per atom; showing element colours`
          : `Colour metric ${source} is not on this server; showing element colours`;
        source = 'element';
      }
    }
    set(COLOUR, 'source', source);
    settings.source = source;
    if (source.startsWith('metric:')) {
      set(COLOUR, 'prediction_ref', modelFp);
      settings.prediction = modelFp;
    }
  }
  if (s.colormap != null) {
    set(COLOUR, 'colormap', s.colormap);
    settings.colormap = s.colormap;
  }
  if (s.atom_size != null) {
    set('ffast.atom_sizes', 'scale', s.atom_size);
    settings.atomSize = s.atom_size;
  }

  let bondStyle = null;
  if (s.bond_width != null || s.bond_colour != null) {
    settings.bondWidth = s.bond_width ?? DEFAULT_LOOK.bondWidth;
    settings.bondColour = s.bond_colour ?? DEFAULT_LOOK.bondColour;
    bondStyle = [settings.bondWidth, settings.bondColour,
      settings.bondColour.toLowerCase() !== DEFAULT_LOOK.bondColour];
  }

  settings.forces.length = s.force_length ?? DEFAULT_LOOK.forces.length;
  settings.forces.normalised = s.force_normalised ?? DEFAULT_LOOK.forces.normalised;
  if (s.force_arrows != null) {
    commands.push({ type: 'TOGGLE_FEATURE', feature: 'forces', enabled: !!s.force_arrows });
    settings.forces.show = !!s.force_arrows;
  }
  if (s.force_arrows) {
    set('ffast.force_arrows', 'prediction_ref', modelFp);
    set('ffast.force_arrows', 'length_factor', settings.forces.length);
    set('ffast.force_arrows', 'normalised', settings.forces.normalised);
  }
  return { commands, bondStyle, note, settings };
}

/**
 * The look on screen as `start` keys ("Use current 3D settings as start",
 * rule 15). Defaults are left out, so the file holds only what differs.
 * @param {{source: string, colormap: string, atomSize: number, bondWidth: number,
 *   bondColour: string, forces: {show: boolean, length: number, normalised: boolean}}} cur
 */
export function startFromCurrent(cur) {
  const d = DEFAULT_LOOK;
  const out = {};
  if (cur.source && cur.source !== d.source) out.colour_by = cur.source.replace(/^metric:/, '');
  if (cur.colormap && cur.colormap !== d.colormap) out.colormap = cur.colormap;
  if (cur.atomSize != null && Number(cur.atomSize) !== d.atomSize) out.atom_size = Number(cur.atomSize);
  if (cur.bondWidth != null && Number(cur.bondWidth) !== d.bondWidth) out.bond_width = Number(cur.bondWidth);
  if (cur.bondColour && cur.bondColour.toLowerCase() !== d.bondColour) out.bond_colour = cur.bondColour;
  if (cur.forces?.show) {
    out.force_arrows = true;
    if (Number(cur.forces.length) !== d.forces.length) out.force_length = Number(cur.forces.length);
    if (cur.forces.normalised !== d.forces.normalised) out.force_normalised = !!cur.forces.normalised;
  }
  return out;
}
