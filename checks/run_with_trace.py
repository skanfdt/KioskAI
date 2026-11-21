"""Observe Windows failures while the installed KioskAI bootstrap runs unchanged."""
import argparse
import ast
import dis
import json
import linecache
import os
from pathlib import Path
import re
import runpy
import sys
import threading
import time


def code_fragment(tb):
    """Use CPython's traceback bytecode offset to identify the failing expression."""
    code = tb.tb_frame.f_code
    offset = tb.tb_lasti
    current = None
    for ins in dis.get_instructions(code):
        if ins.offset > offset:
            break
        current = ins
    if current is None or current.positions.lineno is None:
        return "", None
    pos = current.positions
    if None in (pos.end_lineno, pos.col_offset, pos.end_col_offset) or pos.end_lineno - pos.lineno > 20:
        return "", current.opname
    # CPython's column offsets are UTF-8 byte offsets.
    lines = [linecache.getline(code.co_filename, n).encode("utf-8")
             for n in range(pos.lineno, pos.end_lineno + 1)]
    if not lines:
        return "", current.opname
    lines[-1] = lines[-1][:pos.end_col_offset]
    lines[0] = lines[0][pos.col_offset:]
    try:
        expression = ast.parse(b"".join(lines).decode("utf-8").strip(), mode="eval")
        for node in ast.walk(expression):
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
                node.value = "<literal>"
        return ast.unparse(expression.body)[:320], current.opname
    except (SyntaxError, UnicodeError, ValueError):
        return "", current.opname


class WindowsCallTrace:
    def __init__(self, app, evidence):
        self.app = app
        self.python = Path(sys.executable).resolve().parent
        self.file = evidence / "windows-call-trace.json"
        self.start = time.monotonic()
        self.lock = threading.Lock()
        self.events = []

    def persist(self):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"armed": True, "events": self.events}, indent=2), encoding="utf-8")
        os.replace(tmp, self.file)

    def location(self, code):
        path = Path(code.co_filename)
        for label, root in (("app", self.app), ("python", self.python)):
            if path.is_absolute() and path.is_relative_to(root):
                return label + "/" + path.relative_to(root).as_posix()
        return "<generated or external>"

    def stack(self, tb):
        frames = []
        while tb is not None:
            frame = tb.tb_frame
            code = frame.f_code
            filename = self.location(code)
            row = {"file": filename, "line": tb.tb_lineno, "function": code.co_qualname}
            if filename != "<generated or external>":
                expr, opname = code_fragment(tb)
                row.update(expression=expr, instruction=opname)
                # Dynamic UIA property queries may carry their actual property name in a local.
                for key in ("prop", "property_name", "method_name", "funcname", "attr"):
                    name = frame.f_locals.get(key)
                    if isinstance(name, str) and re.fullmatch(
                            r"(?:Current|GetCurrent|SetCurrent|IUIAutomation)[A-Za-z0-9_]{1,80}", name):
                        row["uia_member"] = name
                        break
            frames.append(row)
            tb = tb.tb_next
        return frames[-45:]

    def record(self, frame, error, tb, terminal):
        if error is None or tb is None:
            return
        error_type = type(error).__name__
        hr = getattr(error, "hresult", None)
        if hr is None and error_type == "COMError" and error.args and type(error.args[0]) is int:
            hr = error.args[0]
        winerror = getattr(error, "winerror", None)
        if not terminal and error_type != "COMError" and not isinstance(hr, int) and not isinstance(winerror, int):
            return
        state = frame.f_locals
        run_id = Path(str(state.get("run_dir", ""))).name
        result = state.get("result") if isinstance(state.get("result"), dict) else {}
        phase = state.get("phase") or result.get("error_phase")
        event = {"terminal": terminal, "seconds": round(time.monotonic() - self.start, 3), "type": error_type,
                 "run_id": run_id if re.fullmatch(r"\d{8}-\d{6}-\d+-native-chat", run_id) else None,
                 "phase": phase if isinstance(phase, str) and re.fullmatch(r"[A-Za-z_]{1,64}", phase) else None,
                 "frames": self.stack(tb)}
        if isinstance(hr, int):
            event.update(hresult=hr, hresult_hex=f"0x{hr & 0xffffffff:08X}")
        if isinstance(winerror, int):
            event["winerror"] = winerror
        function = getattr(error, "funcname", None)
        if isinstance(function, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,100}", function):
            event["win32_function"] = function
        with self.lock:
            self.events.append(event)
            if len(self.events) > 28:
                self.events.pop(0)
            self.persist()

    def local_trace(self, frame, event, arg):
        if event == "exception":
            try:
                self.record(frame, arg[1], arg[2], False)
            except Exception:
                pass
        return self.local_trace

    def global_trace(self, frame, event, _arg):
        name = frame.f_code.co_name
        if name == "run_native" and frame.f_globals.get("__name__") == "native_agent":
            frame.f_trace_lines = False
            return self.local_trace
        if name == "finish" and frame.f_code.co_qualname == "run_native.<locals>.finish":
            if frame.f_locals.get("status") == "error" and frame.f_back is not None:
                try:
                    error = sys.exception()
                    if error is not None:
                        self.record(frame.f_back, error, error.__traceback__, True)
                except Exception:
                    pass
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    app = args.app.resolve(strict=True)
    evidence = args.evidence.resolve(strict=True)
    script = app / "distribution" / "bootstrap.py"
    if not script.is_file() or not (app / "native_agent.py").is_file():
        raise RuntimeError("Released app entry point not found")
    tracer = WindowsCallTrace(app, evidence)
    tracer.persist()
    # No opcode or line tracing: watch only the worker's order boundary, and its error exit.
    threading.settrace(tracer.global_trace)
    os.chdir(app)
    sys.argv[:] = [str(script)]
    sys.path[0] = str(script.parent)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
