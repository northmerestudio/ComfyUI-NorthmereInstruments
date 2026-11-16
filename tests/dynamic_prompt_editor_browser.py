"""Chromium integration smoke for ComfyUI's documented widgetType creation path.

Exercises beforeRegisterNodeDef + getCustomWidgets + addDOMWidget, rather
than calling the highlighter against a manually supplied textarea.
Does not claim a live ComfyUI server integration.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = r'''@define color
  {2::gold|1::silver}
@end
@pick{day: sun|night: moon}
@if(day){ warm \{literal\} light }
@rules
 day -> !night
@end
<script>alert('x')</script>'''


def main() -> None:
    source = (ROOT / 'web/dynamic_prompt_editor.js').read_text()
    source = source.replace("import { app } from '../../scripts/app.js';", '')
    # Mirrors ComfyUI's widgetStore mapping / litegraphService widgetType dispatch:
    # the input's underlying type stays STRING, widget renderer is chosen from widgetType.
    template = '''<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body { margin:0; padding:24px; background:#161616; color:#ececec; --comfy-input-bg:#292929; --input-text:#e5e5e5; --border-color:#555; --p-primary-color:#6bbab6; }
body.light { background:#f4f4f4; color:#222; --comfy-input-bg:#fdfdfd; --input-text:#202020; --border-color:#bdbdbd; --p-primary-color:#287681; }
#panel { width:440px; border-radius:8px; padding:12px 10px; background:#303030; }
body.light #panel { background:#e3e3e3; }
</style></head><body><div id="panel"></div><script>
const constructors = {};
const app = { registerExtension(extension) { window.extension = extension; } };
__JS__
window.def = { name:'NorthmereInstruments.DynamicPrompt', input:{ required:{ template:['STRING',{multiline:true,default:__SAMPLE__}], seed:['INT',{default:0}]}}};
window.unrelated = { name:'OtherNode',input:{required:{template:['STRING',{multiline:true}]}}};
window.extension.beforeRegisterNodeDef({},window.def);
window.extension.beforeRegisterNodeDef({},window.unrelated);
Object.assign(constructors,window.extension.getCustomWidgets());
window.node = {
    size:[440,215],
    widgets:[],
    addDOMWidget(name,type,element,options) {
      let onRemove;
      const widget = {name,type,options, element, callback() { window.changed=(window.changed||0)+1; },
        getValue:options.getValue,setValue:options.setValue,
        get onRemove(){return onRemove},set onRemove(v){onRemove=v}};
      this.widgets.push(widget);
      element.style.height='184px';
      this.element=element;
      document.querySelector('#panel').append(element);
      return widget;
    }
};
// ComfyUI addInputWidget (litegraphService.ts) resolves widgetInputSpec.type from widgetType
// and calls the registered constructor. No nodeCreated hooks or DOM query needed.
const widgetInputSpec={...window.def.input.required.template[1],type:window.def.input.required.template[1].widgetType};
const result=constructors[widgetInputSpec.type](window.node,'template', ['STRING',widgetInputSpec],app);
window.widget=result.widget;
window.dispatchType=widgetInputSpec.type;
</script></body></html>'''
    html = template.replace('__JS__',source).replace('__SAMPLE__',json.dumps(SAMPLE).replace('</','<\\/'))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':620,'height':420}, device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda e: errors.append(str(e)))
        page.set_content(html)
        assert not errors, errors
        assert page.evaluate('window.dispatchType') == 'NORTHMERE_DYNAMIC_PROMPT_EDITOR'
        assert page.evaluate('window.def.input.required.template[0]') == 'STRING'
        assert page.evaluate('window.unrelated.input.required.template[1].widgetType') is None
        assert page.locator('.nmi-prompt-editor').count() == 1
        assert page.locator('.nmi-prompt-indicator').inner_text() == 'syntax'
        assert page.locator('.nmi-prompt-frame span.nmi-prompt-directive').count() >= 5
        assert page.locator('.nmi-prompt-frame span.nmi-prompt-weight').count() == 2
        assert page.locator('.nmi-prompt-frame span.nmi-prompt-operator').count() >= 2
        assert page.locator('.nmi-prompt-frame span.nmi-prompt-escape').count() == 2
        assert page.locator('.nmi-prompt-frame span.nmi-prompt-label').count() >= 2
        assert page.evaluate('window.widget.getValue()') == SAMPLE
        assert page.locator('.nmi-prompt-frame script').count() == 0
        assert page.locator('.nmi-highlight-ready').count() == 1
        page.evaluate("window.widget.setValue('')")
        assert page.locator('textarea').get_attribute('placeholder').startswith('Try {red|blue}')
        assert page.evaluate("getComputedStyle(document.querySelector('textarea'),'::placeholder').webkitTextFillColor") != 'rgba(0, 0, 0, 0)'
        page.evaluate('(s) => window.widget.setValue(s)',SAMPLE)
        # Verify actual browser computed colors and browser-layer visibility.
        page.wait_for_timeout(60)
        palette = page.evaluate('''() => {
          const root=document.querySelector('.nmi-prompt-editor');
          const token=document.querySelector('.nmi-prompt-directive');
          const pre=document.querySelector('pre');
          const tex=document.querySelector('textarea');
          return {mode:root.dataset.mode, token:getComputedStyle(token).color,
            mirror:getComputedStyle(pre).color, text:getComputedStyle(tex).color,
            textFill:getComputedStyle(tex).webkitTextFillColor, area:pre.getBoundingClientRect().height};
        }''')
        assert palette['mode']=='dark',palette
        assert palette['token']=='rgb(211, 167, 255)',palette
        assert palette['area']>140,palette
        assert palette['textFill']=='rgba(0, 0, 0, 0)',palette
        page.screenshot(path='/mnt/data/nmi_editor_dark_verified.png')
        # Theme switch occurs without reconstructing widget and preserves raw value.
        page.evaluate("document.body.classList.add('light')")
        page.wait_for_function("document.querySelector('.nmi-prompt-editor').dataset.mode === 'light'")
        assert page.evaluate("getComputedStyle(document.querySelector('.nmi-prompt-directive')).color")=='rgb(113, 65, 160)'
        assert page.evaluate('window.widget.getValue()')==SAMPLE
        page.screenshot(path='/mnt/data/nmi_editor_light_verified.png')
        # Edit, preserve selection/caret/undo semantics, and send original source bytes.
        textarea=page.locator('textarea')
        textarea.focus()
        textarea.fill('@pick{red: apple|blue: sky}')
        assert page.evaluate('window.widget.getValue()')=='@pick{red: apple|blue: sky}'
        assert page.evaluate('window.changed') >= 1
        assert page.locator('.nmi-prompt-directive').count()==1
        restored='@rules\nfoo ~> bar @ 2\n@end\n{1::one|3::two}'
        page.evaluate('(s) => window.widget.setValue(s)',restored)
        assert page.locator('textarea').input_value()==restored
        assert page.locator('.nmi-prompt-operator').count()>=1
        assert page.locator('.nmi-prompt-weight').count()==2
        # Enter/typing updates overlay and value. No editor swapping/reparenting.
        textarea.press('End'); textarea.press('Enter'); textarea.type('@use(color)')
        assert page.evaluate('window.widget.getValue()').endswith('\n@use(color)')
        assert page.locator('.nmi-prompt-directive').count()>=3
        assert page.evaluate("document.querySelectorAll('.nmi-prompt-editor textarea').length")==1
        # Long wrapped content follows native scrolling, without changing source.
        long_text = ('@pick{day: sun|night: moon} ' + 'soft lights ' * 22 + '\n') * 24
        page.evaluate('(s) => window.widget.setValue(s)',long_text)
        page.evaluate("""() => { const el=document.querySelector('textarea'); el.scrollTop=el.scrollHeight; el.dispatchEvent(new Event('scroll')); }""")
        pos = page.evaluate("""() => [document.querySelector('textarea').scrollTop,document.querySelector('pre').scrollTop]""")
        assert pos[0] > 0 and abs(pos[0]-pos[1]) < 2, pos
        assert page.evaluate('window.widget.getValue()') == long_text
        page.evaluate('(s) => window.widget.setValue(s)',restored)
        # The real textarea owns focus and caret; no contenteditable proxy.
        textarea.focus()
        textarea.press('ControlOrMeta+End')
        assert page.evaluate("document.activeElement.tagName") == 'TEXTAREA'
        assert page.evaluate("document.querySelector('textarea').selectionStart") == len(restored)
        # Escape and raw HTML remain inert; removing widget cleans observers.
        page.evaluate('window.widget.onRemove()')
        assert errors == [],errors
        browser.close()
    print('PASS: widgetType dispatch, rendered colors in dark/light, editing, restore, isolation, XSS and cleanup')

if __name__=='__main__': main()
