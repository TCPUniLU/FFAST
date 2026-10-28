/**
 * Where the 3D colour bar's pieces sit (to_fix 17; the desktop's
 * UI/loupe/colorbar_overlay.py). Four pieces: the gradient **bar**, the
 * **name**, and the highest (**max**) and lowest (**min**) value. Each is
 * dragged, turned by 90° and selected on its own; selected pieces can be
 * grouped so they move and turn together. "Rotate all 90°" switches the whole
 * bar between a vertical and a horizontal layout.
 *
 * Pure geometry, so it tests without a page. A piece on screen is a box
 * `{x, y, w, h}` in pixels of the 3D panel, its top-left corner first; the
 * layout keeps each corner as a FRACTION of the panel, so a resized panel
 * keeps the arrangement. One layout serves every colouring (the desktop keeps
 * one per metric); the page keeps it in browser storage.
 *
 * Each piece also has a size, a factor on its default (`sizes`): the bar's
 * length, a text's font. The bar's thickness has its own factor
 * (`barThickness`), so the bar stretches in either direction on its own
 * (to_fix_v2); a text keeps its shape. Both are set with the corner grip a
 * selected piece shows.
 *
 * Rotation is 0, 90 or -90 degrees, turning the piece's content clockwise for
 * 90. The bar at 0 is upright with its highest colour on top; at 90 the top
 * turns to the right. A text at -90 reads bottom to top.
 */

export const PIECES = Object.freeze(['bar', 'name', 'max', 'min']);
/** Pixels between pieces in the two preset layouts. */
export const GAP = 6;
/** A dragged piece's edge or centre snaps to another's within this many pixels. */
export const SNAP_PX = 6;
/** The bar's thickness in pixels and its length as a fraction of the panel's
 * height (upright) or width (turned), before its own scale. */
export const BAR_THICKNESS = 14;
export const BAR_LENGTH_FRAC = 0.4;
export const SCALE_RANGE = Object.freeze([0.4, 4]);
/** Where a layout with nothing saved puts the bar's corner. */
const ORIGIN = Object.freeze({ x: 0.03, y: 0.3 });

const NAME_TURNS = [0, -90];
const OTHER_TURNS = [0, 90, -90];

const defaultSizes = () => Object.fromEntries(PIECES.map((k) => [k, 1]));

/** The layout with nothing saved: vertical, at the default place and size. */
export function defaultLayout() {
  return { orientation: 'vertical', sizes: defaultSizes(), barThickness: 1, origin: { ...ORIGIN }, pieces: null, groups: [] };
}

/** `layout` with anything missing or malformed replaced by the default, so a
 * stored layout from another version cannot break the page. */
export function cleanLayout(layout) {
  const d = defaultLayout();
  if (!layout || typeof layout !== 'object') return d;
  const num = (v, lo, hi) => Number.isFinite(v) && v >= lo && v <= hi;
  const out = {
    orientation: layout.orientation === 'horizontal' ? 'horizontal' : 'vertical',
    sizes: Object.fromEntries(PIECES.map((k) => {
      // a layout from before the grip kept the bar's size as `scale`
      const v = layout.sizes?.[k] ?? (k === 'bar' ? layout.scale : undefined);
      return [k, num(v, ...SCALE_RANGE) ? v : 1];
    })),
    barThickness: 1,
    origin: layout.origin && num(layout.origin.x, 0, 1) && num(layout.origin.y, 0, 1)
      ? { x: layout.origin.x, y: layout.origin.y } : d.origin,
    pieces: null,
    groups: [],
  };
  // a layout from before the thickness had its own factor: the bar's size was both
  const thick = layout.barThickness ?? out.sizes.bar;
  out.barThickness = num(thick, ...SCALE_RANGE) ? thick : 1;
  const pieces = layout.pieces;
  if (pieces && PIECES.every((k) => pieces[k] && num(pieces[k].x, -1, 2) && num(pieces[k].y, -1, 2)
      && turnsOf(k).includes(pieces[k].rot)))
    out.pieces = Object.fromEntries(PIECES.map((k) => [k, { x: pieces[k].x, y: pieces[k].y, rot: pieces[k].rot }]));
  if (Array.isArray(layout.groups))
    out.groups = layout.groups.filter((g) => Array.isArray(g) && g.length >= 2 && g.every((k) => PIECES.includes(k)))
      .map((g) => [...new Set(g)]).filter((g) => g.length >= 2);
  return out;
}

