"""Exercise the installed KioskAI controller on a disposable Windows desktop."""
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from threading import Thread
import time
from urllib.request import Request, urlopen

import win32api
import win32gui
import win32process
from playwright.sync_api import sync_playwright


HERE = Path(__file__).resolve().parent
SITE = HERE / "site"
GOAL = "사과 주스는 작은 컵으로 한 잔, 큰 컵으로 두 잔 주세요. 귀리 쿠키도 한 개 담고, 총금액을 확인한 뒤 카드 결제 방식을 선택해 주세요."
ROWS = [("사과 주스", "작은 컵", 1, 3600), ("사과 주스", "큰 컵", 2, 8600), ("귀리 쿠키", "기본", 1, 1900)]
CONFIRM = "확인한 내용으로 실행"
MODEL = "qwen3.5:9b"
TITLE = "물결 주스 스탠드"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def exact(view):
    return (sorted(tuple(row) for row in view["rows"]) == sorted(ROWS)
            and view["count"] == 4 and view["total"] == 14100)


class Stand(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        files = {"/": ("index.html", "text/html"), "/stand.css": ("stand.css", "text/css"),
                 "/stand.js": ("stand.js", "text/javascript"), "/published/card.json": ("menu.json", "application/json")}
        path = self.path.split("?", 1)[0]
        if path not in files:
            self.send_error(404)
            return
        filename, mime = files[path]
        body = (SITE / filename).read_text(encoding="utf-8")
        if path == "/":
            link = '<link rel="alternate" type="application/vnd.kioskai.menu+json" href="/published/card.json">'
            body = body.replace("<!--menu-discovery-->", link)
        elif path == "/stand.js":
            body = body.replace("__MENU_DATA__", (SITE / "menu.json").read_text(encoding="utf-8"))
        elif path == "/published/card.json":
            self.server.feed_requests += 1
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def connect_controller(port):
    origin = f"http://127.0.0.1:{port}"
    with urlopen(origin, timeout=10) as response:
        token = re.search(r'nonce="([a-f0-9]{64})"', response.read().decode())[1]

    def call(action, payload=None):
        headers = {"Origin": origin, "X-KioskAI-Token": token, "Content-Type": "application/json"}
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        with urlopen(Request(f"{origin}/api/{action}", data=body, headers=headers), timeout=15) as response:
            return json.load(response)
    return call


def chrome_path():
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if os.environ.get(key):
            chrome = Path(os.environ[key]) / "Google/Chrome/Application/chrome.exe"
            if chrome.is_file():
                return chrome
    raise RuntimeError("Google Chrome is missing on the guest.")


def owned_window(pid):
    matches = []

    def collect(hwnd, _):
        if (win32gui.IsWindowVisible(hwnd) and TITLE in win32gui.GetWindowText(hwnd)
                and win32process.GetWindowThreadProcessId(hwnd)[1] == pid):
            matches.append(hwnd)

    win32gui.EnumWindows(collect, None)
    return matches[0] if len(matches) == 1 else None


def wait_for(check, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.25)
    raise RuntimeError(message)


def chrome_endpoint(proc, profile):
    require(proc.poll() is None, "The dedicated Chrome exited at startup.")
    endpoint = profile / "DevToolsActivePort"
    if endpoint.is_file():
        lines = endpoint.read_text(encoding="ascii").splitlines()
        if lines and lines[0].isdigit():
            return "http://127.0.0.1:" + lines[0]


def own_foreground(hwnd, page, url):
    require(win32gui.GetForegroundWindow() == hwnd and page.url == url,
            "The dedicated kiosk lost the foreground or changed URL.")


def collect_product_logs(app, report, hwnd, url):
    runs = []
    for run_id in report["run_ids"]:
        require(re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]+-native-chat", run_id),
                "Unexpected production log directory name.")
        root = app / "logs" / run_id
        result = json.loads((root / "result.json").read_text(encoding="utf-8"))
        decisions = []
        observations = []
        for path in sorted(root.glob("*.json")):
            entry = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(entry, dict):
                continue
            if path.name.endswith("-observation.json"):
                window = entry.get("window", {})
                same_target = (str(window.get("id")) == str(hwnd)
                               and str(entry.get("url", "")).rstrip("/") == url.rstrip("/"))
                observations.append({"file": path.name, "owned_fixture": same_target,
                                     "automatic_binding": entry.get("auto_store_bound") is True})
            if entry.get("provider") == "ollama" and entry.get("response_model"):
                decisions.append({"file": path.name, "provider": "ollama", "model": entry["response_model"]})
        runs.append({"run_id": run_id, "status": result.get("status"), "os_mouse_control": result.get("os_mouse_control"),
                     "executor": result.get("executor"), "observations": observations, "model_calls": decisions})
    return runs


