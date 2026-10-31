'use strict';

/* realme Buds Control — browser front end.
   All device work happens server side on a single worker thread; this file
   only renders state and sends intents. */

const F_MIN = 20, F_MAX = 20000;
const DISPLAY_LIMIT = 8;        // dB at the top and bottom of the curve
const BELL_OCTAVES = 1.1;       // how wide one band reads on screen

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  connected: false,
  limits: { gainMin: -6, gainMax: 6, bassMin: -5, bassMax: 5, bands: 6 },
  presets: [],
  featureNames: {},
  device: null,
  bands: [],                    // [{hz, gain}]
  savedBands: null,             // what the earbuds last reported
  bass: [0, 0, 0],
  savedBass: null,              // what the earbuds last reported
  activePreset: null,
  busy: false,
};

/* ───────────────────────────── helpers ───────────────────────────── */

function toast(message, kind = 'ok') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.innerHTML = `<span class="mark">${kind === 'ok' ? '✓' : '!'}</span><span></span>`;
  el.lastElementChild.textContent = message;
  $('#toasts').append(el);
  setTimeout(() => {
    el.classList.add('leaving');
    setTimeout(() => el.remove(), 220);
  }, kind === 'ok' ? 2600 : 5200);
}

async function api(path, body) {
  const options = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) }
    : {};
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json();
}

function setBusy(on) {
  state.busy = on;
  $('#status-dot').dataset.state = on ? 'busy' : (state.connected ? 'on' : 'off');
  $$('.btn, .preset').forEach(b => { b.disabled = on || (!state.connected && b.id !== 'connect-btn'); });
  $$('.band input, .feature input').forEach(i => {
    if (!i.dataset.lockedOff) i.disabled = on || !state.connected;
  });
}

/* ─────────────────────────── curve drawing ───────────────────────── */

const xFor = (hz, w, pad) => {
  const clamped = Math.min(F_MAX, Math.max(F_MIN, hz));
  return pad + (Math.log10(clamped / F_MIN) / Math.log10(F_MAX / F_MIN)) * (w - 2 * pad);
};
const freqFor = (x, w, pad) => {
  const r = Math.min(1, Math.max(0, (x - pad) / Math.max(1, w - 2 * pad)));
  return Math.round(F_MIN * Math.pow(10, r * Math.log10(F_MAX / F_MIN)));
};
const yFor = (db, h, pad) =>
  (h / 2) - (db / DISPLAY_LIMIT) * ((h - 2 * pad) / 2);
const dbFor = (y, h, pad) =>
  ((h / 2) - y) / ((h - 2 * pad) / 2) * DISPLAY_LIMIT;

function responseAt(hz) {
  return state.bands.reduce((total, band) => total + bellAt(band, hz), 0);
}

let rafPending = false;
function scheduleDraw() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => { rafPending = false; drawCurve(); });
}

const NS = 'http://www.w3.org/2000/svg';
const GRID_DB = [8, 6, 4, 2, 0, -2, -4, -6, -8];
const GRID_HZ = [[50, '50'], [100, '100'], [200, '200'], [500, '500'],
                 [1000, '1k'], [2000, '2k'], [5000, '5k'], [10000, '10k']];

const bellAt = (band, hz) => (!band.gain || !band.hz) ? 0 :
  band.gain * Math.exp(-0.5 * Math.pow(Math.log2(hz / band.hz) / BELL_OCTAVES, 2));

const toPath = pts =>
  pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');

