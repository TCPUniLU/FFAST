/**
 * "Run with FFAST" (ADR 0086, plan step 5): one dialog, three steps.
 *
 * 1 Choose: input script, job script, reference (on the server's disk), an
 *   optional new data file, stop on alarm.
 * 2 Check: the server loads the job's LAMMPS module on its node, places the
 *   include and wraps the launch (PREPARE_RUN_WITH_FFAST); every check is
 *   listed per file, a refusal names the line and the edit to make by hand.
 * 3 Review: each copy as a diff in its own tab; "Write copies and submit"
 *   writes them next to the originals (WRITE_RUN_WITH_FFAST), submits from
 *   this computer over SSH or from the server's node, then follows the job
 *   (WATCH_JOB) and opens the live run on its first frame. Closing the dialog
 *   keeps following it. The originals are never changed.
 */
import { labelBy, trapFocus } from './dialogs.js';
import { OUT } from './events.js';

/** A unified diff as lines with a class each (pure, for tests). */
export function diffLines(diff) {
  return String(diff || '').split('\n').filter((l) => l !== '').map((text) => {
    let cls = 'ctx';
    if (text.startsWith('+++') || text.startsWith('---') || text.startsWith('@@')) cls = 'head';
    else if (text.startsWith('+')) cls = 'add';
    else if (text.startsWith('-')) cls = 'del';
    return { cls, text };
  });
}

/** A copy's tab label: "run.ffast.in (+3 −1)" or "run.ffast.data (new, 21 atoms)". */
export function copyLabel(copy) {
  const name = String(copy.copy || '').split('/').pop();
  if (copy.kind === 'data') return `${name} (new, ${copy.atoms} atoms)`;
  return `${name} (+${copy.added} −${copy.removed})`;
}

/** What the progress line says for a JOB_STATE (pure, for tests). */
export function jobStateText(kw) {
  const job = `job ${kw.job_id}`;
  switch (kw.state) {
    case 'queued': return `${job} is waiting in the queue`;
    case 'running': return `${job} is running; waiting for ffast-lammps to start the run`;
    case 'started': return `${job}: run folder ${kw.folder}; waiting for the first frame`;
    case 'frame': return `${job}: first frame arrived; opening the live run`;
    case 'ended': return `${job}: ${kw.reason || 'ended before its first frame'}`;
    default: return `${job}: ${kw.reason || 'state unknown'}`;
  }
}

/** Why the Choose step cannot go on, or '' (pure, for tests). */
export function chooseProblem(v) {
  if (!v.input || !v.job || !v.bundle) return 'Pick the input script, the job script and the reference.';
  if (v.data === 'file' && !v.dataPath) return 'Pick the structure file the data file is written from.';
  if (/\s/.test(v.input + v.job + v.bundle)) return 'LAMMPS cannot read paths with spaces.';
  return '';
}

const SECTION_TITLES = { module: 'LAMMPS module', input: 'Input script', job: 'Job script' };

/**
 * @param {{send: (event: string, kw: object) => void,
 *   pickFile: (title: string, onPick: (path: string) => void) => void,
 *   laptop: () => Promise<{ok: boolean, reason?: string}>,
 *   submitFromLaptop: (folder: string, script: string) => Promise<{job_id?: string, error?: string}>,
 *   openRun: (folder: string) => void, status?: (text: string, kind?: string) => void}} o
 */
