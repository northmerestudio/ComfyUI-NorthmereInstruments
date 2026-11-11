import { app } from '../../scripts/app.js';

// The curve is stored as the existing version-one JSON string. This extension
// changes only how that value is edited; no backend or workflow migration is needed.
const STYLE_ID = 'northmere-instruments-curve-style';
if (!document.getElementById(STYLE_ID)) {
  const link = document.createElement('link');
  link.id = STYLE_ID;
  link.rel = 'stylesheet';
  link.href = new URL('./curve.css', import.meta.url).href;
  document.head.append(link);
}

// Nine editable control points. X is normalized progress, not a sampler step.
const DEFAULT_STRENGTHS = [0.5, 0.8, 0.92, 0.968, 0.9872, 0.99488, 0.997952, 0.9991808, 1];
const DEFAULT_POINTS = DEFAULT_STRENGTHS.map((strength, index) =>
  [index / (DEFAULT_STRENGTHS.length - 1), strength]);
const INTERPOLATIONS = [['linear', 'Linear'], ['smooth', 'Smooth'], ['hold', 'Hold']];
// A small set of useful starting shapes; all remain nine-point, editable envelopes.
// The preset is UI state only: saved workflows keep the existing curve JSON schema.
const PRESET_X = DEFAULT_POINTS.map(([x]) => x);
// At halfway, the early fade reaches full weight and remains there.
// The normalized exponential produces a smooth, steep rise without overshooting.
const earlyFadeIn = (x) => x >= 0.5 ? 1 : (1 - Math.exp(-8 * x)) / (1 - Math.exp(-4));
// Late schedules reduce early model-LoRA influence. The capped variant also
// avoids returning to full adapter strength during the final refinement.
const LATE_RISE_POINTS = [
  [0.000, 0.050],
  [0.125, 0.050],
  [0.250, 0.080],
  [0.375, 0.150],
  [0.500, 0.300],
  [0.625, 0.550],
  [0.750, 0.800],
  [0.875, 0.950],
  [1.000, 1.000],
];
const PRESETS = [
  ['off', 'Off', PRESET_X.map((x) => [x, 0])],
  ['full', 'Constant', PRESET_X.map((x) => [x, 1])],
  ['fade-in', 'Fade In', PRESET_X.map((x) => [x, x])],
  ['early-fade-in', 'Quick Fade In', PRESET_X.map((x) => [x, earlyFadeIn(x)])],
  ['fade-out', 'Fade Out', PRESET_X.map((x) => [x, 1 - x])],
  // The original default curve starts at 0.5 and is almost fully on by halfway.
  ['quick-rise', 'Quick Start', DEFAULT_POINTS],
  // Starts at zero; reaches full strength only after 75% of denoising progress.
  ['fast-rise', 'Ease Out', [
    [0.000, 0.000],
    [0.125, 0.372],
    [0.250, 0.635],
    [0.375, 0.807],
    [0.500, 0.912],
    [0.625, 0.975],
    [0.750, 1.000],
    [0.875, 1.000],
    [1.000, 1.000],
  ], 'smooth'],
  // Fitted to the 0.25-start screenshot; this is stronger, not softer, than fast-rise.
  ['soft-rise', 'Soft Start', [
    [0.000, 0.250],
    [0.125, 0.520],
    [0.250, 0.750],
    [0.375, 0.870],
    [0.500, 0.950],
    [0.625, 0.980],
    [0.750, 1.000],
    [0.875, 1.000],
    [1.000, 1.000],
  ], 'smooth'],
  ['late-rise', 'Late Fade In', LATE_RISE_POINTS, 'smooth'],
  // Same late shape with a maximum weight of 0.75, for stronger suppression.
  ['late-rise-capped', 'Late Fade In · 75%',
    LATE_RISE_POINTS.map(([x, y]) => [x, Number((y * 0.75).toFixed(4))]), 'smooth'],
];
const PRESET_OPTIONS = [['custom', 'Custom'], ...PRESETS.map(([id, label]) => [id, label])];
function matchingPreset(curve) {
  return PRESETS.find(([, , points, interpolation = 'linear']) => interpolation === curve.interpolation
    && points.length === curve.points.length
    && points.every(([x, y], index) => Math.abs(curve.points[index][0] - x) < 1e-6
      && Math.abs(curve.points[index][1] - y) < 1e-6))?.[0] ?? 'custom';
}
const MAX_POINTS = 64;
const MIN_GAP = 0.0001;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
// Retain sufficient precision for existing saved LoRA strengths and X positions.
const rounded = (value) => Number(value.toFixed(8));
const clonePoints = (points) => points.map(([x, y]) => [x, y]);
const format = (value) => Number(value.toFixed(3)).toString();
const precise = (value) => Number(value.toFixed(8)).toString();