function drawCurve() {
  const svg = $('#curve');
  const w = svg.clientWidth || 900;
  const h = svg.clientHeight || 232;
  const pad = 16;
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);

  // grid: a line every 2 dB, labelled every 4
  const grid = $('#grid');
  grid.textContent = '';
  for (const db of GRID_DB) {
    const y = yFor(db, h, pad);
    const line = document.createElementNS(NS, 'line');
    line.setAttribute('x1', pad); line.setAttribute('x2', w - pad);
    line.setAttribute('y1', y);   line.setAttribute('y2', y);
    if (db === 0) line.setAttribute('class', 'zero');
    else line.setAttribute('stroke-dasharray', db % 4 ? '1 7' : '2 5');
    grid.append(line);
    if (db % 4) continue;
    const text = document.createElementNS(NS, 'text');
    text.setAttribute('x', w - pad - 2); text.setAttribute('y', y - 5);
    text.setAttribute('text-anchor', 'end');
    text.textContent = db > 0 ? `+${db}` : db;
    grid.append(text);
  }
  for (const [hz, label] of GRID_HZ) {
    const x = xFor(hz, w, pad);
    const line = document.createElementNS(NS, 'line');
    line.setAttribute('x1', x); line.setAttribute('x2', x);
    line.setAttribute('y1', pad * 0.5); line.setAttribute('y2', h - pad - 4);
    line.setAttribute('stroke-dasharray', '2 6');
    grid.append(line);
    const text = document.createElementNS(NS, 'text');
    text.setAttribute('x', x); text.setAttribute('y', h - 4);
    text.setAttribute('text-anchor', 'middle');
    text.textContent = label;
    grid.append(text);
  }

  /* Sample once.  Each band is drawn on its own as well as summed, because a
     dot sits at its band's gain while the bright line is every band added
     together — without the faint bells the dots look unrelated to the line. */
  const steps = 200;
  const clamp = y => Math.max(2, Math.min(h - 2, y));
  const sum = [], bells = state.bands.map(() => []);
  for (let i = 0; i <= steps; i++) {
    const ratio = i / steps;
    const hz = F_MIN * Math.pow(10, ratio * Math.log10(F_MAX / F_MIN));
    const x = pad + ratio * (w - 2 * pad);
    let total = 0;
    state.bands.forEach((band, b) => {
      const db = bellAt(band, hz);
      total += db;
      bells[b].push([x, clamp(yFor(db, h, pad))]);
    });
    sum.push([x, clamp(yFor(total, h, pad))]);
  }

  const bellGroup = $('#bells');
  bellGroup.textContent = '';
  bells.forEach((pts, b) => {
    if (!state.bands[b].gain) return;      // a flat band just traces the zero line
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', toPath(pts));
    bellGroup.append(path);
  });

  const d = toPath(sum);
  $('#curve-line').setAttribute('d', d);
  $('#curve-fill').setAttribute('d',
    `${d} L${sum.at(-1)[0].toFixed(1)} ${yFor(0, h, pad)} L${sum[0][0].toFixed(1)} ${yFor(0, h, pad)} Z`);

  // draggable handles
  const handles = $('#handles');
  handles.textContent = '';
  state.bands.forEach((band, index) => {
    const g = document.createElementNS(NS, 'g');
    g.setAttribute('class', 'handle');
    g.dataset.index = index;
    const x = xFor(band.hz, w, pad);
    const y = Math.max(6, Math.min(h - 6, yFor(band.gain, h, pad)));
    const hit = document.createElementNS(NS, 'circle');
    hit.setAttribute('class', 'hit');
    hit.setAttribute('cx', x); hit.setAttribute('cy', y); hit.setAttribute('r', 18);
    const dot = document.createElementNS(NS, 'circle');
    dot.setAttribute('class', 'dot');
    dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.setAttribute('r', 6);
    const tag = document.createElementNS(NS, 'text');
    tag.setAttribute('class', 'tag');
    tag.setAttribute('x', x);
    tag.setAttribute('y', y < 26 ? y + 18 : y - 13);   // flip below near the top
    tag.setAttribute('text-anchor', 'middle');
    tag.textContent = index + 1;
    g.append(hit, dot, tag);
    handles.append(g);
  });
}

/* ───────────────────────── cursor readout ────────────────────────── */

const fmtHz = hz => hz >= 1000
  ? `${(hz / 1000).toFixed(hz >= 10000 ? 1 : 2).replace(/\.?0+$/, '')} kHz`
  : `${Math.round(hz)} Hz`;
const fmtDb = db => `${db > 0 ? '+' : db < 0 ? '−' : ''}${Math.abs(db).toFixed(1)} dB`;

function setReadout(html, x) {
  const svg = $('#curve');
  const readout = $('#readout');
  readout.innerHTML = html;
  readout.hidden = false;
  readout.style.left = `${Math.max(60, Math.min(svg.clientWidth - 60, x)) + 6}px`;
  const line = $('#cursor-line');
  line.setAttribute('x1', x); line.setAttribute('x2', x);
  line.setAttribute('y1', 8); line.setAttribute('y2', svg.clientHeight - 20);
  line.classList.remove('off');          // SVG ignores the hidden attribute
}

