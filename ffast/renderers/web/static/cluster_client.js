/**
 * The page's side of cluster connections (web parity plan 2026-10-29).
 * The ffast-web launcher does the SSH and SLURM work; the page asks it with
 * JSON requests under /cluster/, carrying the launch id from its own URL.
 */

const SETUP = 'Server setup';

/** The profile form, in order: the desktop's fields plus the server-setup
 * ones the desktop form hides (shown folded, each with a line of help). */
export const CLUSTER_FIELDS = [
  { key: 'name', label: 'Profile name', group: 'Profile', type: 'text' },
  { key: 'host', label: 'Host', group: 'SSH', type: 'text', placeholder: 'login.example.com' },
  { key: 'username', label: 'Username', group: 'SSH', type: 'text', placeholder: 'your_username' },
  { key: 'identity_file', label: 'Identity file', group: 'SSH', type: 'text',
    placeholder: '~/.ssh/id_ed25519 (blank: ssh-agent / default)' },
  { key: 'ffast_server_cmd', label: 'Server command', group: 'SSH', type: 'text',
    placeholder: 'ffast-server' },
  { key: 'partition', label: 'Partition', group: 'Scheduler', type: 'text' },
  { key: 'account', label: 'Account', group: 'Scheduler', type: 'text' },
  { key: 'qos', label: 'QoS', group: 'Scheduler', type: 'text' },
  { key: 'job_name', label: 'Job name', group: 'Scheduler', type: 'text', placeholder: 'ffast' },
  { key: 'cores', label: 'Cores', group: 'Resources', type: 'int' },
  { key: 'cpus_per_task', label: 'CPUs/task', group: 'Resources', type: 'int' },
  { key: 'ntasks_per_node', label: 'Ntasks/node', group: 'Resources', type: 'int' },
  { key: 'gpus_per_task', label: 'GPUs/task', group: 'Resources', type: 'int' },
  { key: 'memory_mb', label: 'Memory (MB)', group: 'Resources', type: 'int' },
  { key: 'time_limit', label: 'Time limit', group: 'Resources', type: 'text', placeholder: 'HH:MM:SS' },
  { key: 'provision', label: 'Install FFAST in the job automatically', group: SETUP, type: 'bool',
    help: 'Builds FFAST here and installs it in the job; the Server command above is then not used.' },
  { key: 'modules', label: 'Modules to load', group: SETUP, type: 'list', placeholder: 'Python CUDA',
    help: 'Names for "module load", space-separated; used when installing.' },
  { key: 'venv_path', label: 'Environment folder', group: SETUP, type: 'text',
    help: "Folder on the cluster for FFAST's Python environment; used when installing." },
  { key: 'snapshot_interval_minutes', label: 'Auto-save every (minutes)', group: SETUP, type: 'int',
    help: 'The server saves its session this often; 0 = never.' },
];

/** A fresh profile with the server's defaults (cluster/config.py ClusterProfile),
 * so "New profile" shows them as values, not only as placeholders. */
export function emptyProfile() {
  return {
    name: '', host: '', username: '', identity_file: '', ffast_server_cmd: 'ffast-server',
    partition: '', account: '', qos: '', job_name: 'ffast', cores: 1, cpus_per_task: 0,
    ntasks_per_node: 0, gpus_per_task: 0, memory_mb: 4096, time_limit: '01:00:00',
    snapshot_interval_minutes: 5, provision: false, modules: [], venv_path: '~/.ffast/venv',
  };
}

/**
 * The profile the form values make. `values` maps key → raw input (a string,
 * or a boolean for `bool`). The server checks again; this only tidies the request.
 */
export function profileFromForm(values) {
  const base = emptyProfile();
  const out = {};
  for (const f of CLUSTER_FIELDS) {
    if (!(f.key in values)) continue;
    const v = values[f.key];
    if (f.type === 'int') {
      const n = parseInt(v, 10);
      out[f.key] = Number.isNaN(n) ? base[f.key] : n;
    } else if (f.type === 'bool') {
      out[f.key] = !!v;
    } else if (f.type === 'list') {
      out[f.key] = String(v ?? '').split(/[\s,]+/).filter(Boolean);
    } else {
      out[f.key] = String(v ?? '').trim();
    }
  }
  return out;
}

/** The reverse, for filling the form: lists joined with a space, numbers as strings. */
export function formValues(profile) {
  const out = {};
  for (const f of CLUSTER_FIELDS) {
    const v = profile[f.key];
    if (f.type === 'list') out[f.key] = (v || []).join(' ');
    else if (f.type === 'bool') out[f.key] = !!v;
    else out[f.key] = v === undefined || v === null ? '' : String(v);
  }
  return out;
}

/** The last Session Record of `profileName`, or null (the desktop's latest_session_record). */
export function latestRecord(records, profileName) {
  const mine = (records || []).filter((r) => r.profile_name === profileName);
  return mine.length ? mine[mine.length - 1] : null;
}

/** The launch id in the page's query string, or null (not opened by ffast-web). */
export function launchId(search) {
  return new URLSearchParams(search).get('launch');
}

/** Asks the launcher's cluster broker. */
export class ClusterClient {
  constructor(launch, fetchFn = (...a) => fetch(...a)) {
    this._launch = launch;
    this._fetch = fetchFn;
  }

  /** GET /cluster/state; rejects with the server's error text. */
  state() { return this._ask('GET', '/cluster/state'); }
  /** Ask SLURM which recorded jobs still exist; the launcher forgets the rest. */
  checkRecords() { return this._ask('POST', '/cluster/records/check', {}); }
  saveProfile(profile) { return this._ask('POST', '/cluster/profile', { profile }); }
  deleteProfile(name) { return this._ask('POST', '/cluster/profile/delete', { name }); }
  connect(profile, reconnectJobId = null) {
    return this._ask('POST', '/cluster/connect', { profile, reconnect_job_id: reconnectJobId });
  }
  cancel() { return this._ask('POST', '/cluster/cancel', {}); }
  stop() { return this._ask('POST', '/cluster/stop', {}); }

  /** The custom header is what keeps other web sites out; a JSON body is
   * same-origin here, so it needs no preflight. */
  async _ask(method, path, body) {
    const r = await this._fetch(path, {
      method,
      headers: { 'X-FFAST-Launch': this._launch, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(data.error || `HTTP ${r.status}`), { status: r.status });
    return data;
  }
}
