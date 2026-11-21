"""Exercise the installed KioskAI controller on a disposable Windows desktop."""
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from threading import Thread
import time
from urllib.error import URLError
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
        request = Request(f"{origin}/api/{action}", data=body, headers=headers)
        # A slow response to a status read must not cancel an order in progress.
        if action == "state" and body is None:
            deadline = time.monotonic() + 240
            while True:
                try:
                    with urlopen(request, timeout=45) as response:
                        return json.load(response)
                except (TimeoutError, ConnectionResetError, URLError) as error:
                    if getattr(error, "code", None) or time.monotonic() >= deadline:
                        raise
                    print("Waiting for the local controller to answer the status check.", flush=True)
                    time.sleep(2)
        with urlopen(request, timeout=45) as response:
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


def dismiss_translation_bubble(hwnd, page, url):
    from pywinauto import Desktop

    top = Desktop(backend="uia").window(handle=hwnd).wrapper_object()

    def chrome_tabs():
        frame_tabs = []
        for tab in top.descendants(control_type="TabItem"):
            parent = tab.parent()
            while parent is not None and parent != top and parent.element_info.control_type != "Document":
                parent = parent.parent()
            if parent == top and tab.is_visible():
                frame_tabs.append(tab)
        return frame_tabs

    def language_tabs(tabs):
        labels = {"english", "korean", "영어", "한국어"}
        return [tab for tab in tabs if (tab.element_info.name or "").strip().casefold() in labels]

    def selected(tab):
        try:
            return bool(tab.iface_selection_item.CurrentIsSelected)
        except Exception:
            return bool(tab.legacy_properties()["State"] & 2)

    # Only prepare browser chrome, before the single order. Never interact with the page here.
    time.sleep(2)
    own_foreground(hwnd, page, url)
    tabs = chrome_tabs()
    langs = language_tabs(tabs)
    selected_before = sum(selected(tab) for tab in tabs)
    shop_tabs = [tab for tab in tabs if TITLE in (tab.element_info.name or "")]
    actions = []
    if (len(langs) == 2 and langs[0].parent() == langs[1].parent() and sum(selected(t) for t in langs) == 1
            and len(shop_tabs) == 1 and selected(shop_tabs[0])):
        panel = langs[0].parent()
        while panel is not None and panel != top:
            if any(TITLE in (t.element_info.name or "") for t in panel.descendants(control_type="TabItem")):
                break
            close = [b for b in panel.descendants(control_type="Button") if b.is_visible()
                     and (b.element_info.name or "").strip().casefold() in ("close", "닫기")]
            if len(close) == 1:
                own_foreground(hwnd, page, url)
                with suppress(Exception):
                    close[0].invoke()
                    actions.append("Closed the Chrome language panel")
                break
            panel = panel.parent()
        if not actions:
            own_foreground(hwnd, page, url)
            win32api.keybd_event(0x1B, 0, 0, 0)
            win32api.keybd_event(0x1B, 0, 2, 0)
            actions.append("Escape in the verified kiosk Chrome window")
        time.sleep(.6)
        if language_tabs(chrome_tabs()):
            own_foreground(hwnd, page, url)
            shop_tabs = [tab for tab in chrome_tabs() if TITLE in (tab.element_info.name or "")]
            if len(shop_tabs) == 1:
                shop_tabs[0].click_input()
                actions.append("Clicked the existing shop tab in the Chrome tab strip")
                time.sleep(.6)
    own_foreground(hwnd, page, url)
    after = chrome_tabs()
    return {"chrome_tabs_before": len(tabs), "language_tabs_before": len(langs), "actions": actions,
            "selected_before": selected_before, "selected_after": sum(selected(tab) for tab in after),
            "chrome_tabs_after": len(after), "language_tabs_after": len(language_tabs(after))}


def public_text(message):
    message = re.sub(r"https?://[^\s)]+", "[URL]", str(message))
    message = re.sub(r"(?i)[a-z]:[\\/][^\n]+", "[guest path]", message)
    return re.sub(r"(?i)\b[a-f0-9]{24,}\b", "[id]", message)[:450]


def capture_state(api, hwnd):
    state = api("state")
    menu = state.get("menu_inventory") or {}
    summary = {k: state.get(k) for k in ("ready", "busy", "model", "model_provider", "observation_mode")}
    summary["menu"] = {k: menu.get(k) for k in ("status", "source", "products", "categories") if k in menu}
    if menu.get("message"):
        summary["menu"]["message"] = public_text(menu["message"])
    summary["menu"]["owned_target"] = bool(hwnd and str(menu.get("target")) == str(hwnd))
    result = state.get("last_result") or {}
    summary["last_result"] = {k: public_text(result[k]) for k in ("status", "message") if k in result}
    question = state.get("clarification") or {}
    summary["question"] = {k: public_text(question[k]) for k in ("kind", "agent_action", "prompt") if k in question}
    summary["events"] = [public_text(e.get("text", "")) for e in state.get("events", [])[-12:]]
    windows = api("windows").get("windows", [])
    summary["windows"] = [{"owned_fixture": str(w.get("id")) == str(hwnd), "chrome": w.get("process") == "chrome.exe",
                           "looks_like_controller": "KioskAI" in w.get("title", ""),
                           "foreground": str(w.get("id")) == str(win32gui.GetForegroundWindow())} for w in windows]
    return summary


