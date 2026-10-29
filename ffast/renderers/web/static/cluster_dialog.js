/**
 * The cluster dialog (web parity plan 2026-10-29): profiles here, the SSH
 * work in the launcher (cluster_client.js).
 */
import { CLUSTER_FIELDS, emptyProfile, formValues, profileFromForm } from './cluster_client.js';
import { askDialog, trapFocus } from './dialogs.js';

const $ = (id) => document.getElementById(id);
const SETUP = 'Server setup';

export class ClusterDialog {
  /** @param {{client: import('./cluster_client.js').ClusterClient}} deps */
  constructor(deps) {
    this._client = deps.client;
    this._state = { profiles: [], records: [], status: {} };
    this._untrap = null;
    this._buildForm();
    $('cluster-profile').addEventListener('change', () => this._fillSelected());
    $('cluster-save-btn').addEventListener('click', () => this._save());
    $('cluster-delete-btn').addEventListener('click', () => this._delete());
    $('cluster-dialog').addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.close();
    });
  }

  /** Show the dialog with the launcher's saved profiles. */
  async open() {
    $('cluster-dialog').classList.remove('hidden');
    this._untrap ||= trapFocus($('cluster-dialog'));
    this._message('');
    try {
      await this.refresh();
    } catch (err) {
      this._message(err.message);
    }
    $('cluster-profile').focus();
  }

  close() {
    this._untrap?.();
    this._untrap = null;
    $('cluster-dialog').classList.add('hidden');
  }

  /** Ask the launcher for its state and redraw the profile list and form. */
  async refresh() {
    const s = await this._client.state();
    this._state = s;
    const select = $('cluster-profile');
    const keep = select.value;
    select.replaceChildren(this._option('', 'New profile'),
      ...s.profiles.map((p) => this._option(p.name, p.name)));
    select.value = s.profiles.some((p) => p.name === keep) ? keep : '';
    this._fillSelected();
  }

  _option(value, label) {
    const o = document.createElement('option');
    o.value = value;
    o.textContent = label;
    return o;
  }

  /** The fields, once: one box per group, the server-setup group folded. */
  _buildForm() {
    const form = $('cluster-form');
    const groups = [...new Set(CLUSTER_FIELDS.map((f) => f.group))];
    for (const group of groups) {
      let box;
      let grid;
      if (group === SETUP) {
        box = document.createElement('details');
        box.id = 'cl-server-setup';
        const summary = document.createElement('summary');
        summary.textContent = SETUP;
        grid = document.createElement('div');
        grid.className = 'cl-grid';
        box.append(summary, grid);
      } else {
        box = document.createElement('fieldset');
        const legend = document.createElement('legend');
        legend.textContent = group;
        box.appendChild(legend);
        grid = box;
      }
      for (const f of CLUSTER_FIELDS.filter((x) => x.group === group)) grid.append(...this._field(f));
      form.appendChild(box);
    }
  }

  _field(f) {
    const label = document.createElement('label');
    label.htmlFor = `cl-${f.key}`;
    label.textContent = f.label;
    const input = document.createElement('input');
    input.id = `cl-${f.key}`;
    input.type = f.type === 'bool' ? 'checkbox' : 'text';
    if (f.type === 'int') input.inputMode = 'numeric';
    if (f.placeholder) input.placeholder = f.placeholder;
    const out = [label, input];
    if (f.help) {
      const help = document.createElement('small');
      help.className = 'cl-help';
      help.id = `cl-${f.key}-help`;
      help.textContent = f.help;
      input.setAttribute('aria-describedby', help.id);
      out.push(help);
    }
    return out;
  }

  _readForm() {
    const values = {};
    for (const f of CLUSTER_FIELDS) {
      const input = $(`cl-${f.key}`);
      values[f.key] = f.type === 'bool' ? input.checked : input.value;
    }
    return profileFromForm(values);
  }

  _fillForm(profile) {
    const values = formValues(profile);
    for (const f of CLUSTER_FIELDS) {
      const input = $(`cl-${f.key}`);
      if (f.type === 'bool') input.checked = values[f.key];
      else input.value = values[f.key];
    }
  }

  /** Fill the form from the chosen profile ("New profile": the defaults). */
  _fillSelected() {
    const name = $('cluster-profile').value;
    const profile = this._state.profiles.find((p) => p.name === name);
    this._fillForm(profile || emptyProfile());
    $('cluster-delete-btn').disabled = !profile;
  }

  _message(text) {
    const el = $('cluster-message');
    el.textContent = text;
    el.classList.toggle('hidden', !text);
  }

  async _save() {
    try {
      const profile = this._readForm();
      await this._client.saveProfile(profile);
      await this.refresh();
      $('cluster-profile').value = profile.name;
      this._fillSelected();
      this._message('');
    } catch (err) {
      this._message(err.message);
    }
  }

  async _delete() {
    const name = $('cluster-profile').value;
    if (!name) return;
    const answer = await askDialog({
      title: 'Delete profile', message: `Delete profile "${name}"?`,
      buttons: ['Cancel', 'Delete'], danger: 'Delete',
    });
    if (answer !== 'Delete') return;
    try {
      await this._client.deleteProfile(name);
      $('cluster-profile').value = '';
      await this.refresh();
      this._message('');
    } catch (err) {
      this._message(err.message);
    }
  }
}
