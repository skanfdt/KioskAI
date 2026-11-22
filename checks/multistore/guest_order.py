"""Observe one practice order made by stock, freshly installed Windows KioskAI."""
from contextlib import suppress
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import smoke


def load_case(directory):
    spec = importlib.util.spec_from_file_location("practice_case", directory / "case.py")
    case = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(case)
    smoke.require(sum(price for _, _, _, price in case.ROWS) == case.TOTAL
                  and sum(qty for _, _, qty, _ in case.ROWS) == case.COUNT,
                  "Fixture order expectations do not add up.")
    return case


def save(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def verify(case, report, earlier, witness, runs):
    receipt = witness["last"]
    clicks = witness["clicks"]
    activations = witness["activations"]
    tenders = [event for event in clicks if event["stage"] == "payment"]
    history = witness["history"]
    review_index = next((i for i, row in enumerate(history) if row["stage"] == "review" and case.exact(row)), None)
    tender_index = next((i for i, row in enumerate(history) if row["stage"] in ("payment", "receipt")), None)
    observations = [obs for run in runs for obs in run["observations"]]
    calls = [call for run in runs for call in run["model_calls"]]
    def trusted_card(event):
        return (event["stage"] == "payment" and event["trusted"] is True and event["label"] == case.CARD_LABEL
                and case.exact(event["before"]))
    report.update(final_order=receipt, trusted_card_clicks=sum(trusted_card(e) for e in tenders),
                  clicks_after_receipt=sum(e["stage"] == "receipt" for e in clicks), model_calls=len(calls))
    report["checks"] = {
        "one_order_submission": report["goal_submissions"] == 1,
        "reviewed_exact_rows_before_tender": review_index is not None and tender_index is not None and review_index < tender_index,
        "one_exact_card_pointer_event": len(tenders) == 1 and trusted_card(tenders[0]),
        "one_trusted_card_activation": (len(activations) == 1 and trusted_card(activations[0]) and bool(tenders)
                                        and activations[0]["at"] >= tenders[0]["at"]),
        "all_pointer_events_trusted": bool(clicks) and all(e["trusted"] is True for e in clicks),
        "correct_receipt": receipt["stage"] == "receipt" and receipt["method"] == case.RECEIPT_METHOD and case.exact(receipt),
        "stopped_after_receipt": (report.get("status") == "completed" and report["clicks_after_receipt"] == 0
                                  and earlier["last"] == receipt and earlier["clicks"] == clicks
                                  and earlier["activations"] == activations),
        "native_input_from_installed_product": bool(runs) and all(
            r["os_mouse_control"] is True and r["executor"] == "windows_native_input" for r in runs),
        "product_bound_itself_to_fixture": bool(observations) and observations[0]["automatic_binding"]
                                           and all(obs["owned_fixture"] for obs in observations),
        "actual_local_model_response": bool(calls) and all(call["model"] == smoke.MODEL for call in calls),
    }


def remember_run(report, state):
    result = state.get("last_result") or {}
    run_id = result.get("run_id")
    if run_id and run_id not in report["run_ids"]:
        report["run_ids"].append(run_id)
    return result


def order(case, directory, page, proc, url, api, app, out, report):
    if getattr(case, "READY_SELECTOR", ""):
        page.locator(case.READY_SELECTOR).first.wait_for(state="visible", timeout=30000)
    else:
        page.get_by_role("heading", name=case.START_HEADING, exact=True).wait_for(timeout=30000)
    page.bring_to_front()
    hwnd = smoke.wait_for(lambda: smoke.owned_window(proc.pid), 15, "Dedicated Chrome window is missing.")
    if smoke.win32gui.GetForegroundWindow() != hwnd:
        with suppress(Exception):
            smoke.win32gui.SetForegroundWindow(hwnd)
    smoke.wait_for(lambda: smoke.win32gui.GetForegroundWindow() == hwnd, 5, "Interactive Windows desktop unavailable.")
    smoke.own_foreground(hwnd, page, url)
    width, height = smoke.win32api.GetSystemMetrics(0), smoke.win32api.GetSystemMetrics(1)
    x, y = smoke.win32api.GetCursorPos()
    corner = x in (0, width-1) and y in (0, height-1)
    if corner:
        left, top, right, _ = smoke.win32gui.GetWindowRect(hwnd)
        smoke.win32api.SetCursorPos(((left + right) // 2, max(top + 18, 1)))
    report["desktop"] = {"width": width, "height": height, "owned_foreground": True, "moved_pointer_from_corner": corner}
    page.evaluate((directory / "observe.js").read_text(encoding="utf-8-sig"))
    read = lambda: page.evaluate("window.readStandProof()")
    started = None
    try:
        report["browser_preparation"] = smoke.dismiss_translation_bubble(hwnd, page, url)
        def ready():
            state = api("state")
            return state if state.get("ready") and not state.get("busy") and not state.get("clarification") else None
        initial = smoke.wait_for(ready, 90, "Controller not ready for a new order.")
        smoke.require(initial.get("model_provider") == "ollama" and initial.get("model") == smoke.MODEL,
                      "Unexpected controller model.")
        report["model"] = initial["model"]
        smoke.own_foreground(hwnd, page, url)
        witness = read()
        smoke.require(witness["last"]["stage"] == "menu" and witness["last"]["count"] == 0 and not witness["clicks"],
                      "Order must start on an untouched, empty kiosk.")
        page.screenshot(path=str(out / "menu.png"), timeout=8000)
        report["before_goal"] = smoke.capture_state(api, hwnd)
        cursor = max((e["id"] for e in initial["events"]), default=0)
        started = time.monotonic()
        deadline = started + 35 * 60
        smoke.require(api("goal", {"goal": case.GOAL}).get("accepted"), "Controller declined the order.")
        report["goal_submissions"] = 1
        save(out / "order.json", report)
        print("Submitted one order without a window or URL.", flush=True)
        checkpoint = None
        answered = set()
        while time.monotonic() < deadline:
            smoke.own_foreground(hwnd, page, url)
            state = api("state", until=deadline)
            witness = read()
            current = witness["last"]
            for entry in state["events"]:
                if entry["id"] > cursor:
                    cursor = entry["id"]
                    msg = smoke.public_text(entry["text"])
                    report["events"].append({"seconds": round(time.monotonic() - started, 2), "text": msg})
                    print(msg, flush=True)
            marker = (cursor, len(witness["history"]), len(witness["clicks"]), len(witness["activations"]))
            if marker != checkpoint:
                report["last_observed"] = current
                save(out / "witness.json", witness)
                save(out / "order.json", report)
                checkpoint = marker
            if current["stage"] == "review" and case.exact(current) and not (out / "review.png").exists():
                page.screenshot(path=str(out / "review.png"), timeout=8000)
            if not state["busy"] and state.get("last_result"):
                result = remember_run(report, state)
                question = state.get("clarification")
                if not question:
                    report["status"] = result.get("status")
                    if report["status"] != "completed":
                        report["failure_origin"] = "product"
                        report["product_message"] = smoke.public_text(result.get("message", ""))
                    break
                if question["id"] not in answered:
                    report["pending_question"] = {k: question.get(k) for k in ("agent_action", "question", "options")}
                    valid = (not answered and question.get("agent_action") == "confirm"
                             and str(question.get("target")) == str(hwnd) and smoke.CONFIRM in question.get("options", [])
                             and current["stage"] in ("review","payment") and case.exact(current)
                             and any(view["stage"] == "review" and case.exact(view) for view in witness["history"]))
                    if not valid:
                        report.update(failure_origin="product_order_or_clarification", status=result.get("status"))
                        raise RuntimeError("Unexpected question or missing independent proof before confirmation.")
                    answer = {"id": question["id"], "target": question["target"], "answer": smoke.CONFIRM}
                    smoke.require(api("clarify", answer).get("accepted"), "Controller declined its order confirmation.")
                    answered.add(question["id"])
                    report["confirmations"] = len(answered)
                    report.pop("pending_question", None)
            time.sleep(1)
        else:
            report["failure_origin"] = "order_timeout"
            raise TimeoutError("The order reached its 35-minute observation limit.")
        smoke.own_foreground(hwnd, page, url)
        earlier = read()
        time.sleep(2)
        smoke.own_foreground(hwnd, page, url)
        witness = read()
        runs = smoke.collect_product_logs(app, report, hwnd, url)
        save(out / "production-order.json", runs)
        verify(case, report, earlier, witness, runs)
        if not all(report["checks"].values()):
            report.setdefault("failure_origin", "independent_order_verification")
            raise RuntimeError(report.get("product_message") or "One or more independent order checks failed.")
    finally:
        if started is not None:
            report["seconds"] = round(time.monotonic() - started, 2)
        if report["goal_submissions"]:
            with suppress(Exception):
                if not report.get("status") and api("state", until=time.monotonic()+6)["busy"]:
                    api("stop", {})
                    cutoff = time.monotonic() + 22
                    smoke.wait_for(lambda: not api("state", until=cutoff)["busy"], 22, "Could not stop the timed-out order.")
                remember_run(report, api("state", until=time.monotonic()+5))
                save(out / "production-order.json", smoke.collect_product_logs(app, report, hwnd, url))
        if page.url == url:
            with suppress(Exception):
                witness = read()
                report["last_observed"] = witness["last"]
                save(out / "witness.json", witness)
            with suppress(Exception):
                page.screenshot(path=str(out / "last-screen.png"), timeout=8000)
        save(out / "order.json", report)


def main(store):
    case = load_case(HERE / store)
    smoke.TITLE = case.TITLE
    smoke.OBSERVER_DEADLINE = time.monotonic() + 40 * 60
    app = Path(os.environ["KIOSKAI_TEST_APP"])
    temp = Path(os.environ["RUNNER_TEMP"])
    out = Path(os.environ.get("EVIDENCE_DIR", str(temp / "kiosk-evidence")))
    out.mkdir(parents=True, exist_ok=True)
    report = {"store": store, "goal": case.GOAL, "expected_rows": case.ROWS, "expected_count": case.COUNT,
              "expected_total": case.TOTAL, "goal_submissions": 0, "confirmations": 0,
              "run_ids": [], "events": [], "passed": False}
    server = proc = api = None
    try:
        smoke.require((app / "desktop_app.py").is_file(), "Installed R3 app directory missing.")
        port = int(os.environ["KIOSKAI_TEST_PORT"])
        api = smoke.connect_controller(port)
        server = ThreadingHTTPServer(("127.0.0.1", 0), case.handler)
        Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}{case.ROUTE}"
        profile = Path(tempfile.mkdtemp(prefix="kiosk-r3-shop-", dir=temp))
        (profile / "Default").mkdir()
        save(profile / "Default/Preferences", {"omnibox": {"prevent_url_elisions": True}})
        proc = subprocess.Popen([str(smoke.chrome_path()), "--user-data-dir=" + str(profile), "--no-first-run",
                                 "--no-default-browser-check", "--disable-search-engine-choice-screen",
                                 "--force-renderer-accessibility", "--start-maximized",
                                 "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", url],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        endpoint = smoke.wait_for(lambda: smoke.chrome_endpoint(proc, profile), 45, "Dedicated Chrome did not start.")
        with smoke.sync_playwright() as runner:
            browser = runner.chromium.connect_over_cdp(endpoint)
            pages = smoke.wait_for(lambda: [p for ctx in browser.contexts for p in ctx.pages if p.url == url],
                                   15, "Dedicated kiosk tab did not load.")
            smoke.require(len(pages) == 1, "Expected one dedicated practice kiosk.")
            with suppress(Exception):
                report["sessions"] = smoke.desktop_sessions(proc.pid, port)
            order(case, HERE / store, pages[0], proc, url, api, app, out, report)
        report["passed"] = True
    except Exception as exc:
        report.setdefault("failure_origin", "test_observer")
        report["error"] = f"{type(exc).__name__}: {smoke.public_text(exc)}"
        if api:
            with suppress(Exception):
                report["controller_at_failure"] = smoke.capture_state(api, smoke.owned_window(proc.pid) if proc else None)
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
        if server:
            server.shutdown()
            server.server_close()
        save(out / "order.json", report)
    print("Order:", store, "PASS" if report["passed"] else report.get("error", "FAIL"), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("green", "cafe"):
        raise SystemExit("usage: guest_order.py green|cafe")
    raise SystemExit(main(sys.argv[1]))
