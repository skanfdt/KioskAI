"""Optional pre-order experiment using ordinary Chrome zoom on its own fixture tab."""
import json
from pathlib import Path
import time

import win32api
import win32gui


MEASURE = """() => {
    const width = document.documentElement.clientWidth, height = document.documentElement.clientHeight;
    const first = [...document.querySelectorAll('button[data-menu][aria-label]:not(:disabled)')]
      .filter(el => el.getClientRects().length).slice(0, 2);
    const products = first.map(button => {
      const price = button.querySelector('.price-row strong');
      const text = price?.innerText.trim() || '';
      const won = /^([0-9][0-9,]*)원$/.exec(text);
      if (!won) return {name:button.getAttribute('aria-label'), price_text:text, visible:false};
      const range = document.createRange();
      range.selectNodeContents(price);
      const rect = range.getBoundingClientRect();
      const x=(rect.left+rect.right)/2, y=(rect.top+rect.bottom)/2;
      return {name:button.getAttribute('aria-label'), price_text:text,
              won:Number(won[1].replaceAll(',', '')), rect:rect.toJSON(),
              visible: Number(won[1].replaceAll(',', ''))>0 && rect.width>0 && rect.height>0
                && rect.left>=0 && rect.top>=0 && rect.right<=width && rect.bottom<=height
                && button.contains(document.elementFromPoint(x,y))};
    });
    return {width,height,device_pixel_ratio:devicePixelRatio,scroll_x:scrollX,scroll_y:scrollY,
            visible_prices:products.filter(p=>p.visible).length,products};
}"""


def fit_menu_prices(hwnd, page, expected_url, out):
    out = Path(out)
    report = {"initial_condition": "default zoom on fresh Chrome profile", "ctrl_minus_count": 0,
              "samples": []}

    def check():
        if (win32gui.GetForegroundWindow() != hwnd or page.url != expected_url
                or [p for p in page.context.pages if not p.is_closed()] != [page]
                or page.evaluate("document.visibilityState") != "visible"):
            raise RuntimeError("Dedicated kiosk lost its foreground tab or changed URL")

    for step in range(5):
        check()
        sample = page.evaluate(MEASURE)
        sample["ctrl_minus_count"] = step
        report["samples"].append(sample)
        report["ctrl_minus_count"] = step
        report["two_visible_prices"] = sample["visible_prices"] == 2
        check()
        page.screenshot(path=str(out / f"viewport-{step}.png"))
        (out / "viewport-diagnostic.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if report["two_visible_prices"] or step == 4:
            return report
        check()
        # Windows VK_CONTROL + VK_OEM_MINUS targets Chrome's native zoom command.
        win32api.keybd_event(0x11, 0, 0, 0)
        try:
            win32api.keybd_event(0xBD, 0, 0, 0)
        finally:
            win32api.keybd_event(0xBD, 0, 2, 0)
            win32api.keybd_event(0x11, 0, 2, 0)
        time.sleep(2.0)
