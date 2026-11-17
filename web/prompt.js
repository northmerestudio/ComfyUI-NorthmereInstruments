import { app } from '../../scripts/app.js';

// Exactly the same custom widget integration and DOM-backed editor.
// The backend still receives an unmodified STRING called "template".
const stylesheet = document.createElement('link');
stylesheet.rel = 'stylesheet';
stylesheet.href = new URL('./prompt.css', import.meta.url).href;
document.head.append(stylesheet);

const IDENT = /^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*/;
const KEYWORD = /^(choose|let|const|fn|require|prefer)\b/;
const WEIGHT = /^@(?:\d+(?:\.\d+)?)/;
const NUMBER = /^\d+(?:\.\d+)?\b/;
const BUILTIN = /^(?:upper|lower|title|str|int|float|bool|len|abs|min|max|round)\b/;
const TYPE = /^(?:text|int|float|bool)\b/;
const EXPRESSION_KEYWORD = /^(?:if|else|and|or|not)\b/;

// Highlighting is deliberately forgiving. The Python parser is authoritative.
// Return alternating tokens with the same total text to avoid caret/mirror drift.
export function tokens(source) {
  const out = [];
  let i = 0;
  let header = true;
  let headerDepth = 0;
  let expressionDepth = 0;
  const emit = (kind, value) => {
    if (out.length && out[out.length - 1].kind === kind) out[out.length - 1].value += value;
    else out.push({ kind, value });
  };
  while (i < source.length) {
    const startLine = i === 0 || source[i - 1] === '\n';
    const rest = source.slice(i);
    if (startLine && header && headerDepth === 0 && !/^[ \t]*(?:#|$|\r?$)/.test(rest) &&
        !/^[ \t]*(?:choose|let|const|fn|require|prefer)\b/.test(rest) &&
        !/^[ \t]*[|}]/.test(rest)) header = false;
    if (startLine) {
      const comment = rest.match(header ? /^[ \t]*#[^\r\n]*/ : /^[ \t]*\/\/[^\r\n]*/);
      if (comment) { emit('comment', comment[0]); i += comment[0].length; continue; }
    }
    if (rest[0] === '\\' && rest.length > 1) {
      emit('escape', rest.slice(0, 2)); i += 2; continue;
    }
    if (header && (startLine || /^[ \t]/.test(source[i - 1] ?? ''))) {
      const keyword = rest.match(KEYWORD);
      if (keyword) { emit('directive', keyword[0]); i += keyword[0].length; continue; }
    }
    const linePrefix = source.slice(source.lastIndexOf('\n', i - 1) + 1, i);
    if (header && /^(?:[ \t]*)(?:choose|let|const|fn)[ \t]+$/.test(linePrefix)) {
      const name = rest.match(IDENT);
      if (name) { emit(linePrefix.trim() === 'fn' ? 'function' : 'variable', name[0]); i += name[0].length; continue; }
    }
    if (header && /^[ \t]*fn[ \t]+[A-Za-z][\w.]*[ \t]*\([^\n]*[,(][ \t]*$/.test(linePrefix)) {
      const argument = rest.match(/^[A-Za-z][A-Za-z0-9_]*(?=[ \t]*:)/);
      if (argument) { emit('variable', argument[0]); i += argument[0].length; continue; }
    }
    if (header && /:[ \t]*$/.test(linePrefix)) {
      const type = rest.match(TYPE);
      if (type) { emit('type', type[0]); i += type[0].length; continue; }
    }
    {
      // Quoted strings are code only in declarations, expressions, or call arguments.
      // Apostrophes in prompt prose (e.g. "sculptor's") must stay plain text.
      const codeString = expressionDepth > 0 || (header && headerDepth === 0 &&
        /^[ \t]*(?:const|fn)\b[^\r\n]*=/.test(linePrefix)) ||
        /\$[A-Za-z][\w.]*(?:[ \t]*\([^\r\n)]*)$/.test(linePrefix);
      const string = codeString && rest.match(/^(?:"(?:[^"\\\r\n]|\\.)*"|'(?:[^'\\\r\n]|\\.)*')/);
      if (string) { emit('string', string[0]); i += string[0].length; continue; }
      const number = (header || expressionDepth) && rest.match(NUMBER);
      if (number) { emit('number', number[0]); i += number[0].length; continue; }
      if ((header || expressionDepth) && /^(?:true|false|True|False)\b/.test(rest)) {
        const value = rest.match(/^(?:true|false|True|False)\b/)[0];
        emit('boolean', value); i += value.length; continue;
      }
    }
    if (expressionDepth && EXPRESSION_KEYWORD.test(rest)) {
      const keyword = rest.match(EXPRESSION_KEYWORD)[0];
      emit('directive', keyword); i += keyword.length; continue;
    }
    if (expressionDepth && BUILTIN.test(rest) && /^[ \t]*\(/.test(rest.slice(rest.match(BUILTIN)[0].length))) {
      const name = rest.match(BUILTIN)[0];
      emit('function', name); i += name.length; continue;
    }
    if (rest.startsWith('=>') || rest.startsWith('->')) {
      emit('operator', rest.slice(0, 2)); i += 2; continue;
    }
    if (rest.startsWith('${')) { expressionDepth++; emit('delimiter', '${'); i += 2; continue; }
    if (rest[0] === '$' || rest[0] === '?') {
      const name = rest.slice(1).match(IDENT);
      if (name && (rest[0] === '$' || rest[1 + name[0].length] === '{')) {
        const isFunction = rest[0] === '$' && /^[ \t]*\(/.test(rest.slice(1 + name[0].length));
        emit(rest[0] === '?' ? 'condition' : isFunction ? 'function' : 'reference', rest[0] + name[0]);
        i += 1 + name[0].length; continue;
      }
    }
    const weight = rest.match(WEIGHT);
    if (weight && i > 0 && /\s/.test(source[i - 1])) {
      emit('weight', weight[0]); i += weight[0].length; continue;
    }
    if (rest[0] === '=') { emit('operator', '='); i++; continue; }
    if ('{}|&!():,+-*/'.includes(rest[0])) {
      if (header && rest[0] === '{') headerDepth++;
      if (header && rest[0] === '}') headerDepth = Math.max(0, headerDepth - 1);
      if (rest[0] === '}' && expressionDepth) expressionDepth--;
      emit('delimiter', rest[0]); i++; continue;
    }
    const tag = rest.match(/^([A-Za-z][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z][A-Za-z0-9_]*)*|_)(?=\s*=>)/);
    if (tag) { emit('label', tag[0]); i += tag[0].length; continue; }
    emit('plain', rest[0]); i++;
  }
  return out;
}

function createPromptEditor(node, inputName) {
  const root = document.createElement('div');
  root.className = 'nmi-prompt';
  const header = document.createElement('div');
  header.className = 'nmi-prompt-header';
  const title = document.createElement('span');
  title.textContent = inputName;
  const hint = document.createElement('span');
  hint.className = 'nmi-prompt-indicator';
  hint.textContent = 'syntax';
  header.append(title, hint);

  const surface = document.createElement('div');
  surface.className = 'nmi-prompt-surface';
  // ComfyUI's DOM canvas forwards uncaptured wheel events to canvas zoom.
  surface.setAttribute('data-capture-wheel', 'true');
  const mirror = document.createElement('div');
  mirror.className = 'nmi-prompt-mirror';
  mirror.setAttribute('aria-hidden', 'true');
  const text = document.createElement('div');
  text.className = 'nmi-prompt-highlight';
  mirror.append(text);
  const textarea = document.createElement('textarea');
  textarea.className = 'nmi-prompt-input';
  textarea.spellcheck = false;
  textarea.wrap = 'off';
  textarea.autocapitalize = 'off';
  textarea.autocomplete = 'off';
  textarea.setAttribute('aria-label', 'Northmere prompt template');
  textarea.placeholder = 'Write a prompt…  {red | blue}  {day => sunny | night => moonlit}';
  surface.append(mirror, textarea);
  root.append(header, surface);

  function paint() {
    const fragment = document.createDocumentFragment();
    for (const token of tokens(textarea.value)) {
      const span = document.createElement('span');
      span.className = `nmi-prompt-${token.kind}`;
      span.textContent = token.value;
      fragment.append(span);
    }
    // Extra final line box ensures newline/selection alignment.
    text.replaceChildren(fragment, document.createTextNode('\u200b'));
    syncScroll();
  }
  function syncScroll() {
    text.style.transform = `translate(${-textarea.scrollLeft}px, ${-textarea.scrollTop}px)`;
  }
  let widget;
  textarea.addEventListener('input', () => {
    paint();
    widget?.callback?.(textarea.value);
    node.graph?.change?.();
    node.setDirtyCanvas?.(true, true);
  });
  textarea.addEventListener('scroll', syncScroll, { passive: true });
  // Keep wheel scrolling native (including trackpad horizontal scroll), not canvas zoom.
  surface.addEventListener('wheel', (event) => event.stopPropagation(), { passive: true });
  // Tab inserts indentation rather than moving focus away from the code editor.
  textarea.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab' || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    event.preventDefault();
    textarea.setRangeText('    ', textarea.selectionStart, textarea.selectionEnd, 'end');
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  });
  // Escape remains an editable native text field, including IME/undo/selection.
  widget = node.addDOMWidget(inputName, 'NORTHMERE_INSTRUMENTS_PROMPT', root, {
    getValue: () => textarea.value,
    setValue: (value) => { textarea.value = typeof value === 'string' ? value : String(value ?? ''); paint(); },
    getMinHeight: () => 230,
    getHeight: () => 260,
    hideOnZoom: false,
  });
  paint();
  return { widget };
}

app.registerExtension({
  name: 'NorthmereInstruments.PromptEditor',
  getCustomWidgets() {
    return { NORTHMERE_INSTRUMENTS_PROMPT: createPromptEditor };
  },
});
