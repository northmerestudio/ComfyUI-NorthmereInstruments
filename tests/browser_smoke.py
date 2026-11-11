"""Stand-alone Chromium smoke test for the real editor DOM and pointer geometry.

Run: python tests/browser_smoke.py
Requires optional developer dependency: pip install playwright
Install Chromium separately if it is not available on the machine.
This does not constitute a live ComfyUI Nodes 2.0 integration test.
"""
from __future__ import annotations

from pathlib import Path
import json
import shutil

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    source = (ROOT / 'web/curve.js').read_text(encoding='utf-8')
    source = source.replace("import { app } from '../../scripts/app.js';", '')
    source = source.replace('import.meta.url', "'file:///tmp/curve.js'")
    css = (ROOT / 'web/curve.css').read_text(encoding='utf-8')
    browser_page = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
    <style>body {{ margin: 0; background: #242424; color: #eee; }}{css}</style>
    </head><body><div id='mount'></div><script>
    const app = {{ graph: {{ setDirtyCanvas() {{}} }},
      registerExtension(extension) {{ window.__extension = extension; }} }};
    {source}
    const node = {{ size: [360, 420], graph: {{ change() {{}} }}, setDirtyCanvas() {{}},
      addDOMWidget(name, type, root, options) {{
        const widget = {{ name, type, ...options, callback() {{}} }};
        this.widget = widget; this.root = root; document.getElementById('mount').append(root);
        return widget;
      }} }};
    window.__node = node;
    window.__extension.getCustomWidgets().NORTHMERE_INSTRUMENTS_CURVE(node, 'curve', [null, {{}}]);
    </script></body></html>"""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=shutil.which('chromium'),
            args=['--no-sandbox'],
        )
        page = browser.new_page(viewport={'width': 950, 'height': 1000})
        failures: list[str] = []
        page.on('pageerror', lambda error: failures.append(str(error)))
        page.set_content(browser_page)
        root = page.locator('.nmi-editor')
        root.evaluate('(element) => { element.style.width = "360px"; }')
        graph = page.locator('.nmi-plot')
        height360 = graph.bounding_box()['height']
        root.evaluate('(element) => { element.style.width = "600px"; }')
        page.evaluate('window.__node.size[0] = 600')
        height600 = graph.bounding_box()['height']
        assert height600 > height360 * 1.45, (height360, height600)
        assert page.evaluate('window.__node.widget.getHeight()') > 520
        root.evaluate('(element) => { element.style.width = "360px"; }')
        page.evaluate('window.__node.size[0] = 360')

        def payload() -> dict:
            return json.loads(page.evaluate('window.__node.widget.getValue()'))

        assert len(payload()['points']) == 9
        # The keyframe inspector must not round stored eight-decimal strengths.
        page.locator('.nmi-handle').nth(7).click()
        numbers = page.locator('.nmi-number')
        assert numbers.nth(1).input_value() == '0.9991808'
        numbers.nth(0).fill('0.877')
        numbers.nth(0).press('Tab')
        assert payload()['points'][7][0] == 0.877
        assert payload()['points'][7][1] == 0.9991808
        page.locator('.nmi-preset').select_option('off')
        assert all(y == 0 for _x, y in payload()['points'])
        page.locator('.nmi-preset').select_option('full')
        assert all(y == 1 for _x, y in payload()['points'])
        page.locator('.nmi-preset').select_option('early-fade-in')
        assert payload()['points'][0][1] == 0
        assert all(y == 1 for _x, y in payload()['points'][4:])

        # A saved arbitrary curve must render without losing precision or sign.
        target = {'version': 1, 'interpolation': 'smooth',
                  'points': [[0, -1.25], [0.5, 2.75], [1, 0.5]]}
        page.evaluate('(value) => window.__node.widget.setValue(value)', json.dumps(target))
        assert payload() == target
        incompatible = json.dumps({'version': 2, 'points': [[0, 0], [1, 1]]})
        page.evaluate('(value) => window.__node.widget.setValue(value)', incompatible)
        assert page.evaluate('window.__node.widget.getValue()') == incompatible
        assert page.locator('.nmi-validation').is_visible()
        page.evaluate('(value) => window.__node.widget.setValue(value)', json.dumps(target))
        assert not page.locator('.nmi-validation').is_visible()
        # Drag the selected point beyond the initial range; it must remain on-plot.
        handle = page.locator('.nmi-handle').nth(1)
        rect = handle.bounding_box()
        page.mouse.move(rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] / 2)
        page.mouse.down()
        page.mouse.move(rect['x'] + rect['width'] / 2, rect['y'] - 110, steps=5)
        page.mouse.up()
        assert payload()['points'][1][1] > 2.75
        cy = page.locator('.nmi-handle').nth(1).get_attribute('cy')
        assert 15 <= float(cy) <= 190, cy
        page.get_by_role('button', name='Reset').click()
        assert len(payload()['points']) == 9
        assert payload()['points'][7][1] == 0.9991808
        assert not failures, failures
        browser.close()
        print(f'PASS Chromium: responsive graph {height360:.0f}->{height600:.0f}px, precise edits, presets, saved data and drag autoscale')


if __name__ == '__main__':
    main()