const turnsOf = (key) => (key === 'name' ? NAME_TURNS : OTHER_TURNS);

/** The rotation after `rot` for a piece: the name turns between upright and
 * reading bottom to top; the others go 0 → 90 → -90. */
export function nextRotation(key, rot) {
  const turns = turnsOf(key);
  return turns[(turns.indexOf(rot) + 1) % turns.length] ?? 0;
}

/** A piece's size on screen: its upright size, turned. */
export function turned(size, rot) {
  return rot ? { w: size.h, h: size.w } : { w: size.w, h: size.h };
}

/** The bar's upright size in a panel of `panel` pixels, at length factor
 * `scale` and thickness factor `thickness`. Upright, its length runs along
 * the panel's height; turned, along its width, so a horizontal bar is not
 * squeezed by a short panel. */
export function barSize(panel, scale = 1, rot = 0, thickness = scale) {
  const along = rot ? panel.w : panel.h;
  return { w: Math.round(BAR_THICKNESS * thickness), h: Math.max(20, Math.round(along * BAR_LENGTH_FRAC * scale)) };
}

/**
 * The two preset layouts, as the desktop draws them, anchored at the bar's
 * corner `origin` (pixels). Vertical: an upright bar, the highest value at
 * its top and the lowest at its bottom, beside it, and the name beyond them,
 * reading bottom to top. Horizontal: a turned bar, the lowest value under its
 * left end, the highest under its right end, the name centred below.
 * @param {'vertical'|'horizontal'} orientation
 * @param {{x: number, y: number}} origin
 * @param {{bar: object, name: object, max: object, min: object}} sizes upright sizes
 * @returns {Object<string, {x: number, y: number, rot: number}>}
 */
export function preset(orientation, origin, sizes) {
  const { x: ox, y: oy } = origin;
  if (orientation === 'horizontal') {
    const bar = turned(sizes.bar, 90);
    const vy = oy + bar.h + GAP;
    const valuesH = Math.max(sizes.min.h, sizes.max.h);
    return {
      bar: { x: ox, y: oy, rot: 90 },
      min: { x: ox, y: vy, rot: 0 },
      max: { x: ox + bar.w - sizes.max.w, y: vy, rot: 0 },
      name: { x: ox + (bar.w - sizes.name.w) / 2, y: vy + valuesH + GAP, rot: 0 },
    };
  }
  const bar = sizes.bar;
  const valuesW = Math.max(sizes.max.w, sizes.min.w);
  const name = turned(sizes.name, -90);
  return {
    bar: { x: ox, y: oy, rot: 0 },
    max: { x: ox + bar.w + GAP, y: oy, rot: 0 },
    min: { x: ox + bar.w + GAP, y: oy + bar.h - sizes.min.h, rot: 0 },
    name: { x: ox + bar.w + GAP + valuesW + GAP, y: oy + (bar.h - name.h) / 2, rot: -90 },
  };
}

/**
 * Every piece's box in pixels for a panel of `panel` pixels.
 * @param {object} layout a clean layout
 * @param {{w: number, h: number}} panel
 * @param {{name: object, max: object, min: object}} textSizes upright sizes of the texts
 * @returns {Object<string, {x: number, y: number, w: number, h: number, rot: number}>}
 */
export function boxesFor(layout, panel, textSizes) {
  const placed = layout.pieces || toFractions(preset(layout.orientation,
    { x: layout.origin.x * panel.w, y: layout.origin.y * panel.h },
    { ...textSizes, bar: barSize(panel, layout.sizes.bar, layout.orientation === 'horizontal' ? 90 : 0, layout.barThickness) }), panel);
  const out = {};
  for (const key of PIECES) {
    const { x, y, rot } = placed[key];
    const upright = key === 'bar' ? barSize(panel, layout.sizes.bar, rot, layout.barThickness) : textSizes[key];
    out[key] = keepInside({ x: x * panel.w, y: y * panel.h, ...turned(upright, rot), rot }, panel);
  }
  return out;
}

