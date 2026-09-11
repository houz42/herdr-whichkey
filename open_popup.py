#!/usr/bin/env python3
"""Plugin action entrypoint: open the whichkey popup pane, sized to content.

Actions invoked from a keybinding run detached (no terminal), so the action
itself cannot be the UI. It asks the running herdr server to open the
manifest-declared `whichkey` pane entrypoint (placement = popup), where the
interactive TUI runs.

The herdr CLI does not expose popup width/height overrides, so the open
request goes over the raw socket API (NDJSON) with dimensions measured from
the key tree: the popup is exactly as large as the content needs. Falls back
to the CLI (manifest dimensions) if the socket path fails.

The action (not the pane) receives the invocation context, so it is forwarded
to the popup process as HERDR_WHICHKEY_CTX.
"""
import json
import os
import socket
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import whichkey  # noqa: E402

HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
PARAMS = {"plugin_id": "houz42.whichkey", "entrypoint": "whichkey"}

ctx = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON", "")
if ctx:
    PARAMS["env"] = {"HERDR_WHICHKEY_CTX": ctx}

try:
    cols, rows = whichkey.measure(whichkey.build_tree(whichkey.load_tree()))
    PARAMS["width"] = cols + 4   # 2 border + 2 margin
    PARAMS["height"] = rows + 2  # 2 border (popup dimensions are outer size)
except Exception:  # noqa: BLE001 - sizing is best-effort; manifest dims apply
    pass


def open_via_socket():
    sock_path = os.environ["HERDR_SOCKET_PATH"]
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(5)
    s.connect(sock_path)
    try:
        req = {"id": "whichkey-open", "method": "plugin.pane.open", "params": PARAMS}
        s.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        resp = json.loads(buf)
    finally:
        s.close()
    if "error" in resp:
        raise RuntimeError(resp["error"].get("message", "socket error"))
    return resp


try:
    open_via_socket()
except Exception as e:  # noqa: BLE001
    print(f"socket open failed ({e}), falling back to CLI", file=sys.stderr)
    cmd = [HERDR, "plugin", "pane", "open"]
    flag = {"plugin_id": "--plugin", "entrypoint": "--entrypoint"}
    for k, v in PARAMS.items():
        if k == "env":
            for ek, ev in v.items():
                cmd += ["--env", f"{ek}={ev}"]
        elif k in ("width", "height"):
            continue  # CLI has no size flags; manifest dims apply
        else:
            cmd += [flag[k], str(v)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    sys.exit(result.returncode)
