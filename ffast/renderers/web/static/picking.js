/**
 * Pick controller (ADR 0045 issue 10 / ADR 0015).
 *
 * Turns pointer gestures on the 3D canvas into atom picks while a tool is
 * armed. Exactly one tool owns the pointer at a time (the Blender/Photoshop
 * modal-tool model, mirrored from the Qt loupe's single shared select
 * toolbar): arming disables orbit and shows a crosshair; a click resolves the
 * nearest visible atom; a drag with a rectangle-capable tool rubber-bands a
 * box. With no tool armed the pointer stays camera-orbit. The geometry itself
 * lives on the renderer (pickAtom/boxSelect); this class is just gesture +
 * arming state.
 */

const DRAG_THRESHOLD_PX = 4;   // below this a press+release counts as a click

/**
 * Toggle `atomId` in `list` (in → remove, else append), then apply the tool's
 * cycle window — the exact semantics of Qt's AtomSelectionBase.selectAtom
 * (visual.py:85). Mutates and returns `list`.
 * @param {number[]} list @param {number} atomId
 * @param {{cycle?: boolean, multiselect: number}} tool
 */
export function toggleSelect(list, atomId, tool) {
  const i = list.indexOf(atomId);
  if (i >= 0) list.splice(i, 1);
  else list.push(atomId);
  if (tool.cycle && list.length > tool.multiselect)
    list.splice(0, list.length - tool.multiselect);
  return list;
}

export class PickController {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {HTMLElement} viewport  element the rubber-band rect is drawn into
   * @param {import('./renderer.js').MoleculeRenderer} renderer
   * @param {{ getRadius: () => number, onPick: (entries: Array<{displayIndex:number, atomId:number}>, opts: {isBox: boolean}) => void }} cb
   */
  constructor(canvas, viewport, renderer, cb) {
    this._canvas = canvas;
    this._viewport = viewport;
    this._renderer = renderer;
    this._cb = cb;
    /** @type {null|{id:string, rectangle?:boolean}} */
    this._tool = null;
    this._down = null;   // {x, y} canvas-local at pointerdown
    this._moved = false;
    this._rect = null;   // rubber-band div, created lazily

    this._onDown = this._onPointerDown.bind(this);
    this._onMove = this._onPointerMove.bind(this);
    this._onUp = this._onPointerUp.bind(this);
    canvas.addEventListener('pointerdown', this._onDown);
  }

  get activeToolId() { return this._tool?.id ?? null; }

  /** Arm a tool (disarms any previous). Idempotent for the same id. */
  arm(tool) {
    this._tool = tool;
    this._renderer.setControlsEnabled(false);
    this._canvas.style.cursor = 'crosshair';
  }

  disarm() {
    this._tool = null;
    this._renderer.setControlsEnabled(true);
    this._canvas.style.cursor = '';
    this._hideRect();
    this._down = null;
    this._moved = false;
  }

  _canvasXY(e) {
    const r = this._canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  _onPointerDown(e) {
    if (!this._tool || e.button !== 0) return;
    e.preventDefault();
    this._down = this._canvasXY(e);
    this._moved = false;
    // Track move/up on window so a drag that leaves the canvas still resolves.
    window.addEventListener('pointermove', this._onMove);
    window.addEventListener('pointerup', this._onUp);
  }

  _onPointerMove(e) {
    if (!this._down) return;
    const p = this._canvasXY(e);
    const dx = p.x - this._down.x, dy = p.y - this._down.y;
    if (!this._moved && dx * dx + dy * dy > DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX)
      this._moved = true;
    if (this._moved && this._tool?.rectangle) this._showRect(this._down, p);
  }

  _onPointerUp(e) {
    window.removeEventListener('pointermove', this._onMove);
    window.removeEventListener('pointerup', this._onUp);
    const down = this._down;
    this._down = null;
    this._hideRect();
    if (!this._tool || !down) return;
    const up = this._canvasXY(e);

    if (this._moved && this._tool.rectangle) {
      const entries = this._renderer.boxSelect(down.x, down.y, up.x, up.y);
      this._cb.onPick(entries, { isBox: true });
      return;
    }
    // Click (or a drag with a non-rectangle tool): nearest atom at release.
    const hit = this._renderer.pickAtom(up.x, up.y, this._cb.getRadius());
    if (hit) this._cb.onPick([hit], { isBox: false });
  }

  _showRect(a, b) {
    if (!this._rect) {
      this._rect = document.createElement('div');
      this._rect.id = 'pick-rect';
      this._viewport.appendChild(this._rect);
    }
    const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
    this._rect.style.left = `${x}px`;
    this._rect.style.top = `${y}px`;
    this._rect.style.width = `${Math.abs(b.x - a.x)}px`;
    this._rect.style.height = `${Math.abs(b.y - a.y)}px`;
    this._rect.style.display = 'block';
  }

  _hideRect() {
    if (this._rect) this._rect.style.display = 'none';
  }
}