/** A piece's size factor after its corner grip moved by (dx, dy) pixels
 * from where it was pressed, when it was `box` at size `size`: the grip's
 * move along the box's diagonal, so the piece keeps its shape. */
export function resized(box, size, dx, dy) {
  const d2 = box.w * box.w + box.h * box.h;
  const factor = d2 ? ((box.w + dx) * box.w + (box.h + dy) * box.h) / d2 : 1;
  return Math.min(SCALE_RANGE[1], Math.max(SCALE_RANGE[0], size * factor));
}

/** The bar's length and thickness factors after its corner grip moved by
 * (dx, dy) pixels from where it was pressed, when it was `box`: each
 * direction on its own. Upright, across is thickness and down is length;
 * turned, the other way round. */
export function resizedBar(box, length, thickness, dx, dy) {
  const clamp = (v) => Math.min(SCALE_RANGE[1], Math.max(SCALE_RANGE[0], v));
  const [across, along, dAcross, dAlong] = box.rot ? [box.h, box.w, dy, dx] : [box.w, box.h, dx, dy];
  return {
    length: clamp(along ? length * (along + dAlong) / along : length),
    thickness: clamp(across ? thickness * (across + dAcross) / across : thickness),
  };
}

/**
 * A text's size after its grip moved by (dx, dy), snapped (to_fix_v2) to
 * another text's height, the bar's thickness, or another piece's edge or
 * centre, when within `threshold` pixels. `others` maps each other piece's
 * key to its box. Returns the size, the guide lines to draw (as snapDrag)
 * and `match`, the piece whose size it took, or null.
 */
export function snapTextResize(box, size, dx, dy, others, threshold = SNAP_PX) {
  const raw = resized(box, size, dx, dy);
  const k = raw / size;
  const across = (b) => (b.rot ? b.w : b.h);
  const reach = box.x + box.w * k;
  const low = box.y + box.h * k;
  const cands = [];
  for (const [key, o] of Object.entries(others)) {
    // the bar's height is its narrow side, whatever its turn
    const target = key === 'bar' ? Math.min(o.w, o.h) : across(o);
    cands.push({ size: size * target / across(box), d: Math.abs(across(box) * k - target), vx: null, hy: null, match: key });
    for (const v of [o.x, o.x + o.w / 2, o.x + o.w]) {
      if (v > box.x) cands.push({ size: size * (v - box.x) / box.w, d: Math.abs(reach - v), vx: v, hy: null, match: null });
    }
    for (const v of [o.y, o.y + o.h / 2, o.y + o.h]) {
      if (v > box.y) cands.push({ size: size * (v - box.y) / box.h, d: Math.abs(low - v), vx: null, hy: v, match: null });
    }
  }
  let best = null;
  for (const c of cands) {
    if (c.size < SCALE_RANGE[0] || c.size > SCALE_RANGE[1]) continue;
    // on a tie a size match wins over an edge
    if (!best || c.d < best.d || (c.d === best.d && c.match && !best.match)) best = c;
  }
  if (best && best.d <= threshold) return { size: best.size, vx: best.vx, hy: best.hy, match: best.match };
  return { size: raw, vx: null, hy: null, match: null };
}

/** Pixel places as fractions of the panel. */
export function toFractions(places, panel) {
  const out = {};
  for (const [key, p] of Object.entries(places))
    out[key] = { x: panel.w ? p.x / panel.w : 0, y: panel.h ? p.y / panel.h : 0, rot: p.rot };
  return out;
}

/** `box` moved so it stays inside the panel (a panel made smaller would
 * otherwise push a piece out of sight). */
export function keepInside(box, panel) {
  const x = Math.min(Math.max(0, box.x), Math.max(0, panel.w - box.w));
  const y = Math.min(Math.max(0, box.y), Math.max(0, panel.h - box.h));
  return { ...box, x, y };
}

