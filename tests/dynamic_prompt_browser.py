"""Actual Chromium DOM test: deferred ComfyUI widget mount, syntax and both themes."""
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
js = (ROOT / 'web/dynamic_prompt_highlight.js').read_text()
js = js.replace("import { app } from '../../scripts/app.js';", '')
js = js.replace('import.meta.url', "'file:///tmp/dynamic_prompt_highlight.js'")
css = (ROOT / 'web/dynamic_prompt_highlight.css').read_text()

html = f'''<!doctype html><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
body {{ font-family: sans-serif; }}
#mount {{ position: relative; width: 440px; height: 250px; }}
textarea {{ width: 400px; height: 220px; color: rgb(30,30,30); background: rgb(250,250,250);
  font: 14px/20px monospace; padding: 8px; border: 1px solid #888; resize: none; }}
body.dark textarea {{ color: rgb(230,230,230); background: rgb(30,30,30); }}
body.dark #mount {{ color: rgb(230,230,230); }}
body:not(.dark) #mount {{ color: rgb(30,30,30); }}
{css}
</style><div id="mount"></div><script>
const app = {{ registerExtension(e) {{ window.extension = e; }} }};
{js}
window.textarea = document.createElement('textarea');
window.textarea.value = String.raw`@pick{{day: sun|night: moon}} 3::bright \\{{literal}}`;
window.widget = {{ name: 'template', element: window.textarea, setValue(v) {{ this.element.value = v }} }};
window.extension.nodeCreated({{ comfyClass: 'NorthmereInstruments.DynamicPrompt', widgets: [widget] }});
window.attachLater = () => document.querySelector('#mount').append(window.textarea);
</script>'''

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 900, 'height': 500}, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.set_content(html)
    assert page.locator('.nmi-prompt-highlight').count() == 0, 'expected detached widget initially'
    page.evaluate('attachLater()')
    page.locator('.nmi-prompt-highlight').wait_for()
    page.wait_for_function("document.querySelector('.nmi-prompt-highlight').textContent.includes('@pick')")
    assert page.locator('.nmi-prompt-directive').first.text_content() == '@pick'
    assert page.locator('.nmi-prompt-weight').first.text_content() == '3'
    assert page.locator('.nmi-prompt-escape').count() == 1
    for theme in ('light', 'dark'):
        page.evaluate("theme => document.body.classList.toggle('dark', theme === 'dark')", theme)
        page.wait_for_function("theme => document.querySelector('.nmi-prompt-highlight').dataset.theme === theme", arg=theme)
        computed = page.evaluate('''() => {
          const textarea = document.querySelector('textarea'), layer = document.querySelector('.nmi-prompt-highlight');
          return {
            hiddenOriginal: getComputedStyle(textarea).color,
            visibleDirective: getComputedStyle(layer.querySelector('.nmi-prompt-directive')).color,
            visiblePlain: getComputedStyle(layer).color,
            covered: (() => {const t=textarea.getBoundingClientRect(), p=layer.getBoundingClientRect();
              return Math.abs(t.x-p.x)<1 && Math.abs(t.y-p.y)<1 && Math.abs(t.width-p.width)<1 && Math.abs(t.height-p.height)<1;})(),
            tokens: layer.textContent,
          };
        }''')
        assert computed['covered'], computed
        assert computed['visibleDirective'] != computed['visiblePlain'], (theme, computed)
        assert computed['hiddenOriginal'] == 'rgba(0, 0, 0, 0)', computed
        assert computed['tokens'].startswith('@pick'), computed
    page.locator('textarea').fill('@if(day){warm} | @end')
    page.wait_for_function("document.querySelector('.nmi-prompt-highlight').textContent.startsWith('@if(day)')")
    assert not errors, errors
    print('Chromium: deferred mount, visible colored tokens, exact overlay bounds, input updates and both themes passed')
    browser.close()