function parseCurve(value) {
  try {
    const input = typeof value === 'string' ? JSON.parse(value) : value;
    if (input?.version !== 1 || !INTERPOLATIONS.some(([id]) => id === input.interpolation)
      || !Array.isArray(input.points) || input.points.length < 2 || input.points.length > MAX_POINTS) {
      throw new Error('Invalid curve shape');
    }
    const points = input.points.map((p) => {
      if (!Array.isArray(p) || p.length !== 2 || p.some((v) => typeof v !== 'number' || !Number.isFinite(v))) {
        throw new Error('Invalid point');
      }
      return [...p];
    });
    if (points[0][0] !== 0 || points.at(-1)[0] !== 1
      || points.some(([x, y], index) => x < 0 || x > 1 || Math.abs(y) > 20
        || (index > 0 && x <= points[index - 1][0]))) {
      throw new Error('Out-of-range curve');
    }
    return { version: 1, interpolation: input.interpolation, points };
  } catch (error) {
    throw new Error('Invalid stored LoRA envelope', { cause: error });
  }
}

function element(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}
function svgElement(tag, attributes = {}) {
  const result = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) result.setAttribute(key, String(value));
  return result;
}
function button(label, title) {
  const result = element('button', 'nmi-button', label);
  result.type = 'button';
  result.title = title;
  return result;
}
function controlLabel(text, control) {
  const wrapper = element('label', 'nmi-control');
  wrapper.append(element('span', 'nmi-control-label', text), control);
  return wrapper;
}
function selectControl(options, label) {
  const result = element('select', 'nmi-select');
  result.setAttribute('aria-label', label);
  for (const [value, text] of options) {
    const option = element('option', '', text);
    option.value = value;
    result.append(option);
  }
  return result;
}
function numericControl(label, step) {
  const input = element('input', 'nmi-number');
  input.type = 'number';
  input.step = String(step);
  input.setAttribute('aria-label', label);
  return input;
}

