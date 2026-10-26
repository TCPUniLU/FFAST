/**
 * The 3D colour bar on the focused 3D panel (to_fix 17; the desktop's
 * UI/loupe/colorbar_overlay.py): four pieces, the gradient bar, the name and
 * the highest and lowest value, each dragged, turned by 90° and selected on
 * its own. Click selects a piece, shift-click adds to the selection; the
 * right-click menu turns a piece (or its group), groups or ungroups the
 * selection, and switches the whole bar between the vertical and horizontal
 * layouts. Dragging snaps to the other pieces, with guide lines. The mouse
 * wheel over the bar makes it longer or shorter.
 *
 * The geometry is colorbar_layout.js; this file only draws it and listens.
 * One layout serves every colouring, kept in this browser (layout_state.js).
 * `draw` paints the pieces onto an exported PNG where they are on screen.
 */

import {
  PIECES, SCALE_RANGE, barSize, boxesFor, cleanLayout, defaultLayout, groupOf, groupSelection,
  keepInside, nextRotation, peers, preset, press, rotateBlock, rotateOne, snapDrag, toFractions, ungroup,
} from './colorbar_layout.js';
import { sharedMenu } from './actions.js';
import { gradientCss } from './colormap.js';
import { loadLayout, saveLayout } from './layout_state.js';

const STORE_KEY = 'colourBar';
const DRAG_PX = 3;   // a smaller move is a click (which edits a value or the unit)
const NAMES = { bar: 'bar', name: 'name', max: 'highest value', min: 'lowest value' };

/**
 * @param {{name: HTMLElement, max: HTMLElement, min: HTMLElement}} texts the
 *   name and the two values, already made (their own clicks still edit them)
 * @returns {{el: HTMLElement, bar: HTMLElement, setColormap: (name: string) => void,
 *   setShown: (on: boolean) => void, draw: (ctx: CanvasRenderingContext2D, opts: object) => void}}
 */
