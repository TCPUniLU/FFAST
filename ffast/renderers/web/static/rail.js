/**
 * The object rail (ADR 0055): the datasets and predictions the server holds,
 * one row each, a frozen subset tagged so. Clicking a row picks it for the
 * main view; its ⋯ opens the row's menu (Delete…, or Freeze for a live
 * subset). What either does is the app's (the ports); the rail only draws
 * the lists.
 */

import { sharedMenu } from './actions.js';
import { icon } from './icons.js';

/**
 * @typedef {{
 *   datasetList: HTMLElement, modelList: HTMLElement,
 *   onSelectDataset: (fp: string) => void, onSelectModel: (fp: string) => void,
 *   rowActions: (kind: 'dataset'|'model', fp: string) => import('./actions.js').Action[],
 * }} RailPorts
 */

export class Rail {
  /** @param {RailPorts} ports */
  constructor(ports) {
    this._ports = ports;
    const list = document.createElement('div');
    list.className = 'menu-list rail-menu hidden';
    list.setAttribute('role', 'menu');
    document.body.appendChild(list);
    this._menu = sharedMenu(list);
  }

  /** The ⋯ button that opens a row's menu, without picking the row. */
  _menuButton(kind, fp) {
    const btn = document.createElement('button');
    btn.className = 'obj-menu-btn';
    btn.innerHTML = icon('more');
    btn.title = 'More';
    btn.setAttribute('aria-label', 'More');
    btn.setAttribute('aria-haspopup', 'menu');
    btn.setAttribute('aria-expanded', 'false');
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      this._menu.open(btn, this._ports.rowActions(kind, fp));
    });
    return btn;
  }

  /**
   * Draw both lists.
   * @param {{datasets: Map<string, object>, models: Map<string, object>,
   *   datasetFp: string|null, modelFp: string|null,
   *   applies: (meta: object) => boolean}} state — `datasets` are the listed
   *   ones; `applies` says whether a prediction applies to the selected dataset.
   */
  render(state) {
    this._menu.close();   // its row is about to be redrawn
    this._renderDatasets(state);
    this._renderModels(state);
  }

  _renderDatasets({ datasets, datasetFp }) {
    const list = this._ports.datasetList;
    list.innerHTML = '';
    if (datasets.size === 0) {
      list.innerHTML = '<div class="obj-empty">None loaded</div>';
      return;
    }
    for (const [fp, meta] of datasets) {
      const row = document.createElement('div');
      row.className = 'obj-row' + (fp === datasetFp ? ' selected' : '');
      row.dataset.fp = fp;
      row.innerHTML =
        `<span class="name">${meta.name || fp.slice(0,8)}</span>` +
        `<span class="meta">${meta.frozen ? 'frozen · ' : ''}${framesText(meta.n)}</span>`;
      row.appendChild(this._menuButton('dataset', fp));
      row.addEventListener('click', () => this._ports.onSelectDataset(fp));
      makeRowKeyboardOperable(row, fp === datasetFp);
      list.appendChild(row);
    }
  }

  _renderModels({ models, modelFp, applies }) {
    const list = this._ports.modelList;
    list.innerHTML = '';
    if (models.size === 0) {
      list.innerHTML = '<div class="obj-empty">None loaded</div>';
      return;
    }
    for (const [fp, meta] of models) {
      // A prediction applies to the dataset it was computed for and its subsets.
      const ok = applies(meta);
      const row = document.createElement('div');
      row.className = 'obj-row'
        + (fp === modelFp ? ' selected' : '')
        + (ok ? '' : ' disabled');
      row.dataset.fp = fp;
      row.innerHTML = `<span class="name">${meta.name || fp.slice(0,8)}</span>`;
      row.appendChild(this._menuButton('model', fp));
      if (ok) row.addEventListener('click', () => this._ports.onSelectModel(fp));
      makeRowKeyboardOperable(row, fp === modelFp, !ok);
      list.appendChild(row);
    }
  }
}

/**
 * A rail row acts like a button for the keyboard (ADR 0063): a tab stop that
 * Enter or Space presses. The row's own ⋯ button keeps its own tab stop.
 */
function makeRowKeyboardOperable(row, selected, disabled = false) {
  row.setAttribute('role', 'button');
  row.setAttribute('aria-pressed', String(selected));
  if (disabled) {
    row.setAttribute('aria-disabled', 'true');
    return;
  }
  row.tabIndex = 0;
  row.addEventListener('keydown', (e) => {
    if (e.target !== row || (e.key !== 'Enter' && e.key !== ' ')) return;
    e.preventDefault();
    row.click();
  });
}

/** "100 frames", or "1 frame". */
export function framesText(n) {
  return `${n} frame${n === 1 ? '' : 's'}`;
}
