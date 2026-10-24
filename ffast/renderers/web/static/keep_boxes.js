/**
 * The keep boxes (ADR 0065): four checkboxes that say which axes of a metric
 * stay separate, Combine for x/y/z, and the Statistic. Drawn under the
 * selected metric in the Metrics pane and in the 3D panel's Color by.
 *
 * The widget only draws a setting and reports a click as the setting it
 * gives; the server sends the settings allowed and writes the formula
 * (REQUEST_METRIC_BOXES). The decisions live in metric_logic.js.
 */

import {
  COMBINE_CHOICES, KEEP_BOXES, STATISTIC_CHOICES, boxAvailable, boxHint, chooseSetting,
  combineChoices, statisticChoices, toggleBox,
} from './metric_logic.js';

/** @param {string} tag @param {string} [cls] @param {string} [text] */
function el(tag, cls = '', text = '') {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

let counter = 0;

/**
 * @param {(setting: {keep: string[], combine: string|null, statistic: string|null}) => void} onChoose
 * @returns {{el: HTMLElement, render: (setting: object, allowed: object[], hints: object) => void}}
 */
export function createKeepBoxes(onChoose) {
  const name = `kb${++counter}`;
  const root = el('div', 'kb');
  const keepRow = el('div', 'kb-row');
  keepRow.append(el('span', 'kb-label', 'Keep'));
  const keepInputs = {};
  for (const [box, words] of KEEP_BOXES) {
    const label = el('label', 'kb-choice');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.dataset.box = box;
    cb.setAttribute('aria-label', `Keep ${words}`);
    label.append(cb, document.createTextNode(` ${words}`));
    keepRow.appendChild(label);
    keepInputs[box] = { cb, label };
  }
  const combineRow = el('div', 'kb-row');
  combineRow.append(el('span', 'kb-label', 'Combine'));
  const combineInputs = {};
  for (const [value, words] of COMBINE_CHOICES) {
    const label = el('label', 'kb-choice');
    const radio = el('input');
    radio.type = 'radio';
    radio.name = `${name}-combine`;
    radio.value = value;
    label.append(radio, document.createTextNode(` ${words}`));
    combineRow.appendChild(label);
    combineInputs[value] = { radio, label };
  }
  const statRow = el('div', 'kb-row');
  const statSelect = el('select');
  statSelect.setAttribute('aria-label', 'Statistic');
  for (const [value, words] of STATISTIC_CHOICES) {
    const o = el('option', '', words);
    o.value = value;
    statSelect.appendChild(o);
  }
  statRow.append(el('span', 'kb-label', 'Statistic'), statSelect);
  root.append(keepRow, combineRow, statRow);

  let current = null;
  let allowed = [];
  for (const [box, { cb }] of Object.entries(keepInputs)) {
    cb.addEventListener('change', () => {
      const next = toggleBox(current, box, allowed);
      if (next) onChoose(next); else cb.checked = !cb.checked;
    });
  }
  for (const [value, { radio }] of Object.entries(combineInputs)) {
    radio.addEventListener('change', () => {
      const next = chooseSetting(current, 'combine', value, allowed);
      if (next) onChoose(next);
    });
  }
  statSelect.addEventListener('change', () => {
    const next = chooseSetting(current, 'statistic', statSelect.value, allowed);
    if (next) onChoose(next);
  });

  return {
    el: root,
    /** Draw `setting` among the `allowed` ones; `hints` say why a box is greyed. */
    render(setting, allowedSettings, hints = {}) {
      current = setting;
      allowed = allowedSettings;
      for (const [box, { cb, label }] of Object.entries(keepInputs)) {
        cb.checked = setting.keep.includes(box);
        const ok = boxAvailable(setting, box, allowed);
        cb.disabled = !ok;
        label.classList.toggle('off', !ok);
        label.title = ok ? '' : boxHint(setting, box, hints);
      }
      const combines = combineChoices(setting, allowed);
      combineRow.classList.toggle('off', !combines.length);
      for (const [value, { radio, label }] of Object.entries(combineInputs)) {
        radio.checked = setting.combine === value;
        radio.disabled = !combines.includes(value);
        label.classList.toggle('off', radio.disabled);
      }
      combineRow.title = combines.length ? '' : 'x/y/z is kept, or this metric has none';
      const stats = statisticChoices(setting, allowed);
      statSelect.disabled = !stats.length;
      statRow.classList.toggle('off', !stats.length);
      statRow.title = stats.length ? '' : 'nothing is averaged: each value comes from one number';
      for (const o of statSelect.options) o.disabled = !stats.includes(o.value);
      statSelect.value = setting.statistic || 'mae';
    },
  };
}
