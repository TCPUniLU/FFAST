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
import { answerOf } from './requests.js';
import { helpToggle } from './help.js';
import { icon } from './icons.js';

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
  if (v.target === 'local' && !v.input) return 'Pick the folder that holds your LAMMPS input.';
  if (v.target !== 'local' && (!v.job || !v.input)) return 'Pick the run folder: the one that holds your job script.';
  if (!v.bundle) return 'Choose a reference.';
  if (v.data === 'file' && !v.dataPath) return 'Pick the structure file the data file is written from.';
  if (/\s/.test(v.input + v.job + v.bundle)) return 'LAMMPS cannot read paths with spaces.';
  return '';
}

const SECTION_TITLES = { module: 'LAMMPS module', input: 'Input script', job: 'Job script' };

/**
 * @param {{send: (event: string, kw: object) => void,
 *   askServer?: (event: string, kw: object) => Promise<{kwargs: object}>,
 *   written?: (kw: object) => void,
 *   pick: (kind: 'folder'|'file'|'bundle', title: string, onPick: (path: string) => void) => void,
 *   laptop: () => Promise<{ok: boolean, reason?: string}>,
 *   submitFromLaptop: (folder: string, script: string) => Promise<{job_id?: string, error?: string}>,
 *   openRun: (folder: string) => void, status?: (text: string, kind?: string) => void, signal?: (name: string, detail?: object) => void,
 *   initialFolder?: string, initialReference?: string, localAvailable?: boolean, practice?: boolean}} o
 */