// Match the backend's PCHIP (Fritsch–Carlson) interpolation, including endpoints.
function slopes(points) {
  const n = points.length;
  const h = points.slice(1).map((point, i) => point[0] - points[i][0]);
  const d = h.map((width, i) => (points[i + 1][1] - points[i][1]) / width);
  if (n === 2) return [d[0], d[0]];
  const values = Array(n).fill(0);
  for (let i = 1; i < n - 1; i++) {
    if (d[i - 1] === 0 || d[i] === 0 || d[i - 1] * d[i] < 0) continue;
    const w1 = 2 * h[i] + h[i - 1];
    const w2 = h[i] + 2 * h[i - 1];
    values[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]);
  }
  for (const [index, delta, a, b, other] of [
    [0, d[0], h[0], h[1], d[1]],
    [n - 1, d[n - 2], h[n - 2], h[n - 3], d[n - 3]],
  ]) {
    let slope = ((2 * a + b) * delta - a * other) / (a + b);
    if (slope * delta <= 0) slope = 0;
    else if (delta * other < 0 && Math.abs(slope) > 3 * Math.abs(delta)) slope = 3 * delta;
    values[index] = slope;
  }
  return values;
}
function evaluate(points, interpolation, x, cachedSlopes = null) {
  if (x <= 0) return points[0][1];
  if (x >= 1) return points.at(-1)[1];
  let i = points.findIndex((point) => point[0] > x) - 1;
  i = clamp(i, 0, points.length - 2);
  const [x0, y0] = points[i];
  const [x1, y1] = points[i + 1];
  const width = x1 - x0;
  const t = (x - x0) / width;
  if (interpolation === 'hold') return y0;
  if (interpolation === 'linear') return y0 + t * (y1 - y0);
  const m = cachedSlopes ?? slopes(points);
  const y = (2 * t ** 3 - 3 * t ** 2 + 1) * y0
    + (t ** 3 - 2 * t ** 2 + t) * width * m[i]
    + (-2 * t ** 3 + 3 * t ** 2) * y1
    + (t ** 3 - t ** 2) * width * m[i + 1];
  return clamp(y, Math.min(y0, y1), Math.max(y0, y1));
}