def exercise(page, proc, api, report, app, out, url, server):
    page.get_by_role("heading", name="메뉴", exact=True).wait_for(timeout=30000)
    page.bring_to_front()
    hwnd = wait_for(lambda: owned_window(proc.pid), 15, "Dedicated Chrome window is missing.")
    if win32gui.GetForegroundWindow() != hwnd:
        with suppress(Exception):
            win32gui.SetForegroundWindow(hwnd)
    wait_for(lambda: win32gui.GetForegroundWindow() == hwnd, 5, "Interactive Windows desktop is unavailable.")
    own_foreground(hwnd, page, url)
    width, height = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
    x, y = win32api.GetCursorPos()
    corner = x in (0, width-1) and y in (0, height-1)
    if corner:
        left, top, right, _ = win32gui.GetWindowRect(hwnd)
        win32api.SetCursorPos(((left + right) // 2, max(top + 18, 1)))
    report["desktop"] = {"width": width, "height": height, "owned_foreground": True,
                         "moved_pointer_from_corner_without_click": corner}
    print("Dedicated kiosk is visible. Pointer moved from screen corner:", corner, flush=True)
    page.evaluate((HERE / "observe.js").read_text(encoding="utf-8"))
    read = lambda: page.evaluate("window.readStandProof()")

    def idle_menu():
        state = api("state")
        menu = state.get("menu_inventory") or {}
        if (state.get("ready") and not state.get("busy") and not state.get("clarification")
                and str(menu.get("target")) == str(hwnd) and menu.get("source") == "advertised_feed"
                and menu.get("products") == 4 and menu.get("status") in ("complete", "cached")):
            return state

    initial = wait_for(idle_menu, 90, "The installed controller did not discover the four published menu items.")
    require(initial.get("model_provider") == "ollama" and initial.get("model") == MODEL, "Unexpected controller model.")
    own_foreground(hwnd, page, url)
    before = read()
    require(before["last"]["stage"] == "menu" and before["last"]["count"] == 0 and not before["clicks"],
            "The practice order must start on an untouched, empty menu.")
    require(server.feed_requests >= 1, "The controller did not request the advertised menu.")
    report["menu_before_goal"] = {k: initial["menu_inventory"].get(k) for k in ("source", "products", "categories", "status")}
    cursor = max((event["id"] for event in initial["events"]), default=0)
    start = time.monotonic()
    try:
        require(api("goal", {"goal": GOAL}).get("accepted"), "The installed controller declined the order.")
        report["goal_submissions"] = 1
        print("One targetless order submitted.", flush=True)
        answered = None
        while time.monotonic() - start < 2700:
            own_foreground(hwnd, page, url)
            state = api("state")
            witness = read()
            current = witness["last"]
            for event in state["events"]:
                if event["id"] > cursor:
                    cursor = event["id"]
                    report["events"].append({"seconds": round(time.monotonic() - start, 2), "text": event["text"]})
                    print(event["text"], flush=True)
            if current["stage"] == "review" and exact(current) and not (out / "review.png").exists():
                page.screenshot(path=str(out / "review.png"))
            result = state.get("last_result") or {}
            if not state["busy"] and result:
                run_id = result.get("run_id")
                if run_id and run_id not in report["run_ids"]:
                    report["run_ids"].append(run_id)
                question = state.get("clarification")
                if question and question["id"] != answered:
                    require(answered is None and question.get("agent_action") == "confirm"
                            and str(question.get("target")) == str(hwnd) and CONFIRM in question.get("options", [])
                            and current["stage"] in ("review", "payment") and exact(current),
                            "Unexpected question or incorrect order at the confirmation step.")
                    answered = question["id"]
                    require(api("clarify", {"id": answered, "target": question["target"], "answer": CONFIRM})["accepted"],
                            "The controller declined the normal confirmation.")
                    report["confirmations"] = 1
                elif not question:
                    report["status"] = result.get("status")
                    break
            time.sleep(.3)
        else:
            raise RuntimeError("The order exceeded 45 minutes.")
        own_foreground(hwnd, page, url)
        at_completion = read()
        time.sleep(2)
        own_foreground(hwnd, page, url)
        witness = read()
        save(out / "witness.json", witness)
        page.screenshot(path=str(out / "last-screen.png"))
        runs = collect_product_logs(app, report, hwnd, url)
        save(out / "production-order.json", runs)
        receipt, history, clicks = witness["last"], witness["history"], witness["clicks"]
        review_index = next((i for i, row in enumerate(history) if row["stage"] == "review" and exact(row)), None)
        tender_index = next((i for i, row in enumerate(history) if row["stage"] in ("payment", "receipt")), None)
        tenders = [event for event in clicks if event["stage"] == "payment"]
        activations = witness["activations"]
        calls = [call for run in runs for call in run["model_calls"]]
        observations = [obs for run in runs for obs in run["observations"]]
        report.update(seconds=round(time.monotonic() - start, 2), final_order=receipt, trusted_card_clicks=sum(
            e["trusted"] and e["label"] == "카드" for e in tenders), clicks_after_receipt=sum(c["stage"] == "receipt" for c in clicks))
        report["checks"] = {
            "reviewed_exact_rows_before_tender": review_index is not None and tender_index is not None and review_index < tender_index,
            "one_exact_card_pointer_event": len(tenders) == 1 and tenders[0]["label"] == "카드" and exact(tenders[0]["before"]),
            "one_trusted_card_activation": len(activations) == 1 and activations[0]["stage"] == "payment"
                                           and activations[0]["label"] == "카드" and activations[0]["trusted"]
                                           and exact(activations[0]["before"]) and bool(tenders)
                                           and activations[0]["at"] >= tenders[0]["at"],
            "all_pointer_events_trusted": bool(clicks) and all(e["trusted"] for e in clicks),
            "correct_receipt": receipt["stage"] == "receipt" and receipt["method"] == "선택한 결제 방식 · 카드" and exact(receipt),
            "stopped_after_receipt": report["status"] == "completed" and not report["clicks_after_receipt"]
                                    and at_completion["last"] == receipt and at_completion["clicks"] == clicks
                                    and at_completion["activations"] == activations,
            "native_input_from_installed_product": bool(runs) and all(
                run["os_mouse_control"] is True and run["executor"] == "windows_native_input" for run in runs),
            "product_bound_itself_to_fixture": bool(observations) and observations[0]["automatic_binding"]
                                               and all(obs["owned_fixture"] for obs in observations),
            "actual_local_model_response": bool(calls) and all(call["model"] == MODEL for call in calls),
        }
        require(all(report["checks"].values()), "See the failed checks in order.json.")
    finally:
        try:
            if report["goal_submissions"] and api("state")["busy"]:
                api("stop", {})
                wait_for(lambda: not api("state")["busy"], 30, "Controller did not acknowledge stop.")
        finally:
            if report["goal_submissions"] and not (out / "production-order.json").exists():
                with suppress(Exception):
                    latest = (api("state").get("last_result") or {}).get("run_id")
                    if latest and latest not in report["run_ids"]:
                        report["run_ids"].append(latest)
                    save(out / "production-order.json", collect_product_logs(app, report, hwnd, url))
            if page.url == url:
                with suppress(Exception):
                    save(out / "witness.json", read())
                    page.screenshot(path=str(out / "last-screen.png"), timeout=8000)


def main():
    app = Path(os.environ["KIOSKAI_TEST_APP"])
    guest_temp = Path(os.environ["RUNNER_TEMP"])
    out = guest_temp / "kiosk-evidence"
    out.mkdir(parents=True, exist_ok=True)
    report = {"goal": GOAL, "goal_submissions": 0, "confirmations": 0, "run_ids": [], "events": [], "passed": False}
    server = proc = None
    try:
        require((app / "desktop_app.py").is_file(), "Installed application directory is missing.")
        api = connect_controller(int(os.environ["KIOSKAI_TEST_PORT"]))
        server = ThreadingHTTPServer(("127.0.0.1", 0), Stand)
        server.feed_requests = 0
        Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/"
        profile = Path(tempfile.mkdtemp(prefix="kiosk-test-chrome-", dir=guest_temp))
        (profile / "Default").mkdir()
        save(profile / "Default/Preferences", {"omnibox": {"prevent_url_elisions": True}})
        proc = subprocess.Popen([str(chrome_path()), "--user-data-dir=" + str(profile), "--no-first-run",
                                 "--no-default-browser-check", "--disable-search-engine-choice-screen",
                                 "--force-renderer-accessibility", "--window-size=1280,900",
                                 "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", url],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        endpoint = wait_for(lambda: chrome_endpoint(proc, profile), 45, "Dedicated Chrome did not start.")
        with sync_playwright() as runner:
            browser = runner.chromium.connect_over_cdp(endpoint)
            def fixture_pages():
                return [p for ctx in browser.contexts for p in ctx.pages if p.url == url]
            pages = wait_for(fixture_pages, 15, "The dedicated kiosk page did not load.")
            require(len(pages) == 1, "Dedicated practice tab is missing.")
            exercise(pages[0], proc, api, report, app, out, url, server)
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
        if server:
            server.shutdown()
            server.server_close()
        save(out / "order.json", report)
    print("Order:", "PASS" if report["passed"] else report.get("error", "FAIL"), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