def desktop_sessions(chrome_pid, port):
    import ctypes
    from ctypes import wintypes
    import psutil

    def session(pid):
        sid = wintypes.DWORD()
        if pid and ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(sid)):
            return sid.value
    listener = next((c.pid for c in psutil.net_connections(kind="tcp") if c.status == psutil.CONN_LISTEN
                     and c.laddr.ip == "127.0.0.1" and c.laddr.port == port), None)
    return {"observer_session": session(os.getpid()), "chrome_session": session(chrome_pid),
            "controller_session": session(listener), "console_session": ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()}


def accessibility_facts(hwnd, url):
    from pywinauto import Desktop

    top = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
    diagnostics = {"owned_foreground": win32gui.GetForegroundWindow() == hwnd}
    tabs = top.descendants(control_type="TabItem")
    edits = top.descendants(control_type="Edit")
    docs = [c for c in top.descendants(control_type="Document") if c.is_visible()
            and c.rectangle().width() * c.rectangle().height() > 0]
    diagnostics.update(tab_items=len(tabs), visible_documents=len(docs), edit_count=len(edits),
                       visible_edit_count=sum(bool(e.is_visible()) for e in edits))
    popup_markers = ("translate", "restore pages", "welcome to chrome", "sign in to chrome", "번역", "페이지 복원")
    visible_controls = [c for c in top.descendants(depth=9) if c.is_visible()]
    diagnostics["visible_popup_markers"] = [m for m in popup_markers if any(
        m in (c.element_info.name or "").casefold() for c in visible_controls)]
    addresses = []
    for edit in edits:
        name = (edit.element_info.name or "").casefold()
        if edit.is_visible() and any(k in name for k in ("address", "주소", "omnibox")):
            row = {"known_chrome_address_label": bool(re.search(r"주소창 및 검색창|주소 및 검색창|address and search bar", name))}
            with suppress(Exception):
                value = edit.get_value().rstrip("/")
                row.update(explicit_scheme=value.startswith("http://"), exact_origin=value == url.rstrip("/"),
                           fixture_host_and_port=value.removeprefix("http://") == url.removeprefix("http://").rstrip("/"))
            addresses.append(row)
    diagnostics["address_controls"] = addresses
    if docs:
        doc = max(docs, key=lambda d: d.rectangle().width() * d.rectangle().height())
        names = [(n.element_info.control_type, n.element_info.name or "") for n in doc.descendants(depth=15) if n.is_visible()]
        diagnostics["menu_uia"] = {"basket": any("주문표" in n for _, n in names),
                                   "positive_price": any(re.search(r"[1-9][\d,]*\s*원", n) is not None for _, n in names),
                                   "menu_item_button": any(k == "Button" and "사과 주스" in n for k, n in names)}
    return diagnostics


def binding_probe(app, hwnd, pid, url):
    """Post-failure diagnostics only. Never send this window or URL to the controller."""
    require(owned_window(pid) == hwnd, "Cannot identify the dedicated fixture for diagnostics.")
    # Use the COM apartment mode and local provider selected by the installed controller.
    sys.coinit_flags = 2
    os.environ["KIOSKAI_MODEL_PROVIDER"] = "ollama"
    diagnostics = {"owned_foreground": win32gui.GetForegroundWindow() == hwnd}
    try:
        diagnostics.update(accessibility_facts(hwnd, url))
    except Exception as exc:
        diagnostics["accessibility_error_type"] = type(exc).__name__
    print(json.dumps(diagnostics, ensure_ascii=False), flush=True)
    sys.path.insert(0, str(app))
    from control.native_desktop import NativeDesktop, NativeHandoff, NativeStopped
    native = None
    try:
        native = NativeDesktop(str(hwnd), store_mode=True, auto_bind=True, stopped=lambda: False)
        with suppress(Exception):
            tabs = native.top.descendants(control_type="TabItem")
            diagnostics["native_selected_tabs"] = sum(bool(native._in_chrome_frame(t) and t.is_visible()
                                                           and native._tab_selected(t)) for t in tabs)
        try:
            key = native.bind_open_store()
            diagnostics["read_only_product_binding"] = {"accepted": True, "same_origin": key.get("url", "").rstrip("/") == url.rstrip("/")}
        finally:
            diagnostics["native_address_counts"] = {k: v for k, v in getattr(native, "address_diagnostics", {}).items()
                                                    if isinstance(v, (bool, int))}
    except Exception as exc:
        diagnostic = {"accepted": False, "type": type(exc).__name__}
        if isinstance(exc, (NativeHandoff, NativeStopped)):
            diagnostic["message"] = public_text(exc)
        diagnostics["read_only_product_binding"] = diagnostic
    finally:
        if native:
            native.close()
        print(json.dumps(diagnostics, ensure_ascii=False), flush=True)