function createCurveEditor(node, inputName, inputData) {
  const initialValue = inputData?.[1]?.default ?? { version: 1, interpolation: 'linear', points: DEFAULT_POINTS };
  let data = parseCurve(initialValue);
  let invalidSerializedValue = null;
  let selected = 0;
  let dragging = null;
  let widget;
  let viewMode = 'auto';
  let bounds = [-0.2, 1.2];
  const root = element('section', 'nmi-editor');
  const validationMessage = element('div', 'nmi-validation');
  validationMessage.hidden = true;
  validationMessage.textContent = 'Saved envelope data is invalid. Choose a preset or edit a point to replace it.';
  root.setAttribute('aria-label', 'LoRA envelope editor');
  // Stop editors from initiating canvas moves; keep keyboard focus native.
  root.addEventListener('pointerdown', (event) => event.stopPropagation());

  const heading = element('div', 'nmi-heading');
  const headingText = element('strong', 'nmi-title', 'Envelope');
  const preset = selectControl(PRESET_OPTIONS, 'Envelope shape');
  preset.classList.add('nmi-preset');
  preset.children[0].disabled = true; // 'Custom' is a state indicator, not a preset.
  preset.title = 'Choose a starting shape; you can edit its keyframes afterward';
  const reset = button('Reset', 'Restore the original nine-point envelope');
  heading.append(headingText, preset, reset);

  const chart = element('div', 'nmi-chart');
  const axisLabel = element('div', 'nmi-axis-top');
  axisLabel.append(element('span', '', 'LoRA weight'), element('span', '', '1.0 = normal')); 
  const svg = svgElement('svg', {
    viewBox: '0 0 460 224', role: 'group',
    'aria-label': 'LoRA envelope by normalized denoising progress',
  });
  svg.classList.add('nmi-plot');
  const chartCaption = element('div', 'nmi-axis-caption');
  chartCaption.append(element('span', '', 'Start'), element('span', '', 'Denoising progress'), element('span', '', 'End'));
  chart.append(axisLabel, svg, chartCaption);

  const inspector = element('div', 'nmi-inspector');
  const pointName = element('span', 'nmi-point-title', 'Keyframe 1 / 8');
  const xInput = numericControl('Selected keyframe progress (0 to 1)', 0.001);
  xInput.min = '0'; xInput.max = '1';
  const yInput = numericControl('Selected keyframe LoRA weight (-20 to 20)', 0.01);
  yInput.min = '-20'; yInput.max = '20';
  const remove = button('Delete', 'Delete selected interior keyframe');
  const add = button('+ Keyframe', 'Insert a new keyframe next to the selected keyframe');
  const pointActions = element('div', 'nmi-point-actions');
  pointActions.append(add, remove);
  inspector.append(pointName, controlLabel('Time (0–1)', xInput), controlLabel('Weight', yInput), pointActions);

  const footer = element('div', 'nmi-footer');
  const mode = selectControl(INTERPOLATIONS, 'Curve interpolation');
  footer.append(controlLabel('Between keyframes', mode));
  root.append(heading, chart, inspector, footer, validationMessage);

  const PLOT = { left: 46, right: 438, top: 15, bottom: 190 };
  const sx = (x) => PLOT.left + x * (PLOT.right - PLOT.left);
  const sy = (y) => PLOT.bottom - (y - bounds[0]) / (bounds[1] - bounds[0]) * (PLOT.bottom - PLOT.top);
  function fitBounds() {
    if (viewMode !== 'auto') return;
    const values = data.points.map((p) => p[1]);
    const bottom = Math.min(0, 1, ...values);
    const top = Math.max(0, 1, ...values);
    const margin = Math.max((top - bottom) * 0.15, 0.12);
    bounds = [Math.max(-20.5, bottom - margin), Math.min(20.5, top + margin)];
  }
  function pointerCoordinates(event) {
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const point = svg.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    const position = point.matrixTransform(ctm.inverse());
    return [clamp((position.x - PLOT.left) / (PLOT.right - PLOT.left), 0, 1),
      clamp(bounds[0] + (PLOT.bottom - position.y) / (PLOT.bottom - PLOT.top) * (bounds[1] - bounds[0]), -20, 20)];
  }
  function commit() {
    invalidSerializedValue = null;
    validationMessage.hidden = true;
    preset.value = matchingPreset(data);
    const json = JSON.stringify(data);
    // The DOM widget's value setter calls setValue(), which would replace the
    // entire editor state and reset the selected point. The value is instead
    // supplied by getValue() when ComfyUI serializes the widget.
    widget?.callback?.(json);
    node.graph?.change?.();
    node.setDirtyCanvas?.(true, true);
    app.graph?.setDirtyCanvas?.(true, true);
  }
  function changePoint(index, x, y) {
    const points = data.points;
    const minX = index === 0 ? 0 : points[index - 1][0] + MIN_GAP;
    const maxX = index === points.length - 1 ? 1 : points[index + 1][0] - MIN_GAP;
    points[index] = [index === 0 ? 0 : index === points.length - 1 ? 1 : rounded(clamp(x, minX, maxX)),
      rounded(clamp(y, -20, 20))];
    fitBounds();
    commit();
    render();
  }
  function deleteSelected() {
    if (selected === 0 || selected === data.points.length - 1) return;
    data.points.splice(selected, 1);
    selected = Math.min(selected, data.points.length - 2);
    fitBounds(); commit(); render();
  }
  function addPoint(x, y) {
    if (data.points.length >= MAX_POINTS || x <= 0 || x >= 1) return false;
    const index = data.points.findIndex((p) => p[0] > x);
    if (index < 1 || x <= data.points[index - 1][0] + MIN_GAP || x >= data.points[index][0] - MIN_GAP) return false;
    data.points.splice(index, 0, [rounded(x), rounded(clamp(y, -20, 20))]);
    selected = index;
    fitBounds(); commit(); render();
    return true;
  }
  function draw() {
    svg.replaceChildren();
    svg.append(svgElement('rect', { x: PLOT.left, y: PLOT.top, width: PLOT.right - PLOT.left,
      height: PLOT.bottom - PLOT.top, class: 'nmi-plot-background' }));
    for (let i = 0; i <= 4; i++) {
      const x = sx(i / 4);
      svg.append(svgElement('line', { x1: x, y1: PLOT.top, x2: x, y2: PLOT.bottom, class: 'nmi-grid' }));
      const tick = svgElement('text', { x, y: PLOT.bottom + 16, 'text-anchor': 'middle', class: 'nmi-tick' });
      tick.textContent = format(i / 4); svg.append(tick);
    }
    for (const y of [0, 1]) {
      if (y < bounds[0] || y > bounds[1]) continue;
      const lineY = sy(y);
      svg.append(svgElement('line', { x1: PLOT.left, y1: lineY, x2: PLOT.right, y2: lineY,
        class: y === 0 ? 'nmi-reference nmi-reference-zero' : 'nmi-reference' }));
      const tick = svgElement('text', { x: PLOT.left - 9, y: lineY + 4, 'text-anchor': 'end', class: 'nmi-tick' });
      tick.textContent = String(y); svg.append(tick);
    }
    for (const [y, text] of [[bounds[0], format(bounds[0])], [bounds[1], format(bounds[1])]]) {
      if (Math.abs(y) < 0.1 || Math.abs(y - 1) < 0.1) continue;
      const tick = svgElement('text', { x: PLOT.left - 9, y: sy(y) + (y === bounds[1] ? 5 : 0),
        'text-anchor': 'end', class: 'nmi-tick nmi-tick-muted' });
      tick.textContent = text; svg.append(tick);
    }
    const samples = [];
    const smoothSlopes = data.interpolation === 'smooth' ? slopes(data.points) : null;
    if (data.interpolation === 'hold') {
      for (let i = 0; i < data.points.length - 1; i++) {
        const [x0, y0] = data.points[i];
        samples.push([x0, y0], [data.points[i + 1][0], y0]);
        if (data.points[i + 1][1] !== y0) samples.push([data.points[i + 1][0], data.points[i + 1][1]]);
      }
    } else {
      for (let i = 0; i <= 200; i++) {
        const x = i / 200;
        samples.push([x, evaluate(data.points, data.interpolation, x, smoothSlopes)]);
      }
    }
    svg.append(svgElement('polyline', { points: samples.map(([x, y]) => `${sx(x)},${sy(y)}`).join(' '),
      class: 'nmi-curve-line' }));
    data.points.forEach(([x, y], index) => {
      const handle = svgElement('circle', { cx: sx(x), cy: sy(y), r: 16,
        class: index === selected ? 'nmi-handle nmi-handle-selected' : 'nmi-handle',
        tabindex: 0, role: 'button',
        'aria-label': `Keyframe ${index + 1}, time ${precise(x)}, weight ${precise(y)}. Arrow keys adjust.` });
      const tip = svgElement('title'); tip.textContent = `Keyframe ${index + 1}: time ${format(x)}, weight ${format(y)}`;
      handle.append(tip);
      handle.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        event.preventDefault(); event.stopPropagation();
        selected = index; dragging = { index, pointerId: event.pointerId };
        svg.setPointerCapture(event.pointerId);
        render();
      });
      handle.addEventListener('keydown', (event) => {
        const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Delete', 'Backspace', 'Enter', ' '];
        if (!keys.includes(event.key)) return;
        event.preventDefault(); event.stopPropagation();
        selected = index;
        if (event.key === 'Delete' || event.key === 'Backspace') {
          deleteSelected(); return;
        }
        if (event.key === 'Enter' || event.key === ' ') {
          render(); return;
        }
        const delta = event.shiftKey ? 0.001 : 0.01;
        changePoint(index,
          x + (event.key === 'ArrowLeft' ? -delta : event.key === 'ArrowRight' ? delta : 0),
          y + (event.key === 'ArrowUp' ? delta : event.key === 'ArrowDown' ? -delta : 0));
        svg.querySelectorAll('.nmi-handle')[index]?.focus();
      });
      svg.append(handle);
      svg.append(svgElement('circle', { cx: sx(x), cy: sy(y),
        r: index === selected ? 6.5 : 5.5,
        class: index === selected ? 'nmi-handle-visual nmi-handle-visual-selected' : 'nmi-handle-visual',
        'pointer-events': 'none', 'aria-hidden': 'true' }));
    });
  }
  function render() {
    const point = data.points[selected];
    pointName.textContent = `Keyframe ${selected + 1} / ${data.points.length}`;
    xInput.value = precise(point[0]);
    yInput.value = precise(point[1]);
    xInput.disabled = selected === 0 || selected === data.points.length - 1;
    remove.disabled = selected === 0 || selected === data.points.length - 1;
    add.disabled = data.points.length >= MAX_POINTS;
    mode.value = data.interpolation;
    preset.value = matchingPreset(data);
    draw();
  }
  svg.addEventListener('pointermove', (event) => {
    if (!dragging || dragging.pointerId !== event.pointerId) return;
    const point = pointerCoordinates(event);
    if (point) changePoint(dragging.index, ...point);
  });
  function endDrag(event) {
    if (!dragging || dragging.pointerId !== event.pointerId) return;
    dragging = null;
    if (svg.hasPointerCapture?.(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    fitBounds(); render();
  }
  svg.addEventListener('pointerup', endDrag);
  svg.addEventListener('pointercancel', endDrag);
  const editInspector = () => {
    const x = Number(xInput.value);
    const y = Number(yInput.value);
    if (!Number.isFinite(x) || !Number.isFinite(y) || y < -20 || y > 20 || x < 0 || x > 1) {
      render(); return;
    }
    changePoint(selected, x, y);
    fitBounds(); render();
  };
  xInput.addEventListener('change', editInspector);
  yInput.addEventListener('change', editInspector);
  add.addEventListener('click', () => {
    // Insert in the interval adjacent to the selected keyframe, preserving its
    // existing shape. If the end is selected, use the preceding interval.
    const left = Math.min(selected, data.points.length - 2);
    const x = (data.points[left][0] + data.points[left + 1][0]) / 2;
    addPoint(x, evaluate(data.points, data.interpolation, x));
  });
  remove.addEventListener('click', deleteSelected);
  preset.addEventListener('change', () => {
    const shape = PRESETS.find(([id]) => id === preset.value);
    if (!shape) return; // 'Custom' reports an edited shape; it is not an action.
    data.points = clonePoints(shape[2]);
    data.interpolation = shape[3] ?? 'linear';
    selected = 0;
    viewMode = 'auto';
    fitBounds(); commit(); render();
  });
  reset.addEventListener('click', () => {
    data.points = clonePoints(DEFAULT_POINTS);
    data.interpolation = 'linear';
    selected = 0;
    viewMode = 'auto';
    fitBounds(); commit(); render();
  });
  mode.addEventListener('change', () => {
    data.interpolation = mode.value;
    commit(); render();
  });
  fitBounds();
  widget = node.addDOMWidget(inputName, 'NORTHMERE_INSTRUMENTS_CURVE', root, {
    getValue: () => invalidSerializedValue ?? JSON.stringify(data),
    setValue: (value) => {
      try {
        data = parseCurve(value);
        invalidSerializedValue = null;
        validationMessage.hidden = true;
      } catch {
        // Preserve invalid or newer-version workflows instead of silently
        // replacing their serialized data with the default preset.
        invalidSerializedValue = typeof value === 'string' ? value : JSON.stringify(value);
        data = parseCurve(initialValue);
        validationMessage.hidden = false;
      }
      selected = 0;
      viewMode = 'auto';
      fitBounds(); render();
    },
    // The SVG follows node width; reserve matching vertical space when resized.
    getMinHeight: () => validationMessage.hidden ? 420 : 462,
    getHeight: () => Math.max(420, Math.ceil(420 +
      ((node.size?.[0] ?? 360) - 360) * 224 / 460)) + (validationMessage.hidden ? 0 : 42),
    hideOnZoom: false,
  });
  render();
  return { widget };
}

app.registerExtension({
  name: 'NorthmereInstruments.CurveEditor',
  getCustomWidgets() {
    return { NORTHMERE_INSTRUMENTS_CURVE: createCurveEditor };
  },
});
