const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('node:assert/strict');

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.events = {}; this.attributes = {};
    this.style = {}; this.classList = { add() {} }; this.value = ''; this.textContent = '';
    this.disabled = false;
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  append(...elements) { this.children.push(...elements); }
  replaceChildren(...elements) { this.children = elements; }
  addEventListener(type, fn) { (this.events[type] ??= []).push(fn); }
  dispatch(type, detail = {}) { for (const fn of this.events[type] ?? []) fn({ target: this, stopPropagation() {}, preventDefault() {}, ...detail }); }
  click() { this.dispatch('click'); }
  getScreenCTM() { return { inverse() { return {}; } }; }
  createSVGPoint() { return { x: 0, y: 0, matrixTransform() { return { x: this.x, y: this.y }; } }; }
  setPointerCapture(id) { this.capturedPointer = id; }
  hasPointerCapture(id) { return this.capturedPointer === id; }
  releasePointerCapture(id) { if (this.capturedPointer === id) this.capturedPointer = null; }
  querySelectorAll(selector) {
    const found = [];
    const walk = (item) => {
      if (item.className === selector.slice(1) || item.attributes.class?.split(' ').includes(selector.slice(1))) found.push(item);
      for (const child of item.children) walk(child);
    };
    walk(this); return found;
  }
}
const document = {
  head: new Element('head'),
  createElement: (tag) => new Element(tag),
  createElementNS: (_, tag) => new Element(tag),
  createSVGPoint: () => ({ x: 0, y: 0, matrixTransform() { return { x: this.x, y: this.y }; } }),
  getElementById: () => null,
};
let extension;
const app = { graph: { setDirtyCanvas() {} }, registerExtension(value) { extension = value; } };
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'curve.js'), 'utf8')
  .replace("import { app } from '../../scripts/app.js';", '')
  .replace('import.meta.url', "'file:///tmp/curve.js'");
vm.runInNewContext(source, { document, app, URL, console });
let changeCount = 0;
const node = {
  size: [360, 420],
  setDirtyCanvas() {},
  graph: { change() { changeCount += 1; } },
  addDOMWidget(name, type, root, options) {
    this.root = root;
    this.widget = { name, type, ...options, callback() {} };
    return this.widget;
  },
};
const makeCurve = (points, interpolation = 'linear') => JSON.stringify({ version: 1, interpolation, points });
const editor = extension.getCustomWidgets().NORTHMERE_INSTRUMENTS_CURVE;
editor(node, 'curve', [null, {}]);
const all = [];
const visit = (item) => { all.push(item); for (const child of item.children) visit(child); };
visit(node.root);
const byText = (tag, label) => all.find((e) => e.tagName === tag && e.textContent === label);
const buttons = all.filter((e) => e.tagName === 'button');
const controls = all.filter((e) => e.tagName === 'input');
const selectors = all.filter((e) => e.tagName === 'select');
const curve = () => JSON.parse(node.widget.getValue());

assert.equal(curve().points.length, 9, 'exactly nine default control points');
assert.equal(curve().points[0][0], 0);
assert.equal(curve().points.at(-1)[0], 1);
for (let index = 0; index < 9; index++) {
  assert.ok(Math.abs(curve().points[index][0] - index / 8) < 1e-12, 'default keyframes are evenly spaced');
}
assert.equal(selectors.length, 2, 'one preset selector and one interpolation selector');
const presetSelect = selectors[0];
const interpolationSelect = selectors[1];
assert.deepEqual(presetSelect.children.map((option) => option.textContent),
  ['Custom', 'Off', 'Full LoRA', 'Fade in · linear', 'Fade in · halfway', 'Quick rise']);
assert.equal(presetSelect.value, 'quick-rise');
assert.equal(presetSelect.children[0].disabled, true, 'Custom cannot be applied as a preset');
for (const [name, first, last] of [
  ['off', 0, 0], ['full', 1, 1], ['fade-in', 0, 1], ['early-fade-in', 0, 1],
]) {
  presetSelect.value = name; presetSelect.dispatch('change');
  assert.equal(curve().points.length, 9, `${name} keeps nine control points`);
  for (let index = 0; index < 9; index++) {
    assert.ok(Math.abs(curve().points[index][0] - index / 8) < 1e-12, `${name} has evenly spaced keyframes`);
  }
  assert.equal(curve().points[0][1], first);
  assert.equal(curve().points.at(-1)[1], last);
  assert.equal(curve().interpolation, 'linear');
  assert.equal(presetSelect.value, name, 'selected preset is retained after applying');
}
presetSelect.value = 'off'; presetSelect.dispatch('change');
assert.ok(curve().points.every(([, y]) => y === 0), 'off disables LoRA contribution across the envelope');
presetSelect.value = 'full'; presetSelect.dispatch('change');
assert.ok(curve().points.every(([, y]) => y === 1), 'full LoRA applies unit strength throughout');
presetSelect.value = 'fade-in'; presetSelect.dispatch('change');
assert.ok(curve().points.every(([x, y]) => Math.abs(x - y) < 1e-12), 'linear fade follows y=x');
presetSelect.value = 'early-fade-in'; presetSelect.dispatch('change');
assert.equal(curve().points[0][1], 0, 'early fade starts at zero');
assert.ok(Math.abs(curve().points[1][1] - (1 - Math.exp(-1)) / (1 - Math.exp(-4))) < 1e-12,
  'early fade uses normalized exponential ease out');