function hideReadout() {
  $('#readout').hidden = true;
  $('#cursor-line').classList.add('off');
}

/* dragging a handle changes both frequency and gain */
(function enableDragging() {
  const svg = $('#curve');
  let dragging = null;

  svg.addEventListener('pointerdown', event => {
    const handle = event.target.closest('.handle');
    if (!handle || !state.connected) return;
    dragging = Number(handle.dataset.index);
    handle.classList.add('dragging');
    svg.setPointerCapture(event.pointerId);
  });

  svg.addEventListener('pointermove', event => {
    const rect = svg.getBoundingClientRect();
    const pad = 16;
    const x = event.clientX - rect.left;

    if (dragging === null) {
      // hovering: show what the summed curve does at this frequency
      const hz = freqFor(x, rect.width, pad);
      setReadout(`<b>${fmtHz(hz)}</b><span>${fmtDb(responseAt(hz))}</span>`, x);
      return;
    }

    const band = state.bands[dragging];
    band.hz = Math.max(F_MIN, Math.min(F_MAX, freqFor(x, rect.width, pad)));
    const db = dbFor(event.clientY - rect.top, rect.height, pad);
    band.gain = Math.max(state.limits.gainMin,
      Math.min(state.limits.gainMax, Math.round(db)));
    setReadout(
      `<b>Band ${dragging + 1} · ${fmtHz(band.hz)}</b><span>${fmtDb(band.gain)}</span>`,
      xFor(band.hz, rect.width, pad));
    syncBandInputs();
    clearPresetSelection();
    markDirty();
    scheduleDraw();
  });

  const release = event => {
    if (dragging === null) return;
    $$('.handle').forEach(h => h.classList.remove('dragging'));
    dragging = null;
    try { svg.releasePointerCapture(event.pointerId); } catch { /* already gone */ }
  };
  svg.addEventListener('pointerup', release);
  svg.addEventListener('pointercancel', release);
  svg.addEventListener('pointerleave', () => { if (dragging === null) hideReadout(); });
})();

window.addEventListener('resize', scheduleDraw);

/* ──────────────────────────── band rows ──────────────────────────── */

function buildBands() {
  const host = $('#bands');
  host.textContent = '';
  state.bands.forEach((band, index) => {
    const row = document.createElement('div');
    row.className = 'band';
    row.innerHTML = `
      <div class="freq">
        <input type="number" min="${F_MIN}" max="${F_MAX}" step="1"
               value="${band.hz}" data-band="${index}"
               data-testid="freq-${index}" aria-label="Band ${index + 1} frequency">
        <span class="unit">Hz</span>
      </div>
      <input type="range" min="${state.limits.gainMin}" max="${state.limits.gainMax}"
             step="1" value="${band.gain}" data-gain="${index}"
             data-testid="gain-${index}" aria-label="Band ${index + 1} gain">
      <span class="value" data-value="${index}"></span>`;
    host.append(row);
  });

  host.oninput = event => {
    const target = event.target;
    if (target.dataset.gain !== undefined) {
      const index = Number(target.dataset.gain);
      state.bands[index].gain = Number(target.value);
      paintRange(target);
      updateValueLabel(index);
      clearPresetSelection();
      markDirty();
      scheduleDraw();
    } else if (target.dataset.band !== undefined) {
      const index = Number(target.dataset.band);
      const hz = Number(target.value);
      const valid = Number.isFinite(hz) && hz >= F_MIN && hz <= F_MAX;
      target.classList.toggle('invalid', !valid);
      if (valid) {
        state.bands[index].hz = Math.round(hz);
        clearPresetSelection();
        markDirty();
        scheduleDraw();
      }
    }
  };
  syncBandInputs();
}

function paintRange(input) {
  const min = Number(input.min), max = Number(input.max);
  const pct = ((Number(input.value) - min) / (max - min)) * 100;
  input.style.setProperty('--pct', `${pct}%`);
}

function updateValueLabel(index) {
  const label = $(`[data-value="${index}"]`);
  if (!label) return;
  const gain = state.bands[index].gain;
  label.textContent = `${gain > 0 ? '+' : ''}${gain} dB`;
  label.classList.toggle('nonzero', gain !== 0);
}

