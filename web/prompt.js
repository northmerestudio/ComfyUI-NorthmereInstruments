import { app } from '../../scripts/app.js';

// Exactly the same V3 custom widget integration as the working LoRA curve.
// The backend still receives an unmodified STRING called "template".
const stylesheet = document.createElement('link');
stylesheet.rel = 'stylesheet';
stylesheet.href = new URL('./prompt.css', import.meta.url).href;
document.head.append(stylesheet);

const DIRECTIVE = /^@(pick|if|use|define|rules|end)\b/;
const IDENT = /^[A-Za-z][\w.-]*/;
const NUMBER = /^(?:\d+(?:\.\d*)?|\.\d+)/;

// Escaping is explicit: never interpolate user text into HTML.
export function tokens(text) {
  const out = [];
  let i = 0;
  const emit = (kind, value) => {
    if (out.length && out[out.length - 1].kind === kind) out[out.length - 1].value += value;
    else out.push({ kind, value });
  };
  while (i < text.length) {
    // Comment syntax applies only at the start of a physical line.
    if (i === 0 || text[i - 1] === '\n') {
      const line = text.slice(i).match(/^[ \t]*\/\/[^\r\n]*/);
      if (line) { emit('comment', line[0]); i += line[0].length; continue; }
    }
    const rest = text.slice(i);
    if (rest[0] === '\\' && rest.length > 1) {
      emit('escape', rest.slice(0, 2)); i += 2; continue;
    }
    const directive = rest.match(DIRECTIVE);
    if (directive) {
      emit('directive', directive[0]); i += directive[0].length; continue;
    }
    const weight = rest.match(NUMBER);
    if (weight && /^\s*::/.test(rest.slice(weight[0].length))) {
      const whole = rest.match(/^(?:\d+(?:\.\d*)?|\.\d+)\s*::/)[0];
      emit('weight', whole); i += whole.length; continue;
    }
    if (rest.startsWith('->') || rest.startsWith('~>')) {
      emit('operator', rest.slice(0, 2)); i += 2; continue;
    }
    if ('{}|()'.includes(rest[0])) {
      emit('delimiter', rest[0]); i++; continue;
    }
    // Label definitions in @pick and fact names in rules are subtly distinguished.
    const identifier = rest.match(IDENT);
    if (identifier && /^[ \t]*(?:,\s*[A-Za-z][\w.-]*\s*)*:/.test(rest.slice(identifier[0].length))) {
      emit('label', identifier[0]); i += identifier[0].length; continue;
    }
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
  textarea.placeholder = 'Write a prompt…  {red|blue}  @pick{day: sunny|night: moonlit}';
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
