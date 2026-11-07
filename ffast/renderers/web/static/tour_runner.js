// The guided-tour runner (ADR 0088). It lights one part of the page at a time,
// dims and locks the rest, and shows a box with the step's text. It knows
// nothing about FFAST: tours (tours.js) are data, and the app tells the runner
// what happened through named events on an EventTarget.

/**
 * @typedef {object} Step
 * @property {string} title
 * @property {string | (() => string)} body  text, or a function read each time the step shows
 * @property {Array<string | (() => Element|null)>} targets  selectors, or functions that find an element; joined into one lit area
 * @property {'top'|'bottom'|'left'|'right'|'inside'} [side]  where the box goes
 * @property {string} [until]  the event that ends a "do this" step; without it the step has Next
 * @property {(detail: object) => boolean} [accept]  which `until` events count, by their detail; all when absent
 * @property {(detail: object) => string} [refused]  what the wait text becomes when `accept` rejects an event
 * @property {string | (() => string)} [wait]  what the box says while it waits for `until`
 * @property {boolean} [stay]  after `until` fires, show Next instead of moving on, so the user can keep trying it
 * @property {() => boolean} [done]  true when the action is already done: show Next instead of waiting
 * @property {string | string[]} [failOn]  events that stop the tour, with `detail.error`, else `failText`, else the title
 * @property {string | ((detail: object) => string)} [failText]  the stop message when the event carries no error
 * @property {() => boolean} [skip]  leave the step out when the tour starts
 * @property {() => void} [prepare]  runs before the step is shown
 * @property {() => (void | Promise<unknown>)} [undo]  runs when Back returns to this step
 */

/**
 * @typedef {object} Tour
 * @property {string} id
 * @property {string} title
 * @property {string} about
 * @property {Step[]} steps
 * @property {() => string} [unavailable]  why the tour cannot start now, or ''
 * @property {boolean} [isDefault]  the tour the empty screen's button starts
 * @property {() => void} [onStart]  runs when the tour starts, before steps are skipped
 */

/** Space between a lit control and the edge of the lit area, in px. */
const PAD = 6;
const FOCUSABLE = 'button, input, select, textarea, a[href], canvas, [tabindex]:not([tabindex="-1"])';

let running = false;

/**
 * Show `tour` one step at a time over the page (ADR 0088). Only one tour runs
 * at a time; starting a second while one runs does nothing and returns null.
 * @param {Tour} tour
 * @param {{events: EventTarget, onEnd: (how: 'done'|'skip'|'stopped', message?: string) => void,
 *          doc?: Document}} opts
 * @returns {{stop: () => void} | null}
 */
