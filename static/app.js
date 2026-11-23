/* ==========================================================================
   1. Dark Mode Toggle (dark-mode-toggle)
   ========================================================================== */

const themeMeta = document.querySelector('meta[name="color-scheme"]');
const themeToggle = document.getElementById('theme-toggle');
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');

function getStoredScheme() {
  try {
    return localStorage.getItem('color-scheme');
  } catch {
    return null;
  }
}

function getSystemScheme() {
  return darkQuery.matches ? 'dark' : 'light';
}

function applyScheme(stored) {
  themeMeta.content = stored ?? 'light dark';
}

themeToggle.addEventListener('click', () => {
  const currentRendered = getStoredScheme() ?? getSystemScheme();
  const target = currentRendered === 'dark' ? 'light' : 'dark';
  const nextStored = target === getSystemScheme() ? null : target;
  try {
    if (nextStored === null) localStorage.removeItem('color-scheme');
    else localStorage.setItem('color-scheme', nextStored);
  } catch {}
  applyScheme(nextStored);
});

window.addEventListener('storage', (event) => {
  if (event.key === 'color-scheme') {
    applyScheme(event.newValue);
  }
});

/* ==========================================================================
   2. Dialog Fallbacks & Top-Layer Exit Animation (dialog + animate-to-from-top-layer)
   ========================================================================== */

let supportsDisplayTransition;
function canTransitionDisplay() {
  if (supportsDisplayTransition !== undefined) return supportsDisplayTransition;
  if (!window.CSS?.supports?.('transition-behavior', 'allow-discrete') || !document.body) {
    return false;
  }
  const probe = document.createElement('div');
  probe.style.cssText = 'transition: display 1s allow-discrete !important; display: block;';
  document.body.appendChild(probe);
  getComputedStyle(probe).display;
  probe.style.display = 'none';
  supportsDisplayTransition = getComputedStyle(probe).display === 'block';
  probe.remove();
  return supportsDisplayTransition;
}

function supportsTopLayerExit() {
  return Boolean(window.CSS?.supports?.('overlay', 'auto') && canTransitionDisplay());
}

async function closeTopLayer(dialog) {
  if (!dialog.open) return;
  if (!supportsTopLayerExit()) {
    dialog.setAttribute('data-closing', '');
    const animations = dialog.getAnimations({ subtree: true });
    if (animations.length > 0) {
      await Promise.race([
        Promise.allSettled(animations.map((a) => a.finished)),
        new Promise((r) => setTimeout(r, 1500)),
      ]);
    }
    if (!dialog.hasAttribute('data-closing')) return;
    dialog.removeAttribute('data-closing');
  }
  dialog.close();
}

// Invoker Commands fallback where command/commandfor isn't natively supported
if (!('commandForElement' in HTMLButtonElement.prototype)) {
  document.addEventListener('click', (event) => {
    const btn = event.target.closest('button[command][commandfor]');
    if (!btn) return;
    const target = document.getElementById(btn.getAttribute('commandfor'));
    if (!target || target.tagName !== 'DIALOG') return;
    const cmd = btn.getAttribute('command');
    if (cmd === 'show-modal' && !target.open) target.showModal();
    else if (cmd === 'close') closeTopLayer(target);
  });
}

// closedby="any" light-dismiss fallback for browsers without HTMLDialogElement.prototype.closedBy
if (!('closedBy' in HTMLDialogElement.prototype)) {
  const isOutside = (e, dialog) => {
    const r = dialog.getBoundingClientRect();
    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
  };
  for (const dialog of document.querySelectorAll('dialog[closedby="any"]')) {
    let pressedOutside = false;
    dialog.addEventListener('pointerdown', (e) => {
      pressedOutside = isOutside(e, dialog);
    });
    dialog.addEventListener('pointerup', (e) => {
      if (pressedOutside && isOutside(e, dialog)) closeTopLayer(dialog);
    });
  }
}

for (const dialog of document.querySelectorAll('dialog')) {
  dialog.addEventListener('cancel', (event) => {
    if (!supportsTopLayerExit()) {
      event.preventDefault();
      closeTopLayer(dialog);
    }
  });
  dialog.addEventListener('close', () => {
    dialog.returnValue = '';
  });
}

/* ==========================================================================
   3. Accessible Form Validation Sync (validate-input-after-interaction + accessible-error-announcement)
   ========================================================================== */

const linkError = (el, shown) => {
  const errorId = el.dataset.error;
  if (!errorId) return;
  const ids = (el.getAttribute('aria-describedby') || '')
    .split(/\s+/)
    .filter((id) => id && id !== errorId);
  if (shown) ids.push(errorId);
  if (ids.length) el.setAttribute('aria-describedby', ids.join(' '));
  else el.removeAttribute('aria-describedby');
};

