import { app } from '../../scripts/app.js';

// Paint syntax behind ComfyUI's native textarea; it remains the only editor and
// the only source of widget values (including undo, IME and workflow saves).
const NODE_ID = 'NorthmereInstruments.DynamicPrompt';
const STYLE_ID = 'northmere-dynamic-prompt-highlight-css';
if (!document.getElementById(STYLE_ID)) {
  const link = document.createElement('link');
  link.id = STYLE_ID;
  link.rel = 'stylesheet';
  link.href = new URL('./dynamic_prompt_highlight.css', import.meta.url).href;
  document.head.append(link);
}

const TOKENS = /\\[\\{}|@:]|@(pick|if|use|define|rules|end)\b|(?:\d+(?:\.\d*)?|\.\d+)(?=\s*::)|::|~>|->|[{}|]|!(?=[A-Za-z])/g;
const tokenClass = (token) => token[0] === '\\' ? 'escape'
  : token[0] === '@' && /[A-Za-z]/.test(token[1] ?? '') ? 'directive'
    : /^(?:\d|\.)/.test(token) ? 'weight'
      : /^(?:->|~>|!)$/.test(token) ? 'operator' : 'delimiter';

function paint(source, target) {
  const fragment = document.createDocumentFragment();
  let offset = 0;
  TOKENS.lastIndex = 0;
  for (const match of source.matchAll(TOKENS)) {
    if (match.index > offset) fragment.append(document.createTextNode(source.slice(offset, match.index)));
    const span = document.createElement('span');
    span.className = `nmi-prompt-${tokenClass(match[0])}`;
    span.textContent = match[0]; // Never interpret user prompt text as HTML.
    fragment.append(span);
    offset = match.index + match[0].length;
  }
  fragment.append(document.createTextNode(source.slice(offset) + '\n')); // Preserve final empty line height.
  target.replaceChildren(fragment);
}

function attach(widget) {
  const textarea = widget?.element ?? widget?.inputEl;
  if (!(textarea instanceof HTMLTextAreaElement) || textarea.dataset.nmiPromptHighlight) return;
  // ComfyUI may create the widget before Vue mounts its textarea into the DOM.
  // The previous one-shot parent check silently skipped highlighting forever.
  if (!textarea.isConnected || !textarea.parentElement) {
    const mounted = new MutationObserver(() => {
      if (!textarea.isConnected || !textarea.parentElement) return;
      mounted.disconnect();
      attach(widget);
    });
    mounted.observe(document.documentElement, { childList: true, subtree: true });
    return;
  }
  const parent = textarea.parentElement;
  const originalCSS = getComputedStyle(textarea);
  const originalColor = originalCSS.color;
  textarea.dataset.nmiPromptHighlight = 'true';
  const originalPosition = parent.style.position;
  if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';
  const layer = document.createElement('pre');
  layer.className = 'nmi-prompt-highlight';
  layer.setAttribute('aria-hidden', 'true');
  parent.insertBefore(layer, textarea);
  if (originalCSS.position === 'static') textarea.style.position = 'relative';
  textarea.classList.add('nmi-prompt-source');

  function syncLayout() {
    const css = getComputedStyle(textarea);
    // Match the browser's textarea typography, padding and wrap geometry.
    for (const prop of ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'lineHeight',
      'letterSpacing', 'tabSize', 'textAlign', 'padding', 'borderWidth', 'borderStyle',
      'boxSizing']) layer.style[prop] = css[prop];
    layer.style.left = `${textarea.offsetLeft}px`;
    layer.style.top = `${textarea.offsetTop}px`;
    layer.style.width = `${textarea.offsetWidth}px`;
    layer.style.height = `${textarea.offsetHeight}px`;
    layer.scrollTop = textarea.scrollTop;
    layer.scrollLeft = textarea.scrollLeft;
    // Derive the palette from the actual field text color, not OS preference:
    // ComfyUI themes can be switched independently of prefers-color-scheme.
    const textColor = getComputedStyle(parent).color || originalColor;
    const rgb = textColor.match(/[\d.]+/g)?.slice(0, 3).map(Number) ?? [0, 0, 0];
    const bright = (rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722) > 140;
    layer.dataset.theme = bright ? 'dark' : 'light';
    layer.style.color = textColor;
    textarea.style.caretColor = textColor;
  }
  let queued = false;
  const update = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      if (!textarea.isConnected) return;
      paint(textarea.value, layer);
      syncLayout();
    });
  };
  textarea.addEventListener('input', update);
  textarea.addEventListener('scroll', () => {
    layer.scrollTop = textarea.scrollTop;
    layer.scrollLeft = textarea.scrollLeft;
  }, { passive: true });
  const resize = new ResizeObserver(syncLayout);
  resize.observe(textarea);
  const theme = new MutationObserver(syncLayout);
  for (const element of [document.documentElement, document.body]) {
    theme.observe(element, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });
  }
  // Widget values can also be restored or set programmatically without input.
  const setValue = widget.setValue;
  if (typeof setValue === 'function') {
    widget.setValue = function (...args) {
      const result = setValue.apply(this, args);
      update();
      return result;
    };
  }
  update();
  // Remove observers when ComfyUI disposes of the DOM widget.
  const disconnect = new MutationObserver(() => {
    if (textarea.isConnected) return;
    resize.disconnect();
    theme.disconnect();
    disconnect.disconnect();
    if (parent.style.position === 'relative' && originalPosition !== 'relative') {
      parent.style.position = originalPosition;
    }
  });
  disconnect.observe(parent.parentElement ?? parent, { childList: true, subtree: true });
}

app.registerExtension({
  name: 'NorthmereInstruments.DynamicPromptHighlight',
  nodeCreated(node) {
    if (node.comfyClass !== NODE_ID) return;
    const widget = node.widgets?.find((item) => item.name === 'template');
    if (widget) attach(widget);
  },
});
