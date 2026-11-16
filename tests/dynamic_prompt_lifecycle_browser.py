"""Exercise actual browser lifecycle cases, without pre-seeding node widgets."""
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
js = (root / 'web/dynamic_prompt_highlight.js').read_text().replace("import { app } from '../../scripts/app.js';", '').replace('import.meta.url', "'file:///tmp/dynamic_prompt_highlight.js'")
css = (root / 'web/dynamic_prompt_highlight.css').read_text()
html = '''<!doctype html><meta charset="utf-8"><style> body{font:14px sans-serif} .slot{position:relative;margin:8px;width:400px;height:120px;color:#ddd} textarea{width:380px;height:110px;font:14px/20px monospace;padding:8px;background:#222;color:#ddd;resize:none}</style><div id="root"></div><script>const app={graph:{_nodes:[]},registerExtension(ext){window.extension=ext}};</script>'''
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
    page = b.new_page()
    errors=[]
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.set_content(html)
    page.add_style_tag(content=css)
    page.add_script_tag(content=js)
    page.evaluate('''() => {
      window.node={type:'NorthmereInstruments.DynamicPrompt',widgets:[]};
      extension.nodeCreated(node);
    }''')
    page.evaluate('''() => {
      const container = document.createElement('div');container.className='slot';
      const textarea = document.createElement('textarea');textarea.value='@pick{day: sun|night: moon}';
      // Separate calls deliberately: widget first, DOM attach later.
      node.widgets.push({name:'template',element:textarea});
      window.mount = () => {document.querySelector('#root').append(container);container.append(textarea)};
    }''')
    page.evaluate('mount()')
    page.wait_for_function("document.querySelectorAll('.nmi-prompt-highlight .nmi-prompt-directive').length === 1")
    assert page.locator('.nmi-prompt-highlight').count() == 1
    assert page.evaluate("getComputedStyle(document.querySelector('.nmi-prompt-highlight .nmi-prompt-directive')).color") != page.evaluate("getComputedStyle(document.querySelector('.nmi-prompt-highlight')).color")
    assert not errors, errors
    print('Chromium: no widgets at nodeCreated, alternate node.type, late widget creation, late DOM mount, colored syntax: passed')
    b.close()