export function openRunWithFfastDialog({ send, pickFile, laptop, submitFromLaptop, openRun, status = () => {} }) {
  document.getElementById('run-with-ffast-dialog')?.remove();
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const modal = el('div', 'warning-modal');
  modal.id = 'run-with-ffast-dialog';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  const panel = el('div', 'warning-modal-panel');
  const head = el('div', 'warning-modal-title rwf-head');
  const title = el('span', '', 'Run with FFAST');
  const steps = el('span', 'rwf-steps');
  const stepEls = ['1 Choose', '2 Check', '3 Review'].map((t) => el('span', '', t));
  steps.append(...stepEls);
  head.append(title, steps);
  labelBy(modal, title);
  const body = el('div', 'rwf-body');
  const foot = el('div', 'warning-modal-footer');
  panel.append(head, body, foot);
  modal.appendChild(panel);

  // ── step 1: choose ──────────────────────────────────────────────────────
  const field = (id, placeholder, pickTitle) => {
    const input = el('input', 'rwf-path');
    input.id = id;
    input.placeholder = placeholder;
    input.spellcheck = false;
    const btn = el('button', '', '…');
    btn.type = 'button';
    btn.title = 'Choose on the server';
    btn.addEventListener('click', () => pickFile(pickTitle, (p) => { input.value = p; check(); }));
    return { input, btn };
  };
  const inp = field('rwf-input', 'run.in on the cluster', 'LAMMPS input script');
  const job = field('rwf-job', 'job.sh on the cluster', 'Slurm job script');
  const ref = field('rwf-bundle', 'aspirin.bundle (or its .lmp)', 'Reference (.bundle folder or .lmp file)');
  const dataOn = el('input');
  dataOn.type = 'checkbox';
  dataOn.id = 'rwf-data-on';
  dataOn.checked = true;
  const dataFrom = el('select', 'rwf-data-from');
  dataFrom.id = 'rwf-data-from';
  for (const [v, t] of [['bundle', "the reference's own geometry"], ['file', 'a frame of a structure file']]) {
    const o = el('option', '', t);
    o.value = v;
    dataFrom.appendChild(o);
  }
  const dataFile = field('rwf-data-path', 'structure file (extxyz, xyz, …)', 'Structure file');
  const dataIndex = el('input', 'rwf-index');
  dataIndex.id = 'rwf-data-index';
  dataIndex.type = 'number';
  dataIndex.value = '0';
  dataIndex.title = 'Frame (0 = first, -1 = last)';
  const fileRow = el('div', 'rwf-row');
  fileRow.append(el('span', '', 'Frame from file'), dataFile.input, dataIndex, dataFile.btn);
  const stopOn = el('input');
  stopOn.type = 'checkbox';
  stopOn.id = 'rwf-stop';
  const problem = el('div', 'rwf-problem');
  const row = (label, ...f) => {
    const r = el('label', 'rwf-row');
    r.append(el('span', '', label), ...f);
    return r;
  };
  const dataLabel = el('label', 'rwf-check');
  dataLabel.append(dataOn, el('span', '', ' Write a new data file from a reference frame'));
  const stopLabel = el('label', 'rwf-check');
  stopLabel.append(stopOn, el('span', '', ' stop LAMMPS when an alarm fires'));
  const choose = el('div', 'rwf-step');
  choose.append(
    row('Input script', inp.input, inp.btn), row('Job script', job.input, job.btn),
    row('Reference', ref.input, ref.btn),
    row('Data file', dataLabel),
    el('div', 'rwf-note', "Proves the atom order. Replaces the input's read_data file in the copy."),
    row('Frame from', dataFrom), fileRow, row('Stop on alarm', stopLabel), problem);

  const values = () => ({
    input: inp.input.value.trim(), job: job.input.value.trim(), bundle: ref.input.value.trim(),
    data: dataOn.checked ? dataFrom.value : 'none', dataPath: dataFile.input.value.trim(),
    dataIndex: parseInt(dataIndex.value, 10) || 0, stop: stopOn.checked,
  });
  const request = () => {
    const v = values();
    const kw = { input: v.input, job: v.job, bundle: v.bundle, data: v.data, stop_on_alarm: v.stop };
    if (v.data === 'file') Object.assign(kw, { data_path: v.dataPath, data_index: v.dataIndex });
    return kw;
  };
  function check() {
    dataFrom.disabled = !dataOn.checked;
    fileRow.hidden = !(dataOn.checked && dataFrom.value === 'file');
    const p = chooseProblem(values());
    problem.textContent = p;
    if (state.step === 0) next.disabled = !!p;
    return !p;
  }

  // ── step 2: check ───────────────────────────────────────────────────────
  const checkStep = el('div', 'rwf-step');
  // ── step 3: review ──────────────────────────────────────────────────────
  const review = el('div', 'rwf-step');
  const tabs = el('div', 'rwf-tabs');
  tabs.setAttribute('role', 'tablist');
  const diffBox = el('pre', 'rwf-diff');
  const submitFrom = el('div', 'rwf-submit-from');
  const progress = el('div', 'rwf-progress');
  progress.setAttribute('aria-live', 'polite');
  review.append(tabs, diffBox, submitFrom, progress);

  const back = el('button', '', '‹ Back');
  const cancel = el('button', '', 'Cancel');
  const next = el('button', 'rwf-go', 'Check ›');
  const footLeft = el('span', 'rwf-foot-note');
  foot.append(footLeft, back, cancel, next);

  const state = { step: 0, prepared: null, request: null, jobId: null, via: 'laptop', laptopOk: false,
    watching: false, open: true };

  function show(step) {
    state.step = step;
    stepEls.forEach((s, i) => { s.className = i < step ? 'done' : i === step ? 'on' : ''; });
    body.replaceChildren([choose, checkStep, review][step]);
    back.hidden = step === 0;
    next.textContent = ['Check ›', 'Review ›', 'Write copies and submit'][step];
    footLeft.textContent = step === 2 ? 'Copies are written next to the originals; the originals are never changed.' : '';
    next.disabled = false;
    if (step === 0) check();
    if (step === 1) next.disabled = !(state.prepared && state.prepared.ok);
  }

  function renderCheck(kw) {
    const parts = [];
    for (const key of ['module', 'input', 'job']) {
      const items = (kw.sections || {})[key] || [];
      if (!items.length) continue;
      const sec = el('div', 'rwf-sec');
      sec.appendChild(el('h4', '', SECTION_TITLES[key]));
      const ul = el('ul', 'rwf-checks');
      for (const c of items) {
        const li = el('li', c.status === 'ok' ? 'ok' : 'warn');
        li.append(el('span', 'rwf-mark', c.status === 'ok' ? '✓' : '!'), el('span', '', c.text));
        ul.appendChild(li);
      }
      sec.appendChild(ul);
      parts.push(sec);
    }
    if (kw.refusal) {
      const r = kw.refusal;
      const box = el('div', 'rwf-refusal');
      const where = r.line ? `line ${r.line}: ` : '';
      box.append(el('b', '', `Cannot run this with FFAST (${r.file || 'files'}).`), el('div', '', where + r.reason));
      if (r.hand_edit) box.append(el('div', 'rwf-hand', `Edit by hand: ${r.hand_edit}`));
      parts.push(box);
    }
    checkStep.replaceChildren(...parts);
  }

  function renderReview(kw) {
    const copies = kw.copies || [];
    tabs.replaceChildren();
    const pick = (i) => {
      [...tabs.children].forEach((t, j) => t.setAttribute('aria-selected', String(i === j)));
      const c = copies[i];
      if (c.kind === 'data') {
        diffBox.replaceChildren(el('div', 'head', `${c.copy}: new LAMMPS data file, ${c.atoms} atoms, `
          + `from ${c.source}; recorded so the run's atom order is proven.`));
        return;
      }
      diffBox.replaceChildren(...diffLines(c.diff).map((l) => el('div', l.cls, l.text)));
    };
    copies.forEach((c, i) => {
      const t = el('button', 'rwf-tab', copyLabel(c));
      t.type = 'button';
      t.setAttribute('role', 'tab');
      t.addEventListener('click', () => pick(i));
      tabs.appendChild(t);
    });
    if (copies.length) pick(0);
    // where sbatch runs: this computer (SSH) by default, else the server's node
    submitFrom.replaceChildren(el('span', '', 'Submit from: '));
    const radio = (value, label) => {
      const l = el('label', 'rwf-radio');
      const r = el('input');
      r.type = 'radio';
      r.name = 'rwf-via';
      r.value = value;
      r.addEventListener('change', () => { state.via = value; });
      l.append(r, el('span', '', ` ${label}`));
      return { l, r };
    };
    const lap = radio('laptop', 'this computer (SSH)');
    const node = radio('server', "the FFAST server's node");
    submitFrom.append(lap.l, node.l);
    const why = el('span', 'rwf-note');
    submitFrom.appendChild(why);
    lap.r.disabled = true;
    node.r.checked = true;
    state.via = 'server';
    laptop().then((a) => {
      state.laptopOk = !!a.ok;
      lap.r.disabled = !a.ok;
      why.textContent = a.ok ? '' : ` (${a.reason})`;
      if (a.ok && !state.jobId) { lap.r.checked = true; state.via = 'laptop'; }
    }).catch(() => { why.textContent = ' (this computer cannot reach the cluster)'; });
  }

  // ── actions ─────────────────────────────────────────────────────────────
  next.addEventListener('click', () => {
    if (state.step === 0) {
      if (!check()) return;
      state.prepared = null;
      state.request = request();
      checkStep.replaceChildren(el('div', 'rwf-wait',
        "Checking… (loading the job's LAMMPS module on the server's node can take a minute)"));
      show(1);
      next.disabled = true;
      send(OUT.PREPARE_RUN_WITH_FFAST, state.request);
    } else if (state.step === 1) {
      renderReview(state.prepared);
      show(2);
    } else if (state.step === 2) {
      next.disabled = true;
      back.disabled = true;
      progress.textContent = 'Writing the copies…';
      send(OUT.WRITE_RUN_WITH_FFAST, { ...state.request, originals: state.prepared.originals });
    }
  });
  back.addEventListener('click', () => show(Math.max(0, state.step - 1)));
  let untrap = () => {};
  const close = () => {
    state.open = false;
    modal.remove();
    untrap();
    if (state.watching) status(`Run with FFAST: following job ${state.jobId}; its tab opens on the first frame`);
  };
  cancel.addEventListener('click', close);
  modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  for (const f of [inp.input, job.input, ref.input, dataFile.input, dataIndex]) f.addEventListener('input', check);
  dataOn.addEventListener('change', check);
  dataFrom.addEventListener('change', check);

  show(0);
  untrap = trapFocus(modal);
  document.body.appendChild(modal);
  inp.input.focus();

  const failed = (text) => {
    progress.replaceChildren(el('div', 'rwf-error', text));
    next.disabled = false;
    back.disabled = false;
  };
  const submitted = (jobId) => {
    state.jobId = jobId;
    state.watching = true;
    cancel.textContent = 'Close (keep following)';
    progress.textContent = `Submitted as job ${jobId}.`;
    send(OUT.WATCH_JOB, { job_id: String(jobId) });
  };

  return {
    /** RUN_WITH_FFAST_PREPARED */
    onPrepared(kw) {
      if (!state.open || state.step !== 1) return;
      state.prepared = kw;
      renderCheck(kw);
      next.disabled = !kw.ok;
    },
    /** RUN_WITH_FFAST_WRITTEN: then submit from the chosen machine. */
    async onWritten(kw) {
      if (!state.open) return;
      if (!kw.ok) { failed((kw.refusal && kw.refusal.text) || 'The copies were not written.'); return; }
      progress.textContent = `Copies written: ${(kw.written || []).join(', ')}. Submitting…`;
      const { folder, script } = kw.submit;
      if (state.via === 'laptop') {
        let out;
        try { out = await submitFromLaptop(folder, script); } catch (e) { out = { error: e.message }; }
        if (out.error) failed(`Submitting from this computer failed: ${out.error}`);
        else submitted(out.job_id);
      } else {
        send(OUT.SUBMIT_JOB, { folder, script });
      }
    },
    /** JOB_SUBMITTED (the server's node submitted it). */
    onSubmitted(kw) {
      if (kw.error) failed(`Submitting from the server's node failed: ${kw.error}`);
      else submitted(kw.job_id);
    },
    /** JOB_STATE: progress; the first frame opens the run, even after Close. */
    onJobState(kw) {
      if (String(kw.job_id) !== String(state.jobId)) return;
      const text = jobStateText(kw);
      if (state.open) {
        const line = el('div', kw.state === 'ended' ? 'rwf-error' : 'rwf-state', text);
        const parts = [line];
        if (kw.state === 'ended' && kw.scorer_log) parts.push(el('pre', 'rwf-log', kw.scorer_log));
        progress.replaceChildren(...parts);
      } else {
        status(`Run with FFAST: ${text}`, kw.state === 'ended' ? 'error' : 'connected');
      }
      if (kw.state === 'frame') {
        state.watching = false;
        openRun(kw.folder);
        if (state.open) close();
      } else if (kw.state === 'ended') {
        state.watching = false;
      }
    },
    close,
    get jobId() { return state.jobId; },
  };
}
