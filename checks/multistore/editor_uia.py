"""Read a dialog inside one foreground fixture tab. The caller also enforces a process timeout."""
from collections import deque
import json
import sys
import time

import win32gui
import win32process
from pywinauto import Desktop


def inspect(hwnd, pid, title, dialog_id, item):
    def owned():
        return (int(hwnd) == win32gui.GetForegroundWindow() and
                win32process.GetWindowThreadProcessId(hwnd)[1] == pid and
                title in win32gui.GetWindowText(hwnd))
    if not owned() or not dialog_id or not item:
        return {"reason": "The original synthetic shop and dialog could not be verified."}
    top = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
    docs = [e for e in top.descendants(control_type="Document", depth=15)
            if e.is_visible() and e.element_info.name == title]
    if len(docs) != 1:
        return {"reason": "No unique, visible Windows document with the fixture title."}
    deadline = time.monotonic() + 5
    queue = deque([docs[0]])
    dialog = None
    for _ in range(220):
        if not queue or time.monotonic() >= deadline or not owned():
            break
        node = queue.popleft()
        info = node.element_info
        aria = str(info.element.CurrentAriaRole or "").strip().lower()
        if node.is_visible() and (info.automation_id == dialog_id or
                (aria == "dialog" or info.control_type == "Window") and info.name == item):
            dialog = node
            break
        queue.extend(node.children()[:60])
    if dialog is None:
        return {"reason": "The visible HTML dialog had no identifiable Windows dialog subtree within the search bound."}
    records = []
    queue = deque([(dialog, None)])
    while queue and len(records) < 70 and time.monotonic() < deadline:
        if not owned():
            return {"reason": "Foreground shop changed during the read."}
        node, parent = queue.popleft()
        info = node.element_info
        el = info.element
        rect = node.rectangle()
        row = dict(parent=parent, role=info.control_type, aria_role=str(el.CurrentAriaRole or "")[:90],
                   id=str(info.automation_id or "")[:100], name=str(info.name or "")[:180],
                   offscreen=bool(el.CurrentIsOffscreen), bounds=[rect.left, rect.top, rect.right, rect.bottom])
        props = str(el.CurrentAriaProperties or "")
        row["aria_state"] = [s for s in props.split(";") if s.partition("=")[0].strip().lower()
                             in ("checked", "selected", "disabled", "label", "roledescription",
                                 "posinset", "setsize")][:12]
        if row["role"] in ("RadioButton", "CheckBox"):
            try:
                row["selected"] = bool(node.iface_selection_item.CurrentIsSelected)
            except Exception:
                try:
                    row["toggle_state"] = int(node.iface_toggle.CurrentToggleState)
                except Exception:
                    pass
        index = len(records)
        records.append(row)
        queue.extend((c, index) for c in node.children()[:45])
    return {"reason": "Read the visible fixture dialog.", "truncated": bool(queue), "nodes": records}


if __name__ == "__main__":
    try:
        result = inspect(int(sys.argv[1]), int(sys.argv[2]), *sys.argv[3:])
    except Exception as exc:
        result = {"reason": "Windows dialog capture failed.", "error_type": type(exc).__name__}
    print("KIOSK_EDITOR " + json.dumps(result, ensure_ascii=True))