const announcer = document.createElement('div');
announcer.setAttribute('aria-live', 'polite');
announcer.classList.add('visually-hidden');
document.body.append(announcer);
let announceTimer;
let announcedField;

const spokenText = (node) => {
  const copy = node.cloneNode(true);
  copy.querySelectorAll('[aria-hidden="true"]').forEach((n) => n.remove());
  copy
    .querySelectorAll('[role="img"][aria-label]')
    .forEach((n) => n.replaceWith(`${n.getAttribute('aria-label')}: `));
  return copy.textContent.replace(/\s+/g, ' ').trim();
};

const announceError = (el) => {
  const error = document.getElementById(el.dataset.error);
  if (!error) return;
  const label = el.labels?.[0] ? `${spokenText(el.labels[0])}: ` : '';
  clearTimeout(announceTimer);
  announcer.textContent = '';
  announcedField = el;
  announceTimer = setTimeout(() => {
    announcer.textContent = label + spokenText(error);
  }, 500);
};

const supportsUserInvalid = CSS.supports('selector(:user-invalid)');
const flagged = new WeakSet();
const announced = new WeakSet();
const dirtyState = new WeakMap();

const syncAria = (el, announce = false) => {
  if (!el.matches?.('input, select, textarea')) return;
  const invalid = supportsUserInvalid
    ? el.matches(':user-invalid')
    : el.classList.contains('user-invalid-fallback');
  if (invalid) {
    if (announce && !announced.has(el)) {
      announceError(el);
      announced.add(el);
    }
    el.setAttribute('aria-invalid', 'true');
    linkError(el, true);
    flagged.add(el);
  } else if (flagged.has(el) || el.getAttribute('aria-invalid') !== 'true') {
    el.setAttribute('aria-invalid', 'false');
    linkError(el, false);
    flagged.delete(el);
    announced.delete(el);
    if (announcedField === el) {
      clearTimeout(announceTimer);
      announcer.textContent = '';
    }
  }
};

document.querySelectorAll('input, select, textarea').forEach((el) => syncAria(el));

document.addEventListener(
  'blur',
  (e) => {
    const el = e.target;
    if (!supportsUserInvalid && el.checkValidity) {
      const state = dirtyState.get(el) || { hasInteracted: false, hasBlurred: false };
      state.hasBlurred = true;
      dirtyState.set(el, state);
      if (state.hasInteracted) {
        el.classList.toggle('user-invalid-fallback', !el.validity.valid);
        el.classList.toggle('user-valid-fallback', el.validity.valid);
      }
    }
    syncAria(el, true);
  },
  true,
);

document.addEventListener('input', (e) => {
  const el = e.target;
  if (!supportsUserInvalid && el.checkValidity) {
    const state = dirtyState.get(el) || { hasInteracted: false, hasBlurred: false };
    state.hasInteracted = true;
    dirtyState.set(el, state);
    if (state.hasBlurred) {
      el.classList.toggle('user-invalid-fallback', !el.validity.valid);
      el.classList.toggle('user-valid-fallback', el.validity.valid);
    }
  }
  if (el.hasAttribute('aria-invalid')) syncAria(el);
});

document.addEventListener('change', (e) => syncAria(e.target, true), true);

document.addEventListener(
  'invalid',
  (e) => {
    const el = e.target;
    if (!supportsUserInvalid) {
      dirtyState.set(el, { hasInteracted: true, hasBlurred: true });
      el.classList.add('user-invalid-fallback');
      el.classList.remove('user-valid-fallback');
    }
    announced.add(el);
    syncAria(el);
  },
  true,
);

/* ==========================================================================
   4. Rate Limiter Playground Logic
   ========================================================================== */

const simForm = document.getElementById('sim-form');
const backendForm = document.getElementById('backend-form');
const redisUrlField = document.getElementById('redis-url-field');
const redisUrlInput = document.getElementById('redis-url');
const redisHealth = document.getElementById('redis-health');
const apiKeyInput = document.getElementById('api-key');
const burstSlider = document.getElementById('burst-slider');
const burstCountInput = document.getElementById('burst-count');
const burstOutput = document.getElementById('burst-output');
const parallelCheckbox = document.getElementById('parallel-mode');
const clearLogBtn = document.getElementById('clear-log-btn');
const activeClientLabel = document.getElementById('active-client-label');
const backendBadge = document.getElementById('backend-badge');
const logTbody = document.getElementById('log-tbody');
const logSummary = document.getElementById('log-summary');
const inspectDialog = document.getElementById('inspect-dialog');
const inspectSummary = document.getElementById('inspect-summary');
const inspectHeaders = document.getElementById('inspect-headers');
const inspectBody = document.getElementById('inspect-body');

const logs = [];
let seq = 0;