export function startTour(tour, { events, onEnd, doc = document }) {
  if (running) return null;
  tour.onStart?.();
  const steps = tour.steps.filter((s) => !s.skip?.());
  if (!steps.length) {
    onEnd?.('done');
    return null;
  }
  running = true;
  const win = doc.defaultView;
  const lastFocus = doc.activeElement;
  const didThisRun = new Set();
  let index = 0;
  let stepOff = [];
  let raf = 0;
  let undoing = false;
  let ended = false;

  const el = (tag, cls, attrs = {}) => {
    const node = doc.createElement(tag);
    if (cls) node.className = cls;
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };
  // Position, stacking and pointer handling are set here, not left to a
  // stylesheet: the lock must hold on any page. index.html adds the looks.
  const layer = el('div', 'tour-layer');
  const shades = [0, 1, 2, 3].map(() => {
    const s = el('div', 'tour-shade');
    Object.assign(s.style, { position: 'fixed', zIndex: '2000', pointerEvents: 'auto' });
    return s;
  });
  const ring = el('div', 'tour-ring');
  Object.assign(ring.style, { position: 'fixed', zIndex: '2001', pointerEvents: 'none' });
  const box = el('div', 'tour-box', {
    role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'tour-title', 'aria-describedby': 'tour-body',
  });
  Object.assign(box.style, { position: 'fixed', zIndex: '2002' });
  const head = el('div', 'tour-head');
  const count = el('span', 'tour-count');
  const dots = el('span', 'tour-dots');
  head.append(count, dots);
  const title = el('h2', 'tour-title', { id: 'tour-title' });
  const body = el('p', 'tour-body', { id: 'tour-body' });
  const foot = el('div', 'tour-foot');
  const skip = el('button', 'tour-skip', { type: 'button' });
  skip.textContent = 'Skip tour';
  const back = el('button', 'tour-back', { type: 'button' });
  back.textContent = 'Back';
  const wait = el('span', 'tour-wait', { 'aria-live': 'polite' });
  const next = el('button', 'tour-next', { type: 'button' });
  foot.append(skip, back, wait, next);
  box.append(head, title, body, foot);
  layer.append(...shades, ring, box);
  doc.body.append(layer);

  const resolve = (target) => (typeof target === 'function' ? target() : doc.querySelector(target));
  const litElements = (step) => step.targets.map(resolve).filter((t) => t && t.isConnected);
  const listen = (name, handler) => {
    events.addEventListener(name, handler);
    stepOff.push(() => events.removeEventListener(name, handler));
  };
  const dropStepListeners = () => { stepOff.forEach((off) => off()); stepOff = []; };

  function end(how, message) {
    if (ended) return;
    ended = true;
    running = false;
    dropStepListeners();
    win.cancelAnimationFrame(raf);
    doc.removeEventListener('keydown', onKey, true);
    layer.remove();
    if (lastFocus?.isConnected) lastFocus.focus?.({ preventScroll: true });
    onEnd?.(how, message);
  }

  function show(i) {
    dropStepListeners();
    index = i;
    const step = steps[i];
    step.prepare?.();
    const last = i === steps.length - 1;
    count.textContent = `Step ${i + 1} of ${steps.length}`;
    dots.replaceChildren(...steps.map((_, k) => el('i', k < i ? 'done' : k === i ? 'now' : '')));
    title.textContent = step.title;
    const text = (v, ...a) => (typeof v === 'function' ? v(...a) : v);
    body.textContent = text(step.body);
    back.hidden = i === 0;
    const showNext = () => {
      wait.hidden = true;
      next.hidden = false;
      next.textContent = last ? 'Done' : 'Next';
    };
    const waiting = !!step.until && !didThisRun.has(step) && !step.done?.();
    if (waiting) {
      next.hidden = true;
      wait.hidden = false;
      wait.textContent = text(step.wait) || '';
      // Focus stays on the control in use, so a step that stays can be tried again.
      listen(step.until, (e) => {
        if (step.accept && !step.accept(e.detail ?? {})) {
          if (step.refused) wait.textContent = step.refused(e.detail ?? {});
          return;
        }
        didThisRun.add(step);
        if (step.stay) showNext();
        else advance();
      });
    } else {
      showNext();
    }
    for (const name of [step.failOn ?? []].flat()) {
      listen(name, (e) => end('stopped', e.detail?.error || text(step.failText, e.detail ?? {}) || step.title));
    }
    const first = resolve(step.targets[0]);
    if (waiting && first?.disabled) {
      end('stopped', first.title || `Tour stopped: “${step.title}” cannot be done now.`);
      return;
    }
    if (!place()) return;
    win.requestAnimationFrame(() => {
      if (!ended && index === i) (waiting ? first : next)?.focus?.({ preventScroll: true });
    });
  }

  function advance() {
    if (ended) return;
    if (index + 1 >= steps.length) end('done');
    else show(index + 1);
  }

  async function goBack() {
    if (ended || undoing || index === 0) return;
    const prev = steps[index - 1];
    if (prev.undo) {
      // The undo takes the action back, so the step waits for it again.
      didThisRun.delete(prev);
      dropStepListeners();
      undoing = true;
      back.hidden = true;
      next.hidden = true;
      wait.hidden = false;
      wait.textContent = 'Undoing…';
      try {
        await prev.undo();
      } catch (err) {
        undoing = false;
        end('stopped', err instanceof Error ? err.message : String(err));
        return;
      }
      undoing = false;
      if (ended) return;
    }
    show(index - 1);
  }

  /** Light the current step's targets and place the box. False when the tour stopped. */
  function place() {
    const step = steps[index];
    const rects = litElements(step).map((t) => t.getBoundingClientRect()).filter((r) => r.width > 0 && r.height > 0);
    if (!rects.length) {
      end('stopped', `Tour stopped: “${step.title}” needs something that is not on screen.`);
      return false;
    }
    const x = Math.min(...rects.map((r) => r.left)) - PAD;
    const y = Math.min(...rects.map((r) => r.top)) - PAD;
    const hole = {
      x, y,
      w: Math.max(...rects.map((r) => r.right)) + PAD - x,
      h: Math.max(...rects.map((r) => r.bottom)) + PAD - y,
    };
    const W = win.innerWidth;
    const H = win.innerHeight;
    const rect = (node, left, top, width, height) => Object.assign(node.style, {
      left: `${left}px`, top: `${top}px`, width: `${Math.max(0, width)}px`, height: `${Math.max(0, height)}px`,
    });
    rect(shades[0], 0, 0, W, hole.y);
    rect(shades[1], 0, hole.y + hole.h, W, H - hole.y - hole.h);
    rect(shades[2], 0, hole.y, hole.x, hole.h);
    rect(shades[3], hole.x + hole.w, hole.y, W - hole.x - hole.w, hole.h);
    rect(ring, hole.x, hole.y, hole.w, hole.h);
    placeBox(hole, step.side);
    return true;
  }

  /** Put the box on `side` of the hole, or the first other side where it fits; else inside it. */
  function placeBox(hole, side) {
    const bw = box.offsetWidth;
    const bh = box.offsetHeight;
    const W = win.innerWidth;
    const H = win.innerHeight;
    const gap = 14;
    const edge = 8;
    const at = {
      bottom: [hole.x + hole.w / 2 - bw / 2, hole.y + hole.h + gap],
      top: [hole.x + hole.w / 2 - bw / 2, hole.y - bh - gap],
      right: [hole.x + hole.w + gap, hole.y],
      left: [hole.x - bw - gap, hole.y],
    };
    const fits = ([l, t]) => l >= edge && t >= edge && l + bw <= W - edge && t + bh <= H - edge;
    const clamp = ([l, t]) => [Math.min(Math.max(edge, l), W - bw - edge), Math.min(Math.max(edge, t), H - bh - edge)];
    let pos;
    if (side !== 'inside') {
      for (const s of [side || 'bottom', 'bottom', 'top', 'right', 'left']) {
        const c = clamp(at[s]);
        // A clamped spot must still clear the hole.
        const overlaps = c[0] < hole.x + hole.w && c[0] + bw > hole.x && c[1] < hole.y + hole.h && c[1] + bh > hole.y;
        if (fits(at[s]) || !overlaps) { pos = c; break; }
      }
    }
    pos ??= clamp([hole.x + 16, hole.y + 16]);
    box.style.left = `${pos[0]}px`;
    box.style.top = `${pos[1]}px`;
  }

  // Every frame: the lit area follows resizes and panels that open or close.
  function follow() {
    if (ended) return;
    if (!undoing && !place()) return;
    raf = win.requestAnimationFrame(follow);
  }

  function onKey(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      end('skip');
      return;
    }
    if (e.key !== 'Tab') return;
    // Keep Tab inside the box and the lit controls.
    const lit = undoing ? [] : litElements(steps[index]);
    const pool = [
      ...lit.flatMap((t) => [...(t.matches(FOCUSABLE) ? [t] : []), ...t.querySelectorAll(FOCUSABLE)]),
      ...box.querySelectorAll('button'),
    ].filter((n) => !n.disabled && !n.hidden && n.getClientRects().length);
    if (!pool.length) return;
    const k = pool.indexOf(doc.activeElement);
    if (k === -1 || (e.shiftKey ? k === 0 : k === pool.length - 1)) {
      e.preventDefault();
      pool[e.shiftKey ? pool.length - 1 : 0].focus();
    }
  }

  skip.addEventListener('click', () => end('skip'));
  next.addEventListener('click', advance);
  back.addEventListener('click', goBack);
  doc.addEventListener('keydown', onKey, true);
  show(0);
  if (!ended) raf = win.requestAnimationFrame(follow);
  return { stop: () => end('skip') };
}
