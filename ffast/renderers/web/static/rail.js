/**
 * The object rail (ADR 0055): the datasets and predictions the server holds,
 * one row each. Clicking a row picks it for the main view; what that does is
 * the app's (the `onSelect…` ports), the rail only draws the lists.
 */

/**
 * @typedef {{
 *   datasetList: HTMLElement, modelList: HTMLElement,
 *   onSelectDataset: (fp: string) => void, onSelectModel: (fp: string) => void,
 * }} RailPorts
 */

export class Rail {
  /** @param {RailPorts} ports */
  constructor(ports) {
    this._ports = ports;
  }

  /**
   * Draw both lists.
   * @param {{datasets: Map<string, object>, models: Map<string, object>,
   *   datasetFp: string|null, modelFp: string|null,
   *   applies: (meta: object) => boolean}} state — `datasets` are the listed
   *   ones; `applies` says whether a prediction applies to the selected dataset.
   */
  render(state) {
    this._renderDatasets(state);
    this._renderModels(state);
  }

  _renderDatasets({ datasets, datasetFp }) {
    const list = this._ports.datasetList;
    list.innerHTML = '';
    if (datasets.size === 0) {
      list.innerHTML = '<div class="obj-empty">— none loaded —</div>';
      return;
    }
    for (const [fp, meta] of datasets) {
      const row = document.createElement('div');
      row.className = 'obj-row' + (fp === datasetFp ? ' selected' : '');
      row.dataset.fp = fp;
      row.innerHTML =
        `<span class="name">${meta.name || fp.slice(0,8)}</span>` +
        `<span class="meta">${meta.n} fr</span>`;
      row.addEventListener('click', () => this._ports.onSelectDataset(fp));
      list.appendChild(row);
    }
  }

  _renderModels({ models, modelFp, applies }) {
    const list = this._ports.modelList;
    list.innerHTML = '';
    if (models.size === 0) {
      list.innerHTML = '<div class="obj-empty">— none —</div>';
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
      if (ok) row.addEventListener('click', () => this._ports.onSelectModel(fp));
      list.appendChild(row);
    }
  }
}