function updateRangeProgress(val) {
  if (!CSS.supports('accent-color', 'red')) {
    const pct = ((val - 1) / (50 - 1)) * 100;
    burstSlider.style.setProperty('--progress', `${pct}%`);
  }
}

function syncBurstDisplay(val) {
  const n = Number(val);
  burstOutput.textContent = `${n} ${n === 1 ? 'request' : 'requests'}`;
  updateRangeProgress(Math.min(50, Math.max(1, n || 1)));
}

burstSlider.addEventListener('input', () => {
  burstCountInput.value = burstSlider.value;
  syncBurstDisplay(burstSlider.value);
  syncAria(burstCountInput);
});

burstCountInput.addEventListener('input', () => {
  const n = Number(burstCountInput.value);
  if (n >= 1 && n <= 50) {
    burstSlider.value = String(n);
    syncBurstDisplay(n);
  }
});

apiKeyInput.addEventListener('input', () => {
  activeClientLabel.innerHTML = '';
  const code = document.createElement('code');
  code.textContent = apiKeyInput.value || '(empty)';
  activeClientLabel.append('Client: ', code);
});

syncBurstDisplay(burstSlider.value);

function renderBackendState(data) {
  const isRedis = data.backend === 'redis';
  backendForm.elements.backend.value = data.backend;
  if (data.redis_url) redisUrlInput.value = data.redis_url;
  redisUrlField.hidden = !isRedis;
  redisUrlInput.required = isRedis;

  backendBadge.className = 'badge';
  if (!isRedis) {
    backendBadge.textContent = 'Backend: in-memory';
    redisHealth.textContent = '';
  } else if (data.redis_connected) {
    backendBadge.classList.add('badge--ok');
    backendBadge.textContent = 'Backend: redis (connected)';
    redisHealth.textContent = 'Connected';
  } else {
    backendBadge.classList.add('badge--degraded');
    backendBadge.textContent = 'Backend: redis (degraded)';
    redisHealth.textContent = 'Unreachable — /search fails open, /login fails closed';
  }
}

backendForm.addEventListener('change', () => {
  const isRedis = backendForm.elements.backend.value === 'redis';
  redisUrlField.hidden = !isRedis;
  redisUrlInput.required = isRedis;
  syncAria(redisUrlInput);
});

backendForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!backendForm.checkValidity()) {
    const firstInvalid = backendForm.querySelector(':invalid');
    for (const field of backendForm.querySelectorAll('input, select, textarea')) {
      if (!field.validity.valid) {
        field.dispatchEvent(new Event('invalid', { cancelable: true }));
      }
    }
    firstInvalid?.focus();
    return;
  }
  const backend = backendForm.elements.backend.value;
  const payload = { backend };
  if (backend === 'redis') payload.redis_url = redisUrlInput.value.trim();
  const res = await fetch('/api/backend', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    renderBackendState(await res.json());
  }
});

async function loadConfig() {
  try {
    const res = await fetch('/api/config');
    if (!res.ok) return;
    renderBackendState(await res.json());
  } catch {
    backendBadge.textContent = 'Backend: unavailable';
  }
}

function updateBucketCard(route, entry) {
  const card = document.querySelector(`.bucket-card[data-route="${route}"]`);
  if (!card) return;
  const statusEl = card.querySelector('[data-field="status"]');
  const remainingEl = card.querySelector('[data-field="remaining"]');
  const progressEl = card.querySelector('[data-field="progress"]');
  const resetEl = card.querySelector('[data-field="reset"]');
  const retryEl = card.querySelector('[data-field="retry"]');

  const limit = Number(entry.rateLimit.limit || progressEl.max);
  const remaining = Number(entry.rateLimit.remaining ?? 0);

  progressEl.max = limit;
  progressEl.value = remaining;
  progressEl.textContent = `${remaining} / ${limit}`;
  remainingEl.textContent = `${remaining} / ${limit}`;
  resetEl.textContent = `${entry.rateLimit.reset ?? 0}s`;
  retryEl.textContent = entry.rateLimit.retryAfter ? `${entry.rateLimit.retryAfter}s` : '—';

  statusEl.className = `status-pill ${entry.status === 429 ? 'status-pill--limited' : 'status-pill--ok'}`;
  const degradedTag = entry.degraded ? ' · degraded' : '';
  statusEl.textContent =
    (entry.status === 429 ? '429 Limited' : '200 Allowed') + degradedTag;
}