function syncBandInputs() {
  state.bands.forEach((band, index) => {
    const freq = $(`[data-band="${index}"]`);
    const gain = $(`[data-gain="${index}"]`);
    if (freq && document.activeElement !== freq) freq.value = band.hz;
    if (gain) { gain.value = band.gain; paintRange(gain); }
    updateValueLabel(index);
  });
}

function markDirty() {
  const saved = state.savedBands;
  const same = saved && saved.length === state.bands.length &&
    saved.every((b, i) => b.hz === state.bands[i].hz && b.gain === state.bands[i].gain);
  $('#eq-dirty').hidden = !!same || !state.connected;
}

/* Only a hand edit drops the preset. Choosing a preset leaves it selected even
   though it is unsaved, otherwise the highlight vanishes the instant you pick
   one and the write gets labelled "Custom". */
function clearPresetSelection() {
  if (!state.activePreset) return;
  state.activePreset = null;
  $('#preset-note').textContent = '';
  $$('.preset').forEach(p => p.setAttribute('aria-pressed', 'false'));
}

/* ──────────────────────────── bass rows ──────────────────────────── */

function buildBass() {
  const host = $('#bass-bands');
  host.textContent = '';
  ['Low', 'Mid', 'High'].forEach((label, index) => {
    const row = document.createElement('div');
    row.className = 'band';
    row.innerHTML = `
      <span class="label">${label}</span>
      <input type="range" min="${state.limits.bassMin}" max="${state.limits.bassMax}"
             step="1" value="${state.bass[index] || 0}" data-bass="${index}"
             data-testid="bass-${index}" aria-label="${label} bass">
      <span class="value" data-bassvalue="${index}"></span>`;
    host.append(row);
  });
  host.oninput = event => {
    const index = Number(event.target.dataset.bass);
    if (Number.isNaN(index)) return;
    state.bass[index] = Number(event.target.value);
    paintRange(event.target);
    syncBassLabels();
    markBassDirty();
  };
  syncBassLabels();
}

/* The equaliser and the bass engine are two separate writes, so each tracks
   its own unsaved state. */
function markBassDirty() {
  const saved = state.savedBass;
  const same = !saved || saved.every((value, i) => value === state.bass[i]);
  $('#bass-dirty').hidden = same || !state.connected;
}

function syncBassLabels() {
  state.bass.forEach((value, index) => {
    const input = $(`[data-bass="${index}"]`);
    const label = $(`[data-bassvalue="${index}"]`);
    if (input) { input.value = value; paintRange(input); }
    if (label) {
      label.textContent = `${value > 0 ? '+' : ''}${value}`;
      label.classList.toggle('nonzero', value !== 0);
    }
  });
}

/* ───────────────────────────── presets ───────────────────────────── */

function buildPresets() {
  const host = $('#presets');
  host.textContent = '';
  state.presets.forEach(preset => {
    const button = document.createElement('button');
    button.className = 'preset';
    button.type = 'button';
    button.textContent = preset.name;
    button.setAttribute('aria-pressed', 'false');
    button.dataset.testid = `preset-${preset.key}`;
    button.onclick = () => applyPreset(preset);
    host.append(button);
  });
}

function applyPreset(preset) {
  state.bands = preset.bands.map(([hz, gain]) => ({ hz, gain }));
  state.bass = [...preset.bass];
  state.activePreset = preset.key;
  $('#preset-note').textContent = preset.description;
  $$('.preset').forEach(p =>
    p.setAttribute('aria-pressed', String(p.textContent === preset.name)));
  buildBands();
  syncBassLabels();
  markDirty();
  markBassDirty();
  scheduleDraw();
}

/* ───────────────────────────── features ──────────────────────────── */

