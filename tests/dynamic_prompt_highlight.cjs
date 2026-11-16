const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.style = {}; this.dataset = {};
    this.listeners = {}; this.classList = { add: (name) => { this.className = name; } };
    this.offsetLeft = 0; this.offsetTop = 0; this.offsetWidth = 350; this.offsetHeight = 140;
    this.isConnected = true; this.scrollTop = 0; this.scrollLeft = 0; this.value = '';
  }
  append(child) { this.children.push(child); child.parentElement = this; }
  appendChild(child) { this.append(child); }
  insertBefore(child, next) { this.children.splice(this.children.indexOf(next), 0, child); child.parentElement = this; }
  replaceChildren(...items) { this.children = items.flatMap((e) => e.fragment ? e.children : [e]); }
  setAttribute() {}
  addEventListener(name, callback) { (this.listeners[name] ??= []).push(callback); }
  emit(name) { for (const cb of this.listeners[name] ?? []) cb(); }
}
class Textarea extends Element { constructor() { super('textarea'); } }
const body = new Element('body'), head = new Element('head'), html = new Element('html');
const document = {
  body, head, documentElement: html,
  getElementById: () => null,
  createElement: (name) => new Element(name),
  createTextNode: (value) => ({ textContent: value }),
  createDocumentFragment: () => { const e = new Element('fragment'); e.fragment = true; return e; },
};
let dark = false, extension;
const getComputedStyle = (node) => ({
  position: 'static', color: dark ? 'rgb(230, 230, 230)' : 'rgb(30, 30, 30)',
  backgroundColor: dark ? 'rgb(40, 40, 40)' : 'rgb(245, 245, 245)',
  fontFamily: 'monospace', fontSize: '14px', fontWeight: '400', fontStyle: 'normal',
  lineHeight: '20px', letterSpacing: 'normal', tabSize: '4', textAlign: 'left',
  padding: '8px', borderWidth: '1px', borderStyle: 'solid', boxSizing: 'border-box',
});
const observers = [];
class Observer { constructor(callback) { this.callback = callback; observers.push(this); } observe() {} disconnect() {} }
const app = { registerExtension(e) { extension = e; } };
const source = fs.readFileSync(path.join(__dirname, '..', 'web/dynamic_prompt_highlight.js'), 'utf8')
  .replace("import { app } from '../../scripts/app.js';", '')
  .replace('import.meta.url', "'file:///tmp/dynamic_prompt_highlight.js'");
vm.runInNewContext(source, {
  app, document, HTMLTextAreaElement: Textarea, getComputedStyle,
  MutationObserver: Observer, ResizeObserver: Observer,
  requestAnimationFrame: (cb) => cb(), URL,
});
const root = new Element('div'); body.append(root);
const input = new Textarea(); root.append(input);
const widget = { name: 'template', element: input, setValue(value) { input.value = value; } };
extension.nodeCreated({ comfyClass: 'OtherNode', widgets: [widget] });
assert.equal(root.children.length, 1, 'other nodes are untouched');
extension.nodeCreated({ comfyClass: 'NorthmereInstruments.DynamicPrompt', widgets: [widget] });
assert.equal(root.children.length, 2);
const highlight = root.children[0];
assert.equal(highlight.dataset.theme, 'light');
assert.equal(highlight.style.color, 'rgb(30, 30, 30)');
input.value = '@pick{day: sunny|night: moonlit} \\{literal} <b>& text 3::red @if(day){yes}';
input.emit('input');
const parts = highlight.children.map((e) => [e.className ?? '', e.textContent]);
assert.ok(parts.some(([kind, text]) => kind === 'nmi-prompt-directive' && text === '@pick'));
assert.ok(parts.some(([kind, text]) => kind === 'nmi-prompt-weight' && text === '3'));
assert.ok(parts.some(([kind, text]) => kind === 'nmi-prompt-escape' && text === '\\{'));
assert.ok(parts.some(([kind, text]) => kind === 'nmi-prompt-operator' && text === '::') === false);
assert.equal(parts.map(([, text]) => text).join(''), input.value + '\n');
assert.ok(parts.some(([, text]) => text.includes('<b>& text')), 'plain text is never interpreted as markup');
input.scrollTop = 27; input.scrollLeft = 3; input.emit('scroll');
assert.equal(highlight.scrollTop, 27); assert.equal(highlight.scrollLeft, 3);
widget.setValue('@rules\nday -> !night\n@end');
assert.equal(highlight.children.map((c) => c.textContent).join(''), input.value + '\n');
dark = true; observers.forEach((observer) => observer.callback());
assert.equal(highlight.dataset.theme, 'dark');
assert.equal(highlight.style.color, 'rgb(230, 230, 230)');
assert.equal(input.style.caretColor, 'rgb(230, 230, 230)');
console.log('Dynamic prompt frontend smoke: passed');