/**
 * Snap a drag: the dragged piece `lead`, moved by (dx, dy), lines its left
 * edge, centre or right edge (and top, middle or bottom) up with another
 * piece's, when within `threshold` pixels. Returns the adjusted move and the
 * guide lines to draw: `vx` a vertical line's x, `hy` a horizontal line's y,
 * or null.
 */
export function snapDrag(lead, others, dx, dy, threshold = SNAP_PX) {
  const x0 = lead.x + dx;
  const y0 = lead.y + dy;
  const leadX = [x0, x0 + lead.w / 2, x0 + lead.w];
  const leadY = [y0, y0 + lead.h / 2, y0 + lead.h];
  let bx = { d: Infinity, by: 0, at: null };
  let by = { d: Infinity, by: 0, at: null };
  for (const t of others) {
    for (const l of leadX) for (const v of [t.x, t.x + t.w / 2, t.x + t.w])
      if (Math.abs(v - l) < bx.d) bx = { d: Math.abs(v - l), by: v - l, at: v };
    for (const l of leadY) for (const v of [t.y, t.y + t.h / 2, t.y + t.h])
      if (Math.abs(v - l) < by.d) by = { d: Math.abs(v - l), by: v - l, at: v };
  }
  const snapX = bx.d <= threshold;
  const snapY = by.d <= threshold;
  return {
    dx: dx + (snapX ? bx.by : 0), dy: dy + (snapY ? by.by : 0),
    vx: snapX ? bx.at : null, hy: snapY ? by.at : null,
  };
}

/** One piece turned to its next rotation about its own centre.
 * @param {string} key @param {{x,y,w,h,rot}} box @param {{w,h}} upright */
export function rotateOne(key, box, upright) {
  const rot = nextRotation(key, box.rot);
  const size = turned(upright, rot);
  return { x: box.x + box.w / 2 - size.w / 2, y: box.y + box.h / 2 - size.h / 2, ...size, rot };
}

/** Pieces turned together by 90°, as one block: each moves a quarter turn
 * about the block's centre and turns itself.
 * @param {Object<string, {x,y,w,h,rot}>} boxes the block's pieces
 * @param {Object<string, {w,h}>} uprights their upright sizes */
export function rotateBlock(boxes, uprights) {
  const all = Object.values(boxes);
  const cx = (Math.min(...all.map((b) => b.x)) + Math.max(...all.map((b) => b.x + b.w))) / 2;
  const cy = (Math.min(...all.map((b) => b.y)) + Math.max(...all.map((b) => b.y + b.h))) / 2;
  const out = {};
  for (const [key, b] of Object.entries(boxes)) {
    const dx = b.x + b.w / 2 - cx;
    const dy = b.y + b.h / 2 - cy;
    const rot = nextRotation(key, b.rot);
    const size = turned(uprights[key], rot);
    // a quarter turn clockwise: what was above the centre goes to its right
    out[key] = { x: cx - dy - size.w / 2, y: cy + dx - size.h / 2, ...size, rot };
  }
  return out;
}

// ── selection and groups ────────────────────────────────────────────────

/** The group `key` is in, or null. */
export function groupOf(groups, key) {
  return groups.find((g) => g.includes(key)) || null;
}

/** The selection after pressing `key`: shift (`additive`) adds or removes
 * it; a plain press selects its group (or it alone), and keeps a selection
 * it is part of, so dragging moves them all. */
export function press(selected, key, additive, groups) {
  if (additive) return selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key];
  if (selected.includes(key)) return selected;
  return [...(groupOf(groups, key) || [key])];
}

/** The pieces that move with `key`: its group, else the selection it is in,
 * else itself. */
export function peers(key, groups, selected) {
  const group = groupOf(groups, key);
  if (group) return [...group];
  if (selected.includes(key) && selected.length > 1) return [...selected];
  return [key];
}

/** The groups after grouping the selection; a piece is in one group only. */
export function groupSelection(groups, selected) {
  if (selected.length < 2) return groups;
  const rest = groups.map((g) => g.filter((k) => !selected.includes(k))).filter((g) => g.length >= 2);
  return [...rest, [...selected]];
}

/** The groups after ungrouping `key`'s group. */
export function ungroup(groups, key) {
  return groups.filter((g) => !g.includes(key));
}