function buildFeatures() {
  const host = $('#features');
  const reported = state.device?.features || {};
  // While disconnected nothing has been reported yet, so saying a feature is
  // missing from the firmware would be untrue.
  const known = state.connected && state.device;
  const ids = Object.keys(state.featureNames)
    .map(Number)
    .filter(id => [24, 27, 6, 26, 9, 29, 17, 4, 5, 8, 11, 54, 19].includes(id))
    .sort((a, b) => {
      const sa = String(a) in reported, sb = String(b) in reported;
      return sa === sb ? a - b : (sa ? -1 : 1);
    });

  host.textContent = '';
  ids.forEach(id => {
    const supported = String(id) in reported;
    const on = reported[String(id)] === 1;
    const row = document.createElement('div');
    row.className = `feature${known && !supported ? ' unsupported' : ''}`;
    const why = !known ? '<span class="why">connect to check</span>'
              : supported ? ''
              : '<span class="why">not in this firmware</span>';
    const lock = known && !supported ? 'disabled data-locked-off="1"'
               : (known ? '' : 'disabled');
    row.innerHTML = `
      <div>
        <span class="name">${state.featureNames[id]}</span>
        ${why}
      </div>
      <label class="switch">
        <input type="checkbox" data-feature="${id}" data-testid="feature-${id}"
               ${on ? 'checked' : ''} ${lock}>
        <span class="track"></span>
      </label>`;
    host.append(row);
  });

  host.onchange = async event => {
    const id = Number(event.target.dataset.feature);
    if (Number.isNaN(id)) return;
    const wanted = event.target.checked;
    setBusy(true);
    try {
      const result = await api('/api/feature', { id, on: wanted });
      if (result.ok) {
        toast(`${state.featureNames[id]} turned ${wanted ? 'on' : 'off'}`);
      } else {
        event.target.checked = !wanted;
        toast(result.error, 'bad');
      }
    } catch (error) {
      event.target.checked = !wanted;
      toast(error.message, 'bad');
    } finally {
      setBusy(false);
    }
  };
}

/* ─────────────────────────── state sync ──────────────────────────── */

function renderDevice() {
  const device = state.device;
  const dot = $('#status-dot');
  if (!state.connected || !device) {
    dot.dataset.state = 'off';
    $('#device-name').textContent = 'Not connected';
    $('#device-sub').textContent = 'Press Connect to look again';
    $('#battery').hidden = true;
    $('#connect-btn').textContent = 'Connect';
    $('#device-info').textContent = 'Not connected.';
    document.body.classList.add('disconnected');
    return;
  }
  dot.dataset.state = 'on';
  $('#device-name').textContent = state.deviceName || 'Earbuds';
  $('#device-sub').textContent = `firmware ${device.firmware || 'unknown'}`;
  $('#connect-btn').textContent = 'Disconnect';
  document.body.classList.remove('disconnected');

  const names = { '1': 'L', '2': 'R', '3': 'Case' };
  const cells = Object.entries(device.battery || {})
    .filter(([key]) => key !== '3')
    .map(([key, pct]) => `<span>${names[key] || key} <b>${pct}%</b></span>`);
  $('#battery').innerHTML = cells.join('');
  $('#battery').hidden = cells.length === 0;

  const lines = [
    `Firmware       ${device.firmware || 'unknown'}`,
    `Region code    ${device.region ?? 'unknown'}`,
  ];
  for (const [key, pct] of Object.entries(device.battery || {})) {
    lines.push(`${(names[key] || key).padEnd(14)} ${pct}%`);
  }
  if (device.activePreset !== null && device.activePreset !== undefined) {
    lines.push(`Active preset  ${device.activePreset}`);
  }
  const reported = Object.entries(device.features || {});
  if (reported.length) {
    lines.push('', 'Switch features reported:');
    for (const [id, value] of reported.sort((a, b) => a[0] - b[0])) {
      lines.push(`  [${String(id).padStart(2)}] ${(state.featureNames[id] || 'unknown').padEnd(24)} ${value ? 'on' : 'off'}`);
    }
    const missing = [11, 19, 54].filter(id => !(String(id) in (device.features || {})));
    if (missing.length) {
      lines.push('', 'Not implemented by this firmware:');
      missing.forEach(id => lines.push(`  [${String(id).padStart(2)}] ${state.featureNames[id]}`));
    }
  }
  $('#device-info').textContent = lines.join('\n');
}

function adoptDeviceEq() {
  const eq = state.device?.eq;
  if (eq?.bands?.length) {
    state.bands = eq.bands.map(([hz, gain]) => ({ hz, gain }));
    state.savedBands = state.bands.map(b => ({ ...b }));
    const match = state.presets.find(p =>
      p.bands.length === state.bands.length &&
      p.bands.every(([hz, gain], i) => hz === state.bands[i].hz && gain === state.bands[i].gain));
    state.activePreset = match?.key || null;
    $('#preset-note').textContent = match?.description || '';
    $$('.preset').forEach(p =>
      p.setAttribute('aria-pressed', String(!!match && p.textContent === match.name)));
  }
  const bass = state.device?.bass || [];
  if (bass.length) {
    state.bass = [0, 0, 0];
    bass.forEach(entry => {
      if (entry.band >= 1 && entry.band <= 3) state.bass[entry.band - 1] = entry.value;
    });
    state.savedBass = [...state.bass];
  }
  buildBands();
  syncBassLabels();
  markDirty();
  markBassDirty();
  scheduleDraw();
}