function renderLogRow(entry) {
  const emptyRow = logTbody.querySelector('.empty-row');
  if (emptyRow) emptyRow.remove();

  const tr = document.createElement('tr');

  const th = document.createElement('th');
  th.scope = 'row';
  th.textContent = `#${entry.id}`;

  const tdTime = document.createElement('td');
  tdTime.textContent = entry.time;

  const tdRoute = document.createElement('td');
  const routeCode = document.createElement('code');
  routeCode.textContent = `${entry.method} ${entry.route}`;
  tdRoute.append(routeCode);

  const tdClient = document.createElement('td');
  const clientCode = document.createElement('code');
  clientCode.textContent = entry.apiKey;
  tdClient.append(clientCode);

  const tdStatus = document.createElement('td');
  const pill = document.createElement('span');
  pill.className = `status-pill ${entry.status === 429 ? 'status-pill--limited' : 'status-pill--ok'}`;
  pill.textContent = `${entry.status} ${entry.status === 429 ? 'Limited' : 'OK'}`;
  tdStatus.append(pill);

  const tdRem = document.createElement('td');
  tdRem.textContent = `${entry.rateLimit.remaining ?? '—'} / ${entry.rateLimit.limit ?? '—'}`;

  const tdReset = document.createElement('td');
  tdReset.textContent = entry.rateLimit.reset != null ? `${entry.rateLimit.reset}s` : '—';

  const tdAction = document.createElement('td');
  const inspectBtn = document.createElement('button');
  inspectBtn.type = 'button';
  inspectBtn.className = 'btn-link';
  inspectBtn.textContent = 'Inspect';
  inspectBtn.addEventListener('click', () => openInspector(entry));
  tdAction.append(inspectBtn);

  tr.append(th, tdTime, tdRoute, tdClient, tdStatus, tdRem, tdReset, tdAction);
  logTbody.prepend(tr);

  // Keep table responsive and bounded to last 100 entries
  while (logTbody.children.length > 100) {
    logTbody.lastElementChild.remove();
  }
}

function updateSummary() {
  const limited = logs.filter((l) => l.status === 429).length;
  logSummary.textContent = `${logs.length} requests sent (${logs.length - limited} allowed, ${limited} rate-limited)`;
}

function openInspector(entry) {
  inspectSummary.innerHTML = '';
  const pairs = [
    ['Request', `${entry.method} ${entry.route}`],
    ['Client Key', entry.apiKey],
    ['HTTP Status', `${entry.status} (${entry.durationMs} ms)`],
    ['Timestamp', entry.time],
  ];
  for (const [k, v] of pairs) {
    const div = document.createElement('div');
    const dt = document.createElement('dt');
    dt.textContent = k;
    const dd = document.createElement('dd');
    dd.textContent = v;
    div.append(dt, dd);
    inspectSummary.append(div);
  }
  inspectHeaders.textContent = Object.entries(entry.headers)
    .map(([k, v]) => `${k}: ${v}`)
    .join('\n');
  inspectBody.textContent = JSON.stringify(entry.body, null, 2);
  inspectDialog.showModal();
}

async function sendOne(route, apiKey) {
  const method = route === '/login' ? 'POST' : 'GET';
  const started = performance.now();
  const res = await fetch(route, {
    method,
    headers: { 'x-api-key': apiKey },
  });
  const durationMs = Math.round(performance.now() - started);
  const body = await res.json().catch(() => ({}));
  const headers = {};
  res.headers.forEach((value, key) => {
    headers[key] = value;
  });

  const entry = {
    id: ++seq,
    time: new Date().toLocaleTimeString(),
    route,
    method,
    apiKey,
    status: res.status,
    degraded: res.headers.get('x-ratelimit-degraded') === 'true',
    durationMs,
    headers,
    body,
    rateLimit: {
      limit: res.headers.get('ratelimit-limit'),
      remaining: res.headers.get('ratelimit-remaining'),
      reset: res.headers.get('ratelimit-reset'),
      retryAfter: res.headers.get('retry-after'),
    },
  };

  logs.push(entry);
  updateBucketCard(route, entry);
  renderLogRow(entry);
  updateSummary();
}

simForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!simForm.checkValidity()) {
    const firstInvalid = simForm.querySelector(':invalid');
    for (const field of simForm.querySelectorAll('input, select, textarea')) {
      if (!field.validity.valid) {
        field.dispatchEvent(new Event('invalid', { cancelable: true }));
      }
    }
    firstInvalid?.focus();
    return;
  }

  const route = simForm.elements.route.value;
  const apiKey = apiKeyInput.value.trim();
  const mode = event.submitter?.dataset.mode || 'single';
  const count = mode === 'burst' ? Number(burstCountInput.value) : 1;

  if (parallelCheckbox.checked && count > 1) {
    await Promise.all(Array.from({ length: count }, () => sendOne(route, apiKey)));
  } else {
    for (let i = 0; i < count; i++) {
      await sendOne(route, apiKey);
    }
  }
});

clearLogBtn.addEventListener('click', () => {
  logs.length = 0;
  logTbody.innerHTML =
    '<tr class="empty-row"><td colspan="8">Send a request or burst to see live token-bucket responses.</td></tr>';
  logSummary.textContent = '0 requests sent';
});

loadConfig();
