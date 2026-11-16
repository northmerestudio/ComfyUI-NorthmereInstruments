import { app } from '../../scripts/app.js';

// ComfyUI's widgetType selects a renderer without changing the STRING input/socket.
// Registering at node-definition time is deterministic; no nodeCreated/DOM polling.
const NODE_ID = 'NorthmereInstruments.DynamicPrompt';
const WIDGET_TYPE = 'NORTHMERE_DYNAMIC_PROMPT_EDITOR';
const STYLE_ID = 'nmi-dynamic-prompt-editor-style';

if (!document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
.nmi-prompt-editor, .nmi-prompt-editor * { box-sizing: border-box; }
.nmi-prompt-editor { width:100%; height:100%; min-height:0; display:flex; flex-direction:column; gap:5px; padding:4px 6px 5px; color:var(--input-text,#e9e9e9); font:11px/1.35 system-ui,sans-serif; }
.nmi-prompt-toolbar { min-height:15px; display:flex; align-items:center; justify-content:space-between; gap:8px; user-select:none; }
.nmi-prompt-title { font-weight:600; opacity:.78; }
.nmi-prompt-indicator { font-size:10px; letter-spacing:.02em; opacity:.72; white-space:nowrap; }
.nmi-prompt-indicator::before { content:''; display:inline-block; width:5px; height:5px; margin-right:5px; vertical-align:2px; border-radius:50%; background:var(--p-primary-color,#70a8a8); }
.nmi-prompt-frame { position:relative; flex:1 1 auto; min-height:0; overflow:hidden; border-radius:5px; }
.nmi-prompt-frame textarea, .nmi-prompt-frame pre { position:absolute; inset:0; display:block; width:100%; height:100%; margin:0; padding:9px 10px; border:1px solid transparent; border-radius:5px; font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,'Liberation Mono',monospace; letter-spacing:0; text-align:left; tab-size:4; white-space:pre-wrap; overflow-wrap:break-word; word-break:normal; }
.nmi-prompt-frame pre { z-index:0; overflow:hidden; pointer-events:none; color:var(--input-text,#e9e9e9); background:transparent; }
.nmi-prompt-frame textarea { z-index:1; resize:none; overflow:auto; background:var(--comfy-input-bg,var(--input-bg,#242424)); border-color:var(--border-color,#555); color:var(--input-text,#e9e9e9); outline:none; caret-color:var(--input-text,#e9e9e9); }
.nmi-prompt-editor.nmi-highlight-ready textarea { background:transparent; color:transparent; -webkit-text-fill-color:transparent; }
.nmi-prompt-editor.nmi-highlight-ready .nmi-prompt-frame { background:var(--comfy-input-bg,var(--input-bg,#242424)); }
.nmi-prompt-editor textarea:focus-visible { border-color:var(--p-primary-color,#6bbab6); box-shadow:inset 0 0 0 1px var(--p-primary-color,#6bbab6); }
.nmi-prompt-editor textarea::selection { background:rgba(97,143,215,.35); }
.nmi-prompt-editor textarea::placeholder { color:var(--input-text,#e9e9e9); -webkit-text-fill-color:var(--input-text,#e9e9e9); opacity:.48; }
.nmi-prompt-editor[data-mode='dark'] { --nmi-directive:#d3a7ff; --nmi-punct:#77ccd8; --nmi-weight:#ffc182; --nmi-operator:#ffa0b1; --nmi-label:#9bd9a3; --nmi-escape:#aab8c8; }
.nmi-prompt-editor[data-mode='light'] { --nmi-directive:#7141a0; --nmi-punct:#176f83; --nmi-weight:#99531e; --nmi-operator:#a63854; --nmi-label:#286a39; --nmi-escape:#62717d; }
.nmi-prompt-directive { color:var(--nmi-directive); font-weight:600; }
.nmi-prompt-punct { color:var(--nmi-punct); }
.nmi-prompt-weight { color:var(--nmi-weight); font-weight:600; }
.nmi-prompt-operator { color:var(--nmi-operator); font-weight:600; }
.nmi-prompt-label { color:var(--nmi-label); }
.nmi-prompt-escape { color:var(--nmi-escape); }
`;
  document.head.append(style);
}

// Token colors are decoration only. The textarea remains the input of record.
// Never interpret template text as HTML, even when prompts contain markup.
const TOKENS = /\\[\\{}|@:]|@(pick|if|use|define|rules|end)\b|(?:\d+(?:\.\d*)?|\.\d+)(?=\s*::)|::|~>|->|[{}|]|\b[A-Za-z_][\w-]*(?=\s*:)|!(?=[A-Za-z_])/g;
function tokenKind(text) {
  if (text[0] === '\\') return 'escape';
  if (text[0] === '@') return 'directive';
  if (/^(?:\d|\.)/.test(text)) return 'weight';
  if (/^(?:~>|->|!)$/.test(text)) return 'operator';
  if (/^[A-Za-z_]/.test(text)) return 'label';
  return 'punct';
}
function render(source, target) {
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  TOKENS.lastIndex = 0;
  for (const match of source.matchAll(TOKENS)) {
    if (match.index > cursor) fragment.append(document.createTextNode(source.slice(cursor, match.index)));
    const span = document.createElement('span');
    span.className = `nmi-prompt-${tokenKind(match[0])}`;
    span.textContent = match[0];
    fragment.append(span);
    cursor = match.index + match[0].length;
  }
  fragment.append(document.createTextNode(source.slice(cursor) + '\n'));
  target.replaceChildren(fragment);
}

function createEditor(node, name, inputData) {
  const opts = Array.isArray(inputData) ? (inputData[1] ?? {}) : (inputData?.options ?? inputData ?? {});
  const root = document.createElement('div');
  root.className = 'nmi-prompt-editor';
  root.dataset.mode = 'dark';

  const toolbar = document.createElement('div');
  toolbar.className = 'nmi-prompt-toolbar';
  const title = document.createElement('span');
  title.className = 'nmi-prompt-title';
  title.textContent = name;
  const indicator = document.createElement('span');
  indicator.className = 'nmi-prompt-indicator';
  indicator.textContent = 'syntax';
  toolbar.append(title, indicator);

  const frame = document.createElement('div');
  frame.className = 'nmi-prompt-frame';
  const mirror = document.createElement('pre');
  mirror.setAttribute('aria-hidden', 'true');
  const textarea = document.createElement('textarea');
  textarea.className = 'comfy-multiline-input';
  textarea.dataset.testid = 'dom-widget-textarea';
  textarea.setAttribute('aria-label', 'Dynamic prompt template');
  textarea.placeholder = opts.placeholder || 'Try {red|blue}, @pick{day: sun|night: moon}, @if(day){warm}';
  textarea.value = opts.default ?? '';
  textarea.spellcheck = false;
  frame.append(mirror, textarea);
  root.append(toolbar, frame);

  function syncGeometry() {
    // Native textarea scrollbars subtract from its content area on some OSes.
    // Reserve that space in the mirror to keep line wrapping aligned.
    const scrollbar = Math.max(0, textarea.offsetWidth - textarea.clientWidth - 2);
    mirror.style.paddingRight = `${10 + scrollbar}px`;
    mirror.scrollTop = textarea.scrollTop;
    mirror.scrollLeft = textarea.scrollLeft;
  }
  function syncTheme() {
    const value = getComputedStyle(frame).backgroundColor;
    const bg = getComputedStyle(textarea).backgroundColor;
    const color = bg === 'rgba(0, 0, 0, 0)' ? value : bg;
    const rgba = color.match(/[\d.]+/g)?.map(Number) ?? [];
    if (rgba.length >= 3) {
      const brightness = rgba[0] * 0.2126 + rgba[1] * 0.7152 + rgba[2] * 0.0722;
      root.dataset.mode = brightness > 140 ? 'light' : 'dark';
    }
  }
  function paint() {
    render(textarea.value, mirror);
    syncGeometry();
    // Only hide the native text after a highlight layer has been painted.
    root.classList.add('nmi-highlight-ready');
  }
  textarea.addEventListener('input', () => {
    paint();
    widget.callback?.(textarea.value);
  });
  textarea.addEventListener('scroll', syncGeometry, { passive:true });
  const options = {
    getValue: () => textarea.value,
    setValue: (value) => { textarea.value = String(value ?? ''); paint(); },
    getMinHeight: () => 178,
    getHeight: () => 184,
    hideOnZoom: true,
  };
  const widget = node.addDOMWidget(name, 'customtext', root, options);
  widget.options ??= {};
  widget.options.minNodeSize = [360, 215];
  const resize = new ResizeObserver(() => { syncGeometry(); syncTheme(); });
  resize.observe(root);
  const theme = new MutationObserver(syncTheme);
  for (const el of [document.documentElement, document.body]) {
    theme.observe(el, { attributes:true, attributeFilter:['class','style','data-theme'] });
  }
  const oldRemove = widget.onRemove;
  widget.onRemove = (...args) => { resize.disconnect(); theme.disconnect(); return oldRemove?.apply(widget, args); };
  paint();
  requestAnimationFrame(syncTheme);
  return { widget, minWidth:360, minHeight:215 };
}

// V3 node definitions are not guaranteed to use the legacy nodeData.name shape.
// Apply the renderer selection BEFORE node registration, when definitions are
// still mutable. The STRING socket type and backend execution stay unchanged.
function patchDefinition(def, key) {
  if (!def || ![NODE_ID, 'Northmere Prompt v3'].includes(key) &&
      ![NODE_ID, 'Northmere Prompt v3'].includes(def.name) &&
      ![NODE_ID, 'Northmere Prompt v3'].includes(def.node_id) &&
      ![NODE_ID, 'Northmere Prompt v3'].includes(def.display_name)) return false;
  let changed = false;
  const input = def.input;
  for (const section of [input?.required, input?.optional]) {
    const spec = section?.template;
    if (Array.isArray(spec) && spec[0] === 'STRING') {
      spec[1] ??= {};
      spec[1].widgetType = WIDGET_TYPE;
      changed = true;
    }
  }
  // Some V3 definitions expose an inputs array prior to legacy conversion.
  for (const spec of def.inputs ?? []) {
    if (spec?.name === 'template' && (spec.type === 'STRING' || spec.type === 'string')) {
      spec.widgetType = WIDGET_TYPE;
      spec.widget_type = WIDGET_TYPE;
      changed = true;
    }
  }
  return changed;
}

app.registerExtension({
  name: 'NorthmereInstruments.DynamicPromptEditor',
  getCustomWidgets() {
    return { [WIDGET_TYPE]: createEditor };
  },
  addCustomNodeDefs(defs) {
    for (const [key, def] of Object.entries(defs)) patchDefinition(def, key);
  },
  beforeRegisterNodeDef(_nodeType, nodeData) {
    patchDefinition(nodeData);
  },
});