assert.ok(curve().points.slice(4).every(([, y]) => y === 1), 'early fade holds full strength after halfway');
const earlyFadeSaved = node.widget.getValue();
node.widget.setValue(earlyFadeSaved);
assert.equal(presetSelect.value, 'early-fade-in', 'early fade restores from saved curve');
presetSelect.value = 'quick-rise'; presetSelect.dispatch('change');
assert.equal(curve().points[1][1], 0.8);
const presetSaved = node.widget.getValue();
node.widget.setValue(presetSaved);
assert.equal(presetSelect.value, 'quick-rise', 'preset is recognized when restoring saved curve');

assert.deepEqual(buttons.map((e) => e.textContent), ['Reset', '+ Keyframe', 'Delete']);
assert.equal(controls.length, 2);
assert.equal(node.widget.getHeight(), 420, 'compact widget height');
node.size[0] = 600;
assert.ok(node.widget.getHeight() > 520, 'widget reserves more height when node gets wider');
node.size[0] = 360;
const stylesheet = fs.readFileSync(path.join(__dirname, '..', 'web', 'curve.css'), 'utf8');
assert.ok(!/\.nmi-plot\s*\{[^}]*max-height\s*:/s.test(stylesheet), 'plot is not height-capped');
assert.equal(byText('span', 'Keyframe 1 / 9').textContent, 'Keyframe 1 / 9');
assert.ok(!source.includes('Double-click graph to add'));

const saved = makeCurve([[0, -1], [0.5, 2], [1, 0]], 'hold');
node.widget.setValue(saved);
assert.deepEqual(curve().points, [[0, -1], [0.5, 2], [1, 0]]);
assert.equal(curve().interpolation, 'hold');
assert.equal(presetSelect.value, 'custom', 'restoring arbitrary saved curves is custom');
// Invalid or future-version values must never be silently overwritten.
const futureValue = JSON.stringify({ version: 2, points: [[0, 0], [1, 1]] });
node.widget.setValue(futureValue);
assert.equal(node.widget.getValue(), futureValue, 'invalid saved data is preserved verbatim');
assert.equal(all.find((e) => e.className === 'nmi-validation').hidden, false);
node.widget.setValue(saved);
assert.equal(all.find((e) => e.className === 'nmi-validation').hidden, true);

buttons.find((e) => e.textContent === '+ Keyframe').click();
assert.deepEqual(curve().points, [[0, -1], [0.25, -1], [0.5, 2], [1, 0]]);
assert.equal(byText('span', 'Keyframe 2 / 4').textContent, 'Keyframe 2 / 4');
controls[1].value = '-2'; controls[1].dispatch('change');
assert.equal(curve().points[1][1], -2);
assert.equal(presetSelect.value, 'custom', 'editing a preset creates a custom curve');
buttons.find((e) => e.textContent === 'Delete').click();
assert.deepEqual(curve().points, [[0, -1], [0.5, 2], [1, 0]]);
interpolationSelect.value = 'smooth'; interpolationSelect.dispatch('change');
assert.equal(curve().interpolation, 'smooth');
// A dragged handle must remain on the plot while Y automatically expands.
const chartSvg = all.find((e) => e.tagName === 'svg');
const firstHandle = chartSvg.querySelectorAll('.nmi-handle')[0];
firstHandle.dispatch('pointerdown', { button: 0, pointerId: 11 });
chartSvg.dispatch('pointermove', { pointerId: 11, clientX: 46, clientY: -500 });
const activeHandle = chartSvg.querySelectorAll('.nmi-handle')[0];
const activeCy = Number(activeHandle.attributes.cy);
assert.ok(curve().points[0][1] > 1.2, 'dragging can move outside initial Y range');
assert.ok(activeCy >= 15 && activeCy <= 190, 'Y autoscale keeps dragged handle visible');
chartSvg.dispatch('pointerup', { pointerId: 11 });
buttons.find((e) => e.textContent === 'Reset').click();
assert.equal(curve().points.length, 9);
assert.equal(curve().interpolation, 'linear');
assert.equal(presetSelect.value, 'quick-rise', 'reset restores the quick-rise preset');
assert.ok(changeCount >= 5);
console.log('PASS: 9-point default, envelope controls, restore, add/edit/remove, interpolation, reset and graph change notifications');
