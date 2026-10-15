import {arena} from './pages.js';

// Patch a mounted fragment, retaining its DOM nodes, focus and event targets.
function syncElement(current, next) {
  for (const attr of [...current.attributes]) {
    if (!next.hasAttribute(attr.name)) current.removeAttribute(attr.name);
  }
  for (const attr of next.attributes) {
    if (current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
  }
  [...next.childNodes].forEach((wanted, index) => {
    const existing = current.childNodes[index];
    if (!existing) current.append(wanted.cloneNode(true));
    else if (existing.nodeType !== wanted.nodeType || existing.nodeName !== wanted.nodeName) {
      existing.replaceWith(wanted.cloneNode(true));
    } else if (wanted.nodeType === 1) syncElement(existing, wanted);
    else if (existing.nodeValue !== wanted.nodeValue) existing.nodeValue = wanted.nodeValue;
  });
  while (current.childNodes.length > next.childNodes.length) current.lastChild.remove();
}

// A snapshot changes only the affected squares. Coordinates remain stable even
// when the viewer flips the board; the board and its 81 squares are never rebuilt.
function syncBoard(current, next) {
  const squares = new Map([...current.children].map(square => [square.dataset.square, square]));
  [...next.children].forEach((wanted, index) => {
    const square = squares.get(wanted.dataset.square);
    if (!square) return;
    syncElement(square, wanted);
    if (current.children[index] !== square) current.insertBefore(square, current.children[index] || null);
  });
}

export function updateMatchView(state) {
  if (!['arena', 'replay'].includes(state.route)) return;
  const mounted = document.querySelector('#arena-board .board');
  if (!mounted) return;
  // Render the desired match snapshot off-document, then patch dynamic regions.
  // The application shell, page layout and other match cards stay untouched.
  const template = document.createElement('template');
  template.innerHTML = arena(state, state.route === 'replay');
  const next = template.content;
  const history = document.querySelector('#history-list');
  const historyTop = history?.scrollTop || 0;
  const following = !history || history.scrollHeight - history.clientHeight - historyTop <= 24;
  const focused = document.activeElement;

  syncBoard(mounted, next.querySelector('#arena-board .board'));
  for (const selector of [
    '.arena-heading h1 > .badge', '.players', '.board-meta > strong',
    '.demo-control', '.replay-controls', '.match-info-grid .status-text',
    '#current-turn', '#latest-move', '.history-tabs', '#history-content', '.technical-notice'
  ]) {
    const current = document.querySelector(selector), wanted = next.querySelector(selector);
    if (current && wanted) syncElement(current, wanted);
  }
  const range = document.querySelector('#replay-range');
  if (range && range.value !== String(state.replayStep)) range.value = String(state.replayStep);
  const updatedHistory = document.querySelector('#history-list');
  if (updatedHistory) {
    updatedHistory.scrollTop = following ? updatedHistory.scrollHeight : historyTop;
  }
  // Moving existing squares for a flipped view can blur them in some browsers.
  if (focused?.isConnected && document.activeElement !== focused) focused.focus({preventScroll: true});
}