async function refresh() {
  const data = await api('/api/state');
  state.connected = data.connected;
  state.limits = data.limits || state.limits;
  state.presets = data.presets || [];
  state.featureNames = data.featureNames || {};
  state.device = data.device;
  if (data.portName) state.deviceName = data.portName;

  if (!$('#presets').children.length) buildPresets();
  if (!state.bands.length) {
    const fallback = state.presets.find(p => p.key === 'classic') || state.presets[0];
    if (fallback) {
      state.bands = fallback.bands.map(([hz, gain]) => ({ hz, gain }));
      state.activePreset = fallback.key;
      $('#preset-note').textContent = fallback.description;
      $$('.preset').forEach(p =>
        p.setAttribute('aria-pressed', String(p.textContent === fallback.name)));
    }
  }
  if (state.connected && state.device) {
    adoptDeviceEq();
  } else {
    // still draw the rows and curve so the page is never half-empty
    buildBands();
    markDirty();
    markBassDirty();
    scheduleDraw();
  }
  buildFeatures();
  renderDevice();
  setBusy(false);
  if (data.error) toast(data.error, 'bad');
}

/* ───────────────────────────── actions ───────────────────────────── */

$('#connect-btn').onclick = async () => {
  setBusy(true);
  try {
    if (state.connected) {
      await api('/api/disconnect', {});
      state.connected = false;
      state.device = null;
      state.savedBands = null;
      state.savedBass = null;
      toast('Disconnected');
    } else {
      const result = await api('/api/connect', {});
      if (!result.ok) { toast(result.error, 'bad'); setBusy(false); return; }
      state.deviceName = result.name;
      toast(`Connected to ${result.name}`);
    }
    await refresh();
  } catch (error) {
    toast(error.message, 'bad');
  } finally {
    setBusy(false);
  }
};

$('#eq-apply').onclick = async () => {
  const invalid = $$('.band input.invalid');
  if (invalid.length) { toast('Fix the highlighted frequency first', 'bad'); return; }
  setBusy(true);
  try {
    const name = state.presets.find(p => p.key === state.activePreset)?.name || 'Custom';
    const result = await api('/api/eq', {
      bands: state.bands.map(b => [b.hz, b.gain]), name,
    });
    if (result.ok) {
      state.savedBands = state.bands.map(b => ({ ...b }));
      markDirty();
      toast(`Applied "${result.name}" to the earbuds`);
    } else {
      toast(result.error, 'bad');
    }
  } catch (error) {
    toast(error.message, 'bad');
  } finally {
    setBusy(false);
  }
};

$('#eq-read').onclick = async () => {
  setBusy(true);
  try {
    await refresh();
    toast('Read the current curve from the earbuds');
  } catch (error) {
    toast(error.message, 'bad');
  } finally {
    setBusy(false);
  }
};

$('#bass-enabled').onchange = () => { $('#bass-dirty').hidden = !state.connected; };

$('#bass-apply').onclick = async () => {
  setBusy(true);
  try {
    const result = await api('/api/bass', {
      values: state.bass, enabled: $('#bass-enabled').checked,
    });
    if (result.ok) {
      state.savedBass = [...state.bass];
      markBassDirty();
    }
    toast(result.ok ? 'Bass engine updated' : result.error, result.ok ? 'ok' : 'bad');
  } catch (error) {
    toast(error.message, 'bad');
  } finally {
    setBusy(false);
  }
};

/* ───────────────────────────── startup ───────────────────────────── */

(async function start() {
  buildBass();
  try {
    await refresh();
    if (!state.connected) {
      // try once automatically so the common case needs no clicks
      const result = await api('/api/connect', {});
      if (result.ok) {
        state.deviceName = result.name;
        await refresh();
        toast(`Connected to ${result.name}`);
      } else {
        $('#device-sub').textContent = 'No earbuds found — press Connect to retry';
      }
    }
  } catch (error) {
    toast(error.message, 'bad');
  }
  scheduleDraw();
})();