def failed_binding(app, hwnd, pid, url):
    if not hwnd or owned_window(pid) != hwnd:
        return {"skipped": "Dedicated fixture window unavailable"}
    cmd = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "--binding-probe", str(hwnd), str(pid), url]
    try:
        result = subprocess.run(cmd, cwd=app, capture_output=True, timeout=65)
        data = result.stdout
        status = result.returncode
    except subprocess.TimeoutExpired as exc:
        data, status = exc.stdout or b"", "timed_out"
    diag = {"probe_exit": status}
    for line in data.decode("utf-8", errors="replace").splitlines():
        with suppress(ValueError):
            value = json.loads(line)
            if isinstance(value, dict) and "owned_foreground" in value:
                diag.update(value)
    return diag


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
                     "executor": result.get("executor"), "observations": observations, "model_calls": decisions,
                     "menu_inventory": {k: v for k, v in (result.get("menu_inventory") or {}).items()
                                        if k in ("status", "source", "products", "categories")}})
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
    report["browser_preparation"] = dismiss_translation_bubble(hwnd, page, url)
    read = lambda: page.evaluate("window.readStandProof()")

    def ready_to_order():
        state = api("state")
        if state.get("ready") and not state.get("busy") and not state.get("clarification"):
            return state

    initial = wait_for(ready_to_order, 90, "The installed controller was not ready for a new order.")
    require(initial.get("model_provider") == "ollama" and initial.get("model") == MODEL, "Unexpected controller model.")
    own_foreground(hwnd, page, url)
    before = read()
    require(before["last"]["stage"] == "menu" and before["last"]["count"] == 0 and not before["clicks"],
            "The practice order must start on an untouched, empty menu.")
    page.screenshot(path=str(out / "menu.png"))
    with suppress(Exception):
        report["before_goal"] = capture_state(api, hwnd)
    report["feed_requests_before_goal"] = server.feed_requests
    report["viewport"] = page.evaluate("({width:innerWidth, height:innerHeight})")
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
                    text = public_text(event["text"])
                    report["events"].append({"seconds": round(time.monotonic() - start, 2), "text": text})
                    print(text, flush=True)
                    save(out / "order.json", report)
                    save(out / "witness.json", witness)
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
            time.sleep(1.2)
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
        report["feed_requests_at_completion"] = server.feed_requests
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
    server = proc = api = None
    try:
        require((app / "desktop_app.py").is_file(), "Installed application directory is missing.")
        port = int(os.environ["KIOSKAI_TEST_PORT"])
        api = connect_controller(port)
        with suppress(Exception):
            report["controller_start"] = capture_state(api, None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Stand)
        server.feed_requests = 0
        Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/"
        profile = Path(tempfile.mkdtemp(prefix="kiosk-test-chrome-", dir=guest_temp))
        (profile / "Default").mkdir()
        save(profile / "Default/Preferences", {"omnibox": {"prevent_url_elisions": True}})
        proc = subprocess.Popen([str(chrome_path()), "--user-data-dir=" + str(profile), "--no-first-run",
                                 "--no-default-browser-check", "--disable-search-engine-choice-screen",
                                 "--force-renderer-accessibility", "--start-maximized",
                                 "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", url],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        endpoint = wait_for(lambda: chrome_endpoint(proc, profile), 45, "Dedicated Chrome did not start.")
        with sync_playwright() as runner:
            browser = runner.chromium.connect_over_cdp(endpoint)
            def fixture_pages():
                return [p for ctx in browser.contexts for p in ctx.pages if p.url == url]
            pages = wait_for(fixture_pages, 15, "The dedicated kiosk page did not load.")
            require(len(pages) == 1, "Dedicated practice tab is missing.")
            page = pages[0]
            with suppress(Exception):
                report["sessions"] = desktop_sessions(proc.pid, port)
            try:
                exercise(page, proc, api, report, app, out, url, server)
            except Exception as error:
                report["error"] = public_text(error)
                report["feed_requests_at_failure"] = server.feed_requests
                # Freeze the result before running any separate product diagnostic.
                save(out / "order.json", report)
                hwnd = owned_window(proc.pid)
                with suppress(Exception):
                    report["controller_at_failure"] = capture_state(api, hwnd)
                if page.url == url:
                    with suppress(Exception):
                        page.screenshot(path=str(out / "last-screen.png"), timeout=8000)
                    with suppress(Exception):
                        snap = page.evaluate("window.readStandProof?.()")
                        if snap:
                            save(out / "witness.json", snap)
                save(out / "order.json", report)
                with suppress(Exception):
                    report["post_failure_binding_diagnostic"] = failed_binding(app, hwnd, proc.pid, url)
                raise
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {public_text(error)}"
        if api and "controller_at_failure" not in report:
            with suppress(Exception):
                report["controller_at_failure"] = capture_state(api, owned_window(proc.pid) if proc else None)
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
    if len(sys.argv) == 5 and sys.argv[1] == "--binding-probe":
        binding_probe(Path(os.environ["KIOSKAI_TEST_APP"]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
    else:
        raise SystemExit(main())
