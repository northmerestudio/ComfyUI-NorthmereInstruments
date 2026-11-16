"""Browser test: exercise the SAME getCustomWidgets/addDOMWidget contract used by curve.js.

This is an isolated ComfyUI-compatible host harness, not a full running ComfyUI instance.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
import shutil

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT/'web/prompt.js').read_text().replace("import { app } from '../../scripts/app.js';", '').replace('import.meta.url', "'file:///tmp/prompt.js'").replace('export function tokens', 'function tokens')
CSS = (ROOT/'web/prompt.css').read_text()
PROMPT = '// A comment about this prompt\n@define hero\ncinematic portrait\n@end\n@pick{day: 3::sunny|night: moonlit} \\{literal\\}\n@if(day){warm light}\n@rules\nday -> !night\nday ~> night @ 3\n@end\n@use(hero)'


def main():
    html = f'''<!doctype html><html><head><meta charset="utf-8"><style>
    body {{margin:30px;background:#252525;color:#e6e6e6;}}
    #mount {{width:450px;height:280px}}
    {CSS}
    </style></head><body><div id="mount"></div><script>
    window.app = {{registerExtension(ext) {{ window.ext = ext; }} }};
    {JS}
    document.addEventListener('wheel', () => window.canvasWheels = (window.canvasWheels || 0) + 1);
    window.node = {{graph:{{change() {{window.changed = (window.changed || 0) + 1;}}}},
       setDirtyCanvas() {{}}, addDOMWidget(name, type, root, options) {{
         document.getElementById('mount').append(root);
         this.widget = {{name,type,...options, callback(){{}}}};return this.widget;
       }} }};
    window.ext.getCustomWidgets().NORTHMERE_INSTRUMENTS_PROMPT(node,'template',['STRING',{{}}]);
    </script></body></html>'''
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
        for theme in ('dark', 'light'):
            page = browser.new_page(viewport={'width':850,'height':550}, color_scheme=theme)
            errors=[]
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.set_content(html)
            page.evaluate("document.body.classList.add('comfy-theme-' + arguments[0])".replace('arguments[0]', f"'{theme}'"))
            assert page.locator('.nmi-prompt-indicator').inner_text() == 'syntax'
            page.evaluate('(v) => node.widget.setValue(v)', PROMPT)
            assert page.evaluate('node.widget.getValue()') == PROMPT
            assert page.locator('.nmi-prompt-comment').count() == 1
            assert page.locator('.nmi-prompt-comment').inner_text() == '// A comment about this prompt'
            assert page.evaluate('getComputedStyle(document.querySelector(".nmi-prompt-comment")).color') != page.evaluate('getComputedStyle(document.querySelector(".nmi-prompt")).color')
            assert page.locator('.nmi-prompt-directive').count() >= 5
            assert page.locator('.nmi-prompt-delimiter').count() >= 4
            assert page.locator('.nmi-prompt-weight').count() == 1
            assert page.locator('.nmi-prompt-label').count() >= 1
            assert page.locator('.nmi-prompt-operator').count() == 2
            assert page.locator('.nmi-prompt-escape').count() == 2
            assert page.locator('.nmi-prompt').count() == 1
            assert page.evaluate('getComputedStyle(document.querySelector(".nmi-prompt-directive")).color') != page.evaluate('getComputedStyle(document.querySelector(".nmi-prompt")).color')
            page.locator('textarea').fill(PROMPT + '\n<script>alert(1)</script>')
            assert '<script>alert(1)</script>' in page.evaluate('node.widget.getValue()')
            assert page.locator('.nmi-prompt-highlight script').count() == 0
            assert page.evaluate('window.changed') >= 1
            page.locator('textarea').press('ControlOrMeta+z')
            page.evaluate('(v) => node.widget.setValue(v)', '\n'.join([PROMPT]*20))
            page.locator('textarea').evaluate('(e) => {e.scrollTop=100;e.dispatchEvent(new Event("scroll"));}')
            assert '-100px' in page.locator('.nmi-prompt-highlight').evaluate('(e) => e.style.transform')
            # Real wheel input stays in the textarea; ComfyUI canvas must not receive it.
            page.locator('textarea').hover()
            page.mouse.wheel(0, 125)
            page.wait_for_timeout(80)
            assert page.locator('textarea').evaluate('(e) => e.scrollTop') > 0
            assert page.evaluate('window.canvasWheels || 0') == 0
            # Long unwrapped lines must scroll horizontally, and the mirror tracks both axes.
            page.evaluate('(v) => node.widget.setValue(v)', ('x' * 180) + '\n' + ('line\n' * 45))
            assert page.locator('textarea').evaluate('(e) => e.scrollWidth > e.clientWidth')
            assert page.locator('textarea').evaluate('(e) => getComputedStyle(e).whiteSpace') == 'pre'
            page.locator('textarea').evaluate('(e) => { e.scrollLeft = 160; e.dispatchEvent(new Event("scroll")); }')
            assert '-160px' in page.locator('.nmi-prompt-highlight').evaluate('(e) => e.style.transform')
            page.locator('textarea').hover()
            page.mouse.wheel(85, 0)
            page.wait_for_timeout(80)
            assert page.locator('textarea').evaluate('(e) => e.scrollLeft') > 160
            assert page.evaluate('window.canvasWheels || 0') == 0
            # Tab indentation keeps focus and updates the serialized widget value.
            page.evaluate('(v) => node.widget.setValue(v)', 'hello')
            page.locator('textarea').focus()
            page.locator('textarea').evaluate('(e) => e.setSelectionRange(0, 0)')
            page.locator('textarea').press('Tab')
            assert page.evaluate('node.widget.getValue()') == '    hello'
            assert page.evaluate('document.activeElement.classList.contains("nmi-prompt-input")')
            # Basic visual parity: mirror and textarea have identical font and padding.
            assert page.evaluate('''() => {
                const a=getComputedStyle(document.querySelector('textarea'));
                const b=getComputedStyle(document.querySelector('.nmi-prompt-highlight'));
                return a.font===b.font && a.padding===b.padding && a.width===b.width;
            }''')
            page.evaluate('(v) => node.widget.setValue(v)', PROMPT)
            assert not errors, errors
            page.close()
        browser.close()
    print('PASS: registration, themes, editing, restore, wheel isolation, horizontal scroll, mirror sync, Tab indentation')

if __name__ == '__main__': main()