export function openRunWithFfastDialog({ send, askServer = null, written = () => {}, pick, laptop,
  submitFromLaptop, openRun, status = () => {},
  signal = (name, detail = {}) => {}, initialFolder = '', initialReference = '', localAvailable = false, practice = false }) {
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
  // Like the tab bar: the step you are on is bold with the accent underline, the
  // ones behind you carry a tick, a "›" lies between them.
  const stepEls = ['1 Choose', '2 Check', '3 Review'].map((t) => {
    const s = el('span');
    const tick = el('span', 'rwf-tick');
    tick.innerHTML = icon('check');
    s.append(tick, t);
    return s;
  });
  stepEls.forEach((s, i) => {
    if (i) {
      const sep = el('span', 'rwf-sep');
      sep.setAttribute('aria-hidden', 'true');
      sep.innerHTML = icon('chevron-right');
      steps.append(sep);
    }
    steps.append(s);
  });
  head.append(title, steps);
  labelBy(modal, title);
  const body = el('div', 'rwf-body');
  const foot = el('div', 'warning-modal-footer');
  panel.append(head, body, foot);
  modal.appendChild(panel);

  // ── step 1: choose a run folder; FFAST finds the rest (plan §7) ─────────
  const field = (id, placeholder, kind, pickTitle, onChosen) => {
    const input = el('input', 'rwf-path');
    input.id = id;
    input.placeholder = placeholder;
    input.spellcheck = false;
    const btn = el('button', '', '…');
    btn.type = 'button';
    btn.title = 'Choose on the server';
    btn.addEventListener('click', () => pick(kind, pickTitle, (p) => { input.value = p; onChosen?.(p); check(); }));
    return { input, btn };
  };
  const folder = field('rwf-folder', 'the folder that holds your job script', 'folder', 'Run folder',
    (p) => scanFolder(p));
  const found = el('div', 'rwf-found');
  found.id = 'rwf-found';
  const inputSel = el('select', 'rwf-select');
  inputSel.id = 'rwf-input';
  inputSel.setAttribute('aria-label', 'Input script');
  const jobSel = el('select', 'rwf-select');
  jobSel.id = 'rwf-job';
  const refSel = el('select', 'rwf-select');
  refSel.id = 'rwf-bundle';
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
  const dataFile = field('rwf-data-path', 'structure file (extxyz, xyz, …)', 'file', 'Structure file');
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
  problem.setAttribute('aria-live', 'polite');
  const row = (label, ...f) => {
    const r = el('label', 'rwf-row');
    r.append(el('span', '', label), ...f);
    return r;
  };
  const dataLabel = el('label', 'rwf-check');
  dataLabel.append(dataOn, el('span', '', ' Write a new data file from a reference frame'));
  const stopLabel = el('label', 'rwf-check');
  stopLabel.append(stopOn, el('span', '', ' stop LAMMPS when an alarm fires'));
  const where = el('fieldset', 'rwf-where');
  where.id = 'rwf-where';
  where.append(el('legend', '', 'Where it runs'));
  for (const [value, label] of [['cluster', 'On the cluster (Slurm)'], ['local', 'This computer']]) {
    const lab = el('label', 'rwf-radio');
    const r = el('input');
    r.type = 'radio'; r.name = 'rwf-target'; r.value = value; r.checked = value === 'cluster';
    if (value === 'local' && !localAvailable) {
      r.disabled = true;
      lab.title = 'Needs FFAST running on this computer (Linux or macOS)';
    }
    r.addEventListener('change', () => {
      state.target = value;
      scanFolder(folder.input.value.trim());
      signal('rwf-target', { target: value });
    });
    lab.append(r, el('span', '', ` ${label}`)); where.append(lab);
  }
  const choose = el('div', 'rwf-step');
  const folderRow = el('label', 'rwf-row');
  folderRow.append(el('span', '', 'Run folder'), folder.input, folder.btn);
  // Small print sits behind a "?", as in the settings sidebar.
  const dataHelp = helpToggle("Proves the atom order. Replaces the input's read_data file in the copy.",
    { subject: 'the new data file' });
  dataHelp.note.classList.add('rwf-note');
  choose.append(
    folderRow, where, found,
    row('Data file', dataLabel, dataHelp.button),
    dataHelp.note,
    row('Frame from', dataFrom), fileRow, row('Stop on alarm', stopLabel), problem);

  // The scan's answer, and what it leads to.
  const howOpen = new Set();   // "?" notes left open, kept when the lines are redrawn
  let scanned = null;
  let scanAsked = null;
  const selectedJob = () => (scanned?.jobs || []).find((j) => j.path === jobSel.value) || null;
  const base = (p) => String(p || '').split('/').pop();
  function scanFolder(path) {
    scanAsked = path;
    found.replaceChildren(el('div', 'rwf-wait', path ? `Looking in ${path}…` : 'Opening the last run folder…'));
    found.dataset.folder = '';
    scanned = null;
    send(OUT.SCAN_RUN_FOLDER, { ...(path ? { folder: path } : {}), target: state.target });
    check();
  }
  function renderFound() {
    const s = scanned;
    if (!s) return;
    if (s.folder && !folder.input.value) folder.input.value = s.folder;
    const parts = [];
    // How a file was found is small print behind the row's "?"; a line that
    // asks the user to do something (`ask`) stays in view.
    const line = (key, valueEl, how, ask = false) => {
      const r = el('div', 'rwf-found-row');
      r.append(el('span', 'rwf-k', key), valueEl);
      parts.push(r);
      if (!how) return;
      if (ask) {
        parts.push(el('div', 'rwf-how', how));
        return;
      }
      const h = helpToggle(how, { subject: `how the ${key.toLowerCase()} was found`, open: howOpen.has(key),
        onToggle: (open) => (open ? howOpen.add(key) : howOpen.delete(key)) });
      h.note.classList.add('rwf-how');
      r.appendChild(h.button);
      parts.push(h.note);
    };
    if (state.target === 'local') {
      inputSel.replaceChildren(...(s.inputs || []).map((i) => {
        const o = el('option', '', i.name); o.value = i.path; return o;
      }));
      if (s.input) inputSel.value = s.input;
      line('Input', inputSel, (s.inputs || []).length > 1 ? 'Choose the input script' : 'LAMMPS input in this folder',
        (s.inputs || []).length > 1);
      if (s.data) line('Data file', el('span', 'rwf-v', base(s.data)), `read by ${base(s.input)}`);
    }
    const jobs = state.target === 'local' ? [] : s.jobs || [];
    jobSel.replaceChildren(...jobs.map((j) => {
      const o = el('option', '', j.problem ? `${j.name} — ${j.problem}` : `${j.name} — ${j.launch}`);
      o.value = j.path;
      o.disabled = !!j.problem;
      return o;
    }));
    if (s.job) jobSel.value = s.job;
    if (jobs.length) {
      const usable = jobs.filter((j) => !j.problem).length;
      const j = selectedJob();
      line('Job script', jobs.length > 1 ? jobSel : el('span', 'rwf-v', base(s.job) || jobs[0].name),
        usable > 1 ? `${usable} job scripts here; choose one`
          : j ? `the only file here with #SBATCH lines that starts LAMMPS (line ${j.line})` : '',
        usable > 1);
      if (j) {
        line('Input', el('span', 'rwf-v', base(j.input)), `run by ${j.name}: ${j.launch}`);
        if (s.data && j.path === s.job) line('Data file', el('span', 'rwf-v', base(s.data)), `read by ${base(j.input)}`);
      }
    }
    const refs = s.references || [];
    refSel.replaceChildren(...refs.map((r) => {
      const what = [r.name, r.n_atoms ? `${r.n_atoms} atoms` : null, (r.types || []).join(' ') || null]
        .filter(Boolean).join(' · ');
      const why = r.problem || (r.match === false ? 'its elements do not match the input' : r.where);
      const o = el('option', '', `${what} — ${why}`);
      o.value = r.bundle;
      o.title = r.bundle;
      o.disabled = !!r.problem;
      return o;
    }), Object.assign(el('option', '', 'Other… (browse; a .bundle folder is one item)'), { value: '__other__' }));
    if (s.reference) refSel.value = s.reference;
    else refSel.value = '__other__';
    const ref = refs.find((r) => r.bundle === refSel.value);
    if (state.target === 'local' || jobs.length || refs.length) {
      line('Reference', refSel, ref ? ref.bundle : 'no reference found here or made by FFAST; choose one with Other…',
        !ref);
    }
    if (s.problem) parts.push(el('div', 'rwf-refusal-inline', s.problem));
    found.replaceChildren(...parts);
    found.dataset.folder = s.folder || '';   // which folder these lines describe
    check();
  }
  inputSel.addEventListener('change', () => {
    scanAsked = scanned.folder;
    send(OUT.SCAN_RUN_FOLDER, { folder: scanned.folder, input: inputSel.value, target: 'local' });
  });
  jobSel.addEventListener('change', () => {
    // another job script: its input, data file and element order may differ
    scanAsked = scanned.folder;
    send(OUT.SCAN_RUN_FOLDER, { folder: scanned.folder, job: jobSel.value });
  });
  refSel.addEventListener('change', () => {
    if (!scanned) return;   // a scan is on its way; its lines replace these
    if (refSel.value === '__other__') {
      pick('bundle', 'Reference (a .bundle folder)', (p) => {
        const path = p.replace(/\/+$/, '');
        scanned.references = [...(scanned.references || []),
          { bundle: path, name: base(path).replace(/\.bundle$/, ''), where: 'chosen', match: null }];
        scanned.reference = path;
        renderFound();
      });
      return;
    }
    scanned.reference = refSel.value;
    renderFound();
  });
  // Typing drops an answer still on its way (the last folder, asked on opening),
  // so it never overwrites what is being typed.
  folder.input.addEventListener('input', () => { scanAsked = undefined; });
  folder.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); scanFolder(folder.input.value.trim()); }
  });
  folder.input.addEventListener('change', () => {
    const v = folder.input.value.trim();
    if (v && v !== scanAsked && v !== scanned?.folder) scanFolder(v);
  });

  const values = () => {
    const j = selectedJob();
    return {
      target: state.target, input: state.target === 'local' ? inputSel.value : j?.input || '', job: j?.path || '', bundle: scanned && refSel.value !== '__other__' ? refSel.value : '',
      data: dataOn.checked ? dataFrom.value : 'none', dataPath: dataFile.input.value.trim(),
      dataIndex: parseInt(dataIndex.value, 10) || 0, stop: stopOn.checked,
    };
  };
  const request = () => {
    const v = values();
    const kw = { input: v.input, job: v.job, bundle: v.bundle, data: v.data, stop_on_alarm: v.stop, target: state.target, practice };
    if (state.target === 'local' && executable.value.trim()) kw.executable = executable.value.trim();
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
  checkStep.setAttribute('aria-live', 'polite');
  const executable = el('input', 'rwf-path');
  executable.id = 'rwf-executable';
  executable.setAttribute('aria-label', 'LAMMPS path');
  executable.placeholder = '/path/to/lmp';
  const retry = el('button', '', 'Check this path');
  retry.type = 'button';
  retry.addEventListener('click', () => prepare());
  const executableRow = row('LAMMPS path', executable, retry);
  executable.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); prepare(); }
  });
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
  const copiesHelp = helpToggle('Copies are written next to the originals; the originals are never changed.',
    { subject: 'what gets written' });
  foot.append(footLeft, back, cancel, next);

  const state = { step: 0, prepared: null, request: null, jobId: null, via: 'laptop', laptopOk: false,
    watching: false, open: true, target: 'cluster', localRunId: null };

  function show(step) {
    state.step = step;
    modal.dataset.step = String(step);
    stepEls.forEach((s, i) => {
      s.className = i < step ? 'done' : i === step ? 'on' : '';
      if (i === step) s.setAttribute('aria-current', 'step'); else s.removeAttribute('aria-current');
    });
    body.replaceChildren([choose, checkStep, review][step]);
    back.hidden = step === 0;
    next.textContent = ['Check ›', 'Review ›', state.target === 'local' ? 'Write copies and start' : 'Write copies and submit'][step];
    footLeft.replaceChildren(...(step === 2 ? [copiesHelp.button, copiesHelp.note] : []));
    next.disabled = false;
    if (step === 0) check();
    if (step === 1) next.disabled = !(state.prepared && state.prepared.ok);
  }

  function renderCheck(kw) {
    const parts = [];
    if (state.target === 'local') {
      const builds = el('div', 'rwf-sec');
      builds.id = 'rwf-builds';
      builds.append(el('h4', '', 'LAMMPS builds on this computer'));
      const rows = kw.builds || kw.sections?.builds || [];
      if (!rows.length) builds.append(el('p', '', 'No LAMMPS found on PATH. Give its executable path below.'));
      for (const b of rows) {
        const line = el('label', 'rwf-build');
        const r = el('input'); r.type = 'radio'; r.name = 'rwf-build'; r.value = b.executable;
        r.disabled = !b.ok; r.checked = !!b.ok && b.executable === kw.lammps?.executable;
        r.addEventListener('change', () => { executable.value = b.executable; prepare(); });
        line.append(r, el('span', '', `${b.executable}${b.version ? ` (${b.version})` : ''}: ${b.reason}`));
        builds.append(line);
      }
      parts.push(builds, executableRow);
    }
    for (const key of ['module', 'input', 'job']) {
      const items = (kw.sections || {})[key] || [];
      if (!items.length) continue;
      const sec = el('div', 'rwf-sec');
      sec.appendChild(el('h4', '', state.target === 'local' && key === 'module' ? 'LAMMPS commands' : SECTION_TITLES[key]));
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
        signal('rwf-data-tab');
        return;
      }
      diffBox.replaceChildren(...diffLines(c.diff).map((l) => {
        const line = el('div', l.cls, l.text);
        if (l.cls === 'add' && /^\+include /.test(l.text)) line.dataset.rwf = 'include';
        if (l.cls === 'add' && l.text.includes('write_dump')) line.dataset.rwf = 'snapshot';
        if (l.text.includes('shell sleep') || l.text.includes('/GO')) line.dataset.rwf = 'tutorial';
        return line;
      }));
    };
    copies.forEach((c, i) => {
      const t = el('button', 'rwf-tab', copyLabel(c));
      t.type = 'button';
      t.setAttribute('role', 'tab');
      t.addEventListener('click', () => pick(i));
      tabs.appendChild(t);
    });
    if (copies.length) pick(0);
    if (state.target === 'local') {
      submitFrom.replaceChildren(el('span', '', `Start here: ${kw.launch.executable} -in ${kw.launch.input}`));
      return;
    }
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
  /** Send a request and hand its final reply to `then` (ADR 0097); without
   * `askServer` the reply comes back through the page's event handlers. */
  const asking = (event, kw, then) => {
    if (!askServer) { send(event, kw); return; }
    answerOf(askServer(event, kw)).then((reply) => { if (!reply.connectionLost) then(reply); });
  };

  function prepare() {
    state.prepared = null;
    const asked = request();
    state.request = asked;
    checkStep.replaceChildren(el('div', 'rwf-wait', state.target === 'local'
      ? "Checking LAMMPS builds on this computer…"
      : "Checking… (loading the job's LAMMPS module on the server's node can take a minute)"));
    show(1);
    next.disabled = true;
    // Only the latest check counts: an earlier one's answer (Back, then Next
    // again) would carry other originals' checksums into the write.
    asking(OUT.PREPARE_RUN_WITH_FFAST, asked, (kw) => { if (state.request === asked) api.onPrepared(kw); });
  }
  next.addEventListener('click', () => {
    if (state.step === 0) {
      if (!check()) return;
      prepare();
    } else if (state.step === 1) {
      renderReview(state.prepared);
      show(2);
      signal('rwf-review');
    } else if (state.step === 2) {
      next.disabled = true;
      back.disabled = true;
      progress.textContent = 'Writing the copies…';
      asking(OUT.WRITE_RUN_WITH_FFAST, { ...state.request, originals: state.prepared.originals },
        (kw) => { written(kw); api.onWritten(kw); });
    }
  });
  back.addEventListener('click', () => show(Math.max(0, state.step - 1)));
  let untrap = () => {};
  const close = () => {
    state.open = false;
    modal.remove();
    untrap();
    signal('rwf-closed', { watching: state.watching, started: !!state.localRunId });
    if (state.watching) status(`Run with FFAST: following job ${state.jobId}; its tab opens on the first frame`);
  };
  cancel.addEventListener('click', close);
  modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  for (const f of [dataFile.input, dataIndex]) f.addEventListener('input', check);
  dataOn.addEventListener('change', check);
  dataFrom.addEventListener('change', check);

  show(0);
  untrap = trapFocus(modal);
  document.body.appendChild(modal);
  // The last run folder, if there is one. The field waits for that answer (a
  // moment; at most 5 s), so the answer never lands in the middle of typing.
  folder.input.readOnly = true;
  folder.input.placeholder = 'opening the last run folder…';
  const unlock = () => {
    if (!folder.input.readOnly) return;
    folder.input.readOnly = false;
    folder.input.placeholder = 'the folder that holds your job script';
    folder.input.focus();
  };
  setTimeout(unlock, 5000);
  scanFolder(initialFolder);
  signal('rwf-open');

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

  const api = {
    /** RUN_FOLDER_SCANNED: what the folder holds; an answer to an older question is dropped. */
    onScanned(kw) {
      if (!state.open || (kw.asked || '') !== (scanAsked || '') || (kw.target || 'cluster') !== state.target) return;
      scanned = kw;
      if (initialReference && kw.folder === initialFolder) scanned.reference = initialReference;
      if (kw.folder) folder.input.value = kw.folder;
      renderFound();
      unlock();
      signal('rwf-chosen', { folder: kw.folder });
    },
    /** RUN_WITH_FFAST_PREPARED */
    onPrepared(kw) {
      if (!state.open || state.step !== 1) return;
      state.prepared = kw;
      if (state.target === 'local' && kw.ok) state.request.executable = kw.lammps.executable;
      renderCheck(kw);
      next.disabled = !kw.ok;
      signal('rwf-checked', { ok: !!kw.ok });
    },
    /** RUN_WITH_FFAST_WRITTEN: then submit from the chosen machine. */
    async onWritten(kw) {
      if (!state.open) return;
      if (!kw.ok) { failed((kw.refusal && kw.refusal.text) || 'The copies were not written.'); return; }
      if (kw.launch) {
        state.localRunId = kw.run_id;
        progress.textContent = 'LAMMPS started; waiting for its first frame…';
        cancel.textContent = 'Close (keep following)';
        openRun(kw.run_dir);
        return;
      }
      progress.textContent = `Copies written: ${(kw.written || []).join(', ')}. Submitting…`;
      const { folder, script } = kw.submit;
      if (state.via === 'laptop') {
        let out;
        try { out = await submitFromLaptop(folder, script); } catch (e) { out = { error: e.message }; }
        if (out.error) failed(`Submitting from this computer failed: ${out.error}`);
        else submitted(out.job_id);
      } else {
        asking(OUT.SUBMIT_JOB, { folder, script }, (kw) => api.onSubmitted(kw));
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
    onLocalUpdate(kw) {
      if (kw.run_id !== state.localRunId || !state.open) return;
      const error = kw.error || (kw.events || []).find((e) => ['refused', 'scorer_failed'].includes(e.kind))?.reason;
      if (error || kw.end && !kw.fingerprint) failed(error || `LAMMPS ${kw.end.state} before its first frame.`);
    },
    onLocalFrame(kw) {
      if (kw.run_id === state.localRunId && state.open) close();
    },
    close,
    get prepared() { return state.prepared; },
    get step() { return state.step; },
    get target() { return state.target; },
    get folder() { return scanned?.folder; },
    get jobId() { return state.jobId; },
  };
  return api;
}
