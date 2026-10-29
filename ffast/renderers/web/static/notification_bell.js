/**
 * The bell in the bottom-right corner: its badge counts the warnings and
 * errors not yet seen (red when one is an error), and a click lists them all,
 * newest first (notifications.js keeps the list).
 */
import { icon } from './icons.js';
import { loadNotices, markRead, saveNotices, unreadSummary } from './notifications.js';

const $ = (id) => document.getElementById(id);

function clock(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function isOpen() {
  return !$('notif-panel').hidden;
}

function drawBadge(list) {
  const { count, level } = unreadSummary(list);
  const badge = $('notif-bell').querySelector('.notif-badge');
  badge.hidden = !count;
  badge.textContent = count > 99 ? '99+' : String(count);
  badge.className = `notif-badge notif-${level || 'warning'}`;
  $('notif-bell').setAttribute('aria-label',
    count ? `Warnings and errors, ${count} not seen` : 'Warnings and errors');
}

function drawList(list) {
  const box = $('notif-list');
  if (!list.length) {
    const empty = document.createElement('p');
    empty.className = 'notif-empty';
    empty.textContent = 'No warnings or errors';
    box.replaceChildren(empty);
    return;
  }
  box.replaceChildren(...list.map((n) => {
    const item = document.createElement('li');
    item.className = `notif-item notif-${n.level}`;
    const mark = document.createElement('span');
    mark.className = 'notif-mark';
    mark.textContent = '⚠';
    mark.setAttribute('aria-hidden', 'true');
    const body = document.createElement('div');
    const text = document.createElement('div');
    text.className = 'notif-text';
    text.textContent = n.text;
    const meta = document.createElement('div');
    meta.className = 'notif-meta';
    meta.textContent = n.count > 1 ? `${clock(n.time)} · ×${n.count}` : clock(n.time);
    body.append(text, meta);
    item.append(mark, body);
    return item;
  }));
}

function draw(list) {
  // While the list is open, what arrives is seen at once.
  if (isOpen() && unreadSummary(list).count) {
    saveNotices(markRead(list));   // draws again through the event
    return;
  }
  drawBadge(list);
  if (isOpen()) drawList(list);
}

function open() {
  $('notif-panel').hidden = false;
  $('notif-bell').setAttribute('aria-expanded', 'true');
  draw(loadNotices());
}

function close() {
  $('notif-panel').hidden = true;
  $('notif-bell').setAttribute('aria-expanded', 'false');
}

/** Put the bell and its list on the page. Called once, at app setup. */
export function initBell() {
  if ($('notif-bell')) return;
  const bell = document.createElement('button');
  bell.id = 'notif-bell';
  bell.type = 'button';
  bell.setAttribute('aria-expanded', 'false');
  bell.setAttribute('aria-controls', 'notif-panel');
  bell.innerHTML = `${icon('bell')}<span class="notif-badge" hidden></span>`;

  const panel = document.createElement('div');
  panel.id = 'notif-panel';
  panel.hidden = true;
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Warnings and errors');
  const head = document.createElement('div');
  head.className = 'notif-head';
  const title = document.createElement('span');
  title.textContent = 'Warnings and errors';
  const clear = document.createElement('button');
  clear.id = 'notif-clear';
  clear.type = 'button';
  clear.textContent = 'Clear';
  head.append(title, clear);
  const list = document.createElement('ol');
  list.id = 'notif-list';
  panel.append(head, list);
  document.body.append(panel, bell);

  bell.addEventListener('click', () => (isOpen() ? close() : open()));
  clear.addEventListener('click', () => saveNotices([]));
  panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') { close(); bell.focus(); } });
  bell.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  document.addEventListener('pointerdown', (e) => {
    if (isOpen() && !panel.contains(e.target) && !bell.contains(e.target)) close();
  });
  window.addEventListener('ffast-notices', (e) => draw(e.detail));
  draw(loadNotices());
}