export function createColourBar(texts) {
  const root = document.createElement('div');
  root.id = 'colorbar';
  root.className = 'hidden';
  const bar = document.createElement('div');
  bar.id = 'colorbar-gradient';
  const inner = { bar, ...texts };
  const pieceEls = {};
  for (const key of PIECES) {
    const el = document.createElement('div');
    el.className = `cb-piece cb-piece-${key}`;
    el.dataset.piece = key;
    el.tabIndex = 0;
    el.setAttribute('aria-label', `Colour bar ${NAMES[key]}: drag to move; R turns it; right-click for more`);
    inner[key].classList.add('cb-inner');
    el.appendChild(inner[key]);
    root.appendChild(el);
    pieceEls[key] = el;
  }
  const guides = { v: document.createElement('div'), h: document.createElement('div') };
  guides.v.className = 'cb-guide cb-guide-v';
  guides.h.className = 'cb-guide cb-guide-h';
  guides.v.hidden = guides.h.hidden = true;
  root.append(guides.v, guides.h);

  const menuList = document.createElement('div');
  menuList.className = 'menu-list cb-menu hidden';
  menuList.setAttribute('role', 'menu');
  document.body.appendChild(menuList);
  const menu = sharedMenu(menuList);

  let layout = cleanLayout(loadLayout()[STORE_KEY]);
  let stops = gradientCss('viridis').split(', ');
  let boxes = null;          // piece → {x, y, w, h, rot} in panel pixels, as drawn
  let selected = [];
  let drag = null;
  let justDragged = false;

  const panel = () => ({ w: root.clientWidth, h: root.clientHeight });
  const textSizes = () => Object.fromEntries(['name', 'max', 'min'].map(
    (k) => [k, { w: inner[k].offsetWidth, h: inner[k].offsetHeight }]));
  /** A piece's upright size at rotation `rot`. */
  const upright = (key, rot) => (key === 'bar' ? barSize(panel(), layout.scale, rot) : textSizes()[key]);

  function save() {
    saveLayout({ [STORE_KEY]: layout });
  }

  /** Keep the pieces where they are now, as fractions of the panel. */
  function commit() {
    layout.pieces = toFractions(boxes, panel());
    save();
  }

  function place(key) {
    const b = boxes[key];
    const el = pieceEls[key];
    Object.assign(el.style, { left: `${b.x}px`, top: `${b.y}px`, width: `${b.w}px`, height: `${b.h}px` });
    if (key === 'bar') {
      const dir = { 0: 'to top', 90: 'to right', [-90]: 'to left' }[b.rot];
      Object.assign(bar.style, { width: `${b.w}px`, height: `${b.h}px`,
        background: `linear-gradient(${dir}, ${stops.join(', ')})` });
    } else {
      inner[key].style.transform = b.rot === 90 ? `translate(${b.w}px, 0) rotate(90deg)`
        : b.rot === -90 ? `translate(0, ${b.h}px) rotate(-90deg)` : '';
    }
    el.classList.toggle('selected', selected.includes(key));
  }

  function render() {
    const p = panel();
    if (root.classList.contains('hidden') || !p.w || !p.h || drag) return;
    boxes = boxesFor(layout, p, textSizes());
    for (const key of PIECES) place(key);
  }

  const showGuides = (vx, hy) => {
    guides.v.hidden = vx == null;
    guides.h.hidden = hy == null;
    if (vx != null) guides.v.style.left = `${vx}px`;
    if (hy != null) guides.h.style.top = `${hy}px`;
  };

  const select = (next) => {
    selected = next;
    for (const key of PIECES) pieceEls[key].classList.toggle('selected', selected.includes(key));
  };

  /** Turn a piece, or its group as one block, by 90°. */
  function rotate(key) {
    if (!boxes) return;
    const group = groupOf(layout.groups, key);
    if (group) {
      const block = Object.fromEntries(group.map((k) => [k, boxes[k]]));
      const ups = Object.fromEntries(group.map((k) => [k, upright(k, nextRotation(k, boxes[k].rot))]));
      Object.assign(boxes, rotateBlock(block, ups));
    } else {
      boxes[key] = rotateOne(key, boxes[key], upright(key, nextRotation(key, boxes[key].rot)));
    }
    for (const k of PIECES) boxes[k] = keepInside(boxes[k], panel());
    commit();
    render();
  }

  /** "Rotate all 90°": the vertical layout becomes the horizontal one and
   * back, anchored at the bar's corner. */
  function rotateAll() {
    if (!boxes) return;
    layout.orientation = layout.orientation === 'vertical' ? 'horizontal' : 'vertical';
    const sizes = { ...textSizes(), bar: upright('bar', layout.orientation === 'horizontal' ? 90 : 0) };
    layout.pieces = toFractions(preset(layout.orientation, { x: boxes.bar.x, y: boxes.bar.y }, sizes), panel());
    save();
    render();
  }

  function reset() {
    layout = defaultLayout();
    select([]);
    save();
    render();
  }

  const menuActions = (key) => [
    { id: 'cb-rotate', label: groupOf(layout.groups, key) ? 'Rotate group 90°' : 'Rotate 90°', run: () => rotate(key) },
    { id: 'cb-rotate-all', label: `Rotate all 90° (${layout.orientation === 'vertical' ? 'horizontal' : 'vertical'})`,
      run: rotateAll },
    { id: 'cb-group', label: 'Group', run: () => { layout.groups = groupSelection(layout.groups, selected); save(); },
      unavailable: () => (selected.length < 2 ? 'Shift-click pieces to select two or more first' : '') },
    { id: 'cb-ungroup', label: 'Ungroup', run: () => { layout.groups = ungroup(layout.groups, key); select([]); save(); },
      unavailable: () => (groupOf(layout.groups, key) ? '' : 'This piece is not in a group') },
    { separator: true },
    { id: 'cb-reset', label: 'Reset colour bar layout', run: reset },
  ];

  function move(e) {
    if (!drag) return;
    const dx = e.clientX - drag.x0;
    const dy = e.clientY - drag.y0;
    if (!drag.moved && Math.hypot(dx, dy) < DRAG_PX) return;
    drag.moved = true;
    const others = PIECES.filter((k) => !drag.moving.includes(k)).map((k) => drag.start[k]);
    const snap = snapDrag(drag.start[drag.key], others, dx, dy);
    for (const k of drag.moving) {
      boxes[k] = keepInside({ ...drag.start[k], x: drag.start[k].x + snap.dx, y: drag.start[k].y + snap.dy }, panel());
      place(k);
    }
    showGuides(snap.vx, snap.hy);
  }

  function end() {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', end);
    window.removeEventListener('pointercancel', end);
    if (!drag) return;
    const moved = drag.moved;
    drag = null;
    showGuides(null, null);
    if (!moved) return;
    justDragged = true;   // the click that ends a drag edits nothing
    setTimeout(() => { justDragged = false; });   // no click follows a drag that left the piece
    commit();
  }

  for (const key of PIECES) {
    const el = pieceEls[key];
    el.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || e.target.closest('input')) return;
      e.stopPropagation();   // not an orbit, a pick or a box selection
      const additive = e.shiftKey || e.metaKey || e.ctrlKey;
      select(press(selected, key, additive, layout.groups));
      if (additive || !boxes) return;
      drag = { key, moving: peers(key, layout.groups, selected), x0: e.clientX, y0: e.clientY,
        start: structuredClone(boxes), moved: false };
      // Window listeners, not pointer capture: a press without a drag stays
      // an ordinary click on the piece (it edits a limit or the unit).
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', end);
      window.addEventListener('pointercancel', end);
    });
    el.addEventListener('click', (e) => {
      if (!justDragged) return;
      justDragged = false;
      e.stopPropagation();
      e.preventDefault();
    }, true);
    el.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!selected.includes(key)) select(press(selected, key, false, layout.groups));
      menu.open(el, menuActions(key));
    });
    el.addEventListener('keydown', (e) => {
      if (e.target !== el) return;   // a value being typed
      const step = e.shiftKey ? 10 : 1;
      const nudge = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      if (nudge && boxes) {
        e.preventDefault();
        for (const k of peers(key, layout.groups, selected)) {
          boxes[k] = keepInside({ ...boxes[k], x: boxes[k].x + nudge[0], y: boxes[k].y + nudge[1] }, panel());
          place(k);
        }
        commit();
      } else if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        rotate(key);
      } else if (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) {
        e.preventDefault();
        menu.open(el, menuActions(key));
      } else return;
      e.stopPropagation();   // not the 3D view's own keys
    });
  }

  // The mouse wheel over the bar changes its length, not the camera's zoom.
  pieceEls.bar.addEventListener('wheel', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const scale = layout.scale * 1.05 ** (-e.deltaY / 100);
    layout.scale = Math.min(SCALE_RANGE[1], Math.max(SCALE_RANGE[0], scale));
    save();
    render();
  }, { passive: false });

  // A click anywhere else drops the selection (a group stays a group).
  document.addEventListener('pointerdown', (e) => {
    if (selected.length && !root.contains(e.target) && !menuList.contains(e.target)) select([]);
  }, true);

  // The panel resizes, or a value or the name changes its size.
  const sizes = new ResizeObserver(() => render());
  for (const el of [root, inner.name, inner.max, inner.min]) sizes.observe(el);

  return {
    el: root,
    bar,

    setColormap(name) {
      stops = gradientCss(name).split(', ');
      render();
    },

    setShown(on) {
      root.classList.toggle('hidden', !on);
      render();
    },

    /** Paint the pieces where they are on screen, onto an export of the
     * panel `scale` times its size on screen. Text takes the screen's colour
     * on a transparent export, else a colour that reads on `background`. */
    draw(ctx, { scale = 1, transparent = false, background = '#000000' } = {}) {
      if (root.classList.contains('hidden') || !boxes) return;
      const ink = transparent ? getComputedStyle(inner.name).color : inkOn(background);
      for (const key of PIECES) {
        const b = boxes[key];
        ctx.save();
        ctx.scale(scale, scale);
        ctx.translate(b.x, b.y);
        if (key === 'bar') {
          const [x0, y0, x1, y1] = { 0: [0, b.h, 0, 0], 90: [0, 0, b.w, 0], [-90]: [b.w, 0, 0, 0] }[b.rot];
          const grad = ctx.createLinearGradient(x0, y0, x1, y1);
          stops.forEach((c, i) => grad.addColorStop(i / Math.max(1, stops.length - 1), c));
          ctx.fillStyle = grad;
          ctx.fillRect(0, 0, b.w, b.h);
        } else {
          const el = inner[key];
          const style = getComputedStyle(el);
          if (b.rot === 90) { ctx.translate(b.w, 0); ctx.rotate(Math.PI / 2); }
          if (b.rot === -90) { ctx.translate(0, b.h); ctx.rotate(-Math.PI / 2); }
          ctx.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
          ctx.fillStyle = ink;
          ctx.textBaseline = 'middle';
          ctx.fillText(el.textContent, parseFloat(style.paddingLeft) || 0, el.offsetHeight / 2);
        }
        ctx.restore();
      }
    },
  };
}

/** A text colour that reads on `background` (#rrggbb). */
function inkOn(background) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(background || '');
  if (!m) return '#f0f0f0';
  const [r, g, b] = m.slice(1).map((h) => parseInt(h, 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5 ? '#1a1a1a' : '#f0f0f0';
}

/** A PNG of the panel (`url`, from the renderer) with the colour bar drawn
 * on, where it is on screen. `onScreen` is the panel's width in CSS pixels. */
export async function withColourBar(url, colourBar, onScreen, opts) {
  const img = new Image();
  img.src = url;
  await img.decode();
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0);
  colourBar.draw(ctx, { ...opts, scale: onScreen ? img.naturalWidth / onScreen : 1 });
  return canvas.toDataURL('image/png');
}
