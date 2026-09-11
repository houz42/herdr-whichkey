#!/usr/bin/env python3
"""which-key.nvim style hierarchical key popup for herdr.

Runs inside a herdr plugin popup pane. Shows a tree of key groups (panes,
tabs, workspaces, session); pressing a group key descends, backspace climbs,
escape closes. Leaf keys dispatch through the herdr CLI ($HERDR_BIN_PATH).
Leaves with no CLI/socket equivalent are shown greyed and left to the native
prefix (ctrl+f12).

The key tree lives in $HERDR_PLUGIN_CONFIG_DIR/keys.json (seeded with the
default tree on first run); edit it to rebind or regroup. Successful dispatch
exits the process, which closes the popup.
"""
import json
import os
import select
import shutil
import subprocess
import sys
import time

CONFIG_DIR = os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or "/tmp"
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or "/tmp"
KEYS_PATH = os.path.join(CONFIG_DIR, "keys.json")
LOG_PATH = os.path.join(STATE_DIR, "whichkey.log")
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")


def log(msg):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def run_herdr(argv):
    """Run a herdr CLI command, returning (rc, stdout, stderr)."""
    log(f"run: {argv}")
    try:
        r = subprocess.run([HERDR] + argv, capture_output=True, text=True, timeout=15)
    except Exception as e:  # noqa: BLE001 - surface anything to the status line
        log(f"spawn error: {e!r}")
        return 127, "", str(e)
    log(f"rc={r.returncode} out={r.stdout[:200]!r} err={r.stderr[:200]!r}")
    return r.returncode, r.stdout, r.stderr


def cli_json(argv):
    rc, out, err = run_herdr(argv)
    if rc != 0:
        raise RuntimeError(err.strip() or f"herdr {argv[0]} exited {rc}")
    return json.loads(out)["result"]


# ---------------------------------------------------------------------------
# Invocation context: which pane/tab/workspace the user was looking at
# ---------------------------------------------------------------------------

def resolve_context():
    ctx = {}
    for var in ("HERDR_WHICHKEY_CTX", "HERDR_PLUGIN_CONTEXT_JSON"):
        raw = os.environ.get(var)
        if raw:
            try:
                ctx = json.loads(raw)
                log(f"ctx source: {var}")
                break
            except json.JSONDecodeError:
                log(f"{var} unparseable")
    if not ctx.get("focused_pane_id") and os.environ.get("HERDR_PANE_ID"):
        # Split-pane placement: HERDR_PANE_ID is this popup's own pane, so only
        # trust workspace/tab from the environment, not the pane.
        ctx["workspace_id"] = ctx.get("workspace_id") or os.environ.get("HERDR_WORKSPACE_ID")
        ctx["tab_id"] = ctx.get("tab_id") or os.environ.get("HERDR_TAB_ID")
    if not ctx.get("focused_pane_id"):
        try:
            pane = cli_json(["pane", "current"])["pane"]
            ctx["focused_pane_id"] = pane.get("pane_id")
            ctx["focused_pane_cwd"] = pane.get("cwd") or pane.get("foreground_cwd")
            ctx["tab_id"] = ctx.get("tab_id") or pane.get("tab_id")
            ctx["workspace_id"] = ctx.get("workspace_id") or pane.get("workspace_id")
            log("ctx source: pane current")
        except Exception as e:  # noqa: BLE001
            log(f"pane current failed: {e!r}")
    return ctx


def _pane(ctx):
    return ctx.get("focused_pane_id")


def _pane_cwd(ctx):
    return ctx.get("focused_pane_cwd") or ctx.get("workspace_cwd")


def _tab(ctx):
    return ctx.get("tab_id")


def _ws(ctx):
    return ctx.get("workspace_id")


# ---------------------------------------------------------------------------
# Dispatch: action name -> argv builder. None = native-prefix only.
# ---------------------------------------------------------------------------

def _split(ctx, direction):
    argv = ["pane", "split"]
    if _pane(ctx):
        argv += ["--pane", _pane(ctx)]
    argv += ["--direction", direction]
    if _pane_cwd(ctx):
        argv += ["--cwd", _pane_cwd(ctx)]
    return argv


def _pane_verb(verb):
    def fn(ctx):
        return ["pane", verb, _pane(ctx)] if _pane(ctx) else ["pane", verb]
    return fn


def _new_tab(ctx):
    argv = ["tab", "create", "--focus"]
    if _ws(ctx):
        argv += ["--workspace", _ws(ctx)]
    if _pane_cwd(ctx):
        argv += ["--cwd", _pane_cwd(ctx)]
    return argv


def _new_workspace(ctx):
    argv = ["workspace", "create", "--focus"]
    if _pane_cwd(ctx):
        argv += ["--cwd", _pane_cwd(ctx)]
    return argv


def _tab_digit(ctx, n):
    argv = ["tab", "list"] + (["--workspace", _ws(ctx)] if _ws(ctx) else [])
    tabs = cli_json(argv)["tabs"]
    if 1 <= n <= len(tabs):
        return ["tab", "focus", tabs[n - 1]["tab_id"]]
    raise RuntimeError(f"no tab {n}")


def _tab_relative(ctx, delta):
    argv = ["tab", "list"] + (["--workspace", _ws(ctx)] if _ws(ctx) else [])
    tabs = cli_json(argv)["tabs"]
    idx = next((i for i, t in enumerate(tabs) if t["tab_id"] == _tab(ctx)), None)
    if idx is None:
        idx = next((i for i, t in enumerate(tabs) if t.get("focused")), 0)
    j = min(max(idx + delta, 0), len(tabs) - 1)
    return ["tab", "focus", tabs[j]["tab_id"]]


def _ws_relative(ctx, delta):
    wss = cli_json(["workspace", "list"])["workspaces"]
    idx = next((i for i, w in enumerate(wss) if w["workspace_id"] == _ws(ctx)), None)
    if idx is None:
        idx = next((i for i, w in enumerate(wss) if w.get("focused")), 0)
    j = min(max(idx + delta, 0), len(wss) - 1)
    return ["workspace", "focus", wss[j]["workspace_id"]]


# action: ("argv", fn(ctx)->argv) | ("prompt", prompt, fn(ctx,text)->argv)
#       | ("digit",)              | None (native prefix only)
ACTIONS = {
    "split_horizontal": ("argv", lambda c: _split(c, "down")),
    "split_vertical":   ("argv", lambda c: _split(c, "right")),
    "close_pane":       ("argv", _pane_verb("close")),
    "zoom":             ("argv", _pane_verb("zoom")),
    "rename_pane":      ("prompt", "Rename pane", lambda c, t: ["pane", "rename", _pane(c), t]),
    "last_pane":        None,
    "new_tab":          ("argv", _new_tab),
    "rename_tab":       ("prompt", "Rename tab", lambda c, t: ["tab", "rename", _tab(c), t]),
    "close_tab":        ("argv", lambda c: ["tab", "close", _tab(c)]),
    "switch_tab":       ("digit",),
    "previous_tab":     ("argv", lambda c: _tab_relative(c, -1)),
    "next_tab":         ("argv", lambda c: _tab_relative(c, +1)),
    "new_workspace":    ("argv", _new_workspace),
    "rename_workspace": ("prompt", "Rename workspace", lambda c, t: ["workspace", "rename", _ws(c), t]),
    "close_workspace":  ("argv", lambda c: ["workspace", "close", _ws(c)]),
    "previous_workspace": ("argv", lambda c: _ws_relative(c, -1)),
    "next_workspace":     ("argv", lambda c: _ws_relative(c, +1)),
    "reload_config":    ("argv", lambda c: ["server", "reload-config"]),
    "help":             None,
    "detach":           None,
    "copy_mode":        None,
    "resize_mode":      None,
}

# Default key tree: ordered [key, label, value] rows, grouped under mnemonic
# group keys. A list value is a group (descend); a string value is an action
# name from ACTIONS; None is native-prefix only.
# Key style: n=new, x=close, r=rename/reload, s/v=split down/right (vim),
# ]/[=next/previous. Native-only leaves keep their REAL native chords as keys
# so the status hint teaches the actual ctrl+f12 chord.
DEFAULT_TREE = [
    ["p", "+panes", [
        ["v", "Split pane right", "split_vertical"],
        ["s", "Split pane down", "split_horizontal"],
        ["x", "Close pane", "close_pane"],
        ["z", "Zoom pane", "zoom"],
        ["r", "Rename pane", "rename_pane"],
        [";", "Last pane", None],
    ]],
    ["t", "+tabs", [
        ["n", "New tab", "new_tab"],
        ["r", "Rename tab", "rename_tab"],
        ["x", "Close tab", "close_tab"],
        ["]", "Next tab", "next_tab"],
        ["[", "Previous tab", "previous_tab"],
        ["1..9", "Switch to tab 1-9", "switch_tab"],
    ]],
    ["w", "+workspaces", [
        ["n", "New workspace", "new_workspace"],
        ["r", "Rename workspace", "rename_workspace"],
        ["x", "Close workspace", "close_workspace"],
        ["]", "Next workspace", "next_workspace"],
        ["[", "Previous workspace", "previous_workspace"],
    ]],
    ["s", "+session", [
        ["r", "Reload config", "reload_config"],
        ["?", "Keybinding help", None],
        ["[", "Copy mode", None],
        ["d", "Detach client", None],
        ["ctrl+arrows", "Resize mode", None],
    ]],
]


def load_tree():
    try:
        with open(KEYS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(KEYS_PATH, "w") as f:
                json.dump(DEFAULT_TREE, f, indent=2)
                f.write("\n")
            log(f"seeded {KEYS_PATH}")
        except OSError as e:
            log(f"seed failed: {e!r}")
        return DEFAULT_TREE


class Leaf:
    def __init__(self, key, label, action):
        self.key = key
        self.label = label
        self.action = action  # action name or None (native-only)

    def matches(self, ch):
        k = self.key
        if k.startswith("shift+") and len(k) == 7:
            return ch == k[-1].upper()
        if "+" in k:  # ctrl/alt chords cannot be read from a popup
            return False
        if ".." in k:  # digit ranges are handled by the digit branch
            return False
        return ch == k


class Group:
    def __init__(self, key, name, children):
        self.key = key
        self.name = name  # display name, e.g. "+panes"
        self.children = children  # list of Group | Leaf


def build_tree(raw):
    nodes = []
    for key, label, val in raw:
        if isinstance(val, list):
            nodes.append(Group(key, label, build_tree(val)))
        else:
            nodes.append(Leaf(key, label, val))
    return nodes


def measure(tree):
    """Content size in terminal cells: the widest/tallest level of the tree."""
    levels = [([], tree)]
    for node in tree:
        if isinstance(node, Group):
            levels.append(([node.name], node.children))
    cols = rows = 0
    for path, nodes in levels:
        crumb = " ".join(["f12", *[f"› {p}" for p in path]])
        lines = [f"{crumb} — which-key for herdr", ""]
        for n in nodes:
            if isinstance(n, Group):
                label = n.name
            else:
                label = n.label if n.action else f"{n.label} (native prefix)"
            lines.append(f"  {n.key:<{KEY_W}}{label}")
        lines += ["", "bksp: back · esc: close · native prefix: ctrl+f12"]
        rows = max(rows, len(lines))
        cols = max(cols, max(len(line) for line in lines))
    return cols, rows


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------

DIM = "\x1b[2m"
BOLD = "\x1b[1m"
CYAN = "\x1b[1;36m"
MAGENTA = "\x1b[1;35m"
GREY = "\x1b[2;37m"
RED = "\x1b[1;31m"
RESET = "\x1b[0m"
KEY_W = 14


def render(path, nodes, status=""):
    cols, rows = shutil.get_terminal_size((80, 24))
    crumb = " ".join(["f12", *[f"› {p}" for p in path]])
    out = ["\x1b[2J\x1b[H", f"{BOLD}{crumb}{RESET} {DIM}— which-key for herdr{RESET}", ""]
    for node in nodes:
        if isinstance(node, Group):
            out.append(f"  {MAGENTA}{node.key:<{KEY_W}}{RESET}{BOLD}{node.name}{RESET}")
        elif node.action is not None:
            out.append(f"  {CYAN}{node.key:<{KEY_W}}{RESET}{node.label}")
        else:
            out.append(f"  {GREY}{node.key:<{KEY_W}}{node.label} (native prefix){RESET}")
    out.append("")
    if status:
        out.append(f"{RED}{status}{RESET}")
    else:
        hint = "esc: close"
        if path:
            hint = "bksp: back · " + hint
        out.append(f"{DIM}{hint} · native prefix: ctrl+f12{RESET}")
    text = "\r\n".join(line[:cols] for line in out[:rows])
    sys.stdout.write(text)
    sys.stdout.flush()


def read_key(fd):
    """Read one key; returns a single char, 'ESC', 'BKSP', or None."""
    b = os.read(fd, 1)
    if b == b"":  # pty EOF
        return "ESC"
    if b == b"\x1b":
        r, _, _ = select.select([fd], [], [], 0.04)
        if r:  # escape sequence (arrows etc.) — drain and ignore
            while True:
                c = os.read(fd, 1)
                if c.isalpha() or c == b"~":
                    break
            return None
        return "ESC"
    if b == b"\x03":
        return "ESC"
    if b in (b"\x7f", b"\x08"):
        return "BKSP"
    try:
        return b.decode("utf-8", "replace")
    except UnicodeDecodeError:
        return None


def prompt_line(fd, label):
    """Inline single-line editor on the status row. Returns text or None.

    No save/restore: after each redraw the cursor must sit right after the
    buffer, because that is where the next typed character lands.
    """
    buf = ""
    while True:
        sys.stdout.write(f"\x1b[999;1H\x1b[K{BOLD}{label}: {RESET}{buf}")
        sys.stdout.flush()
        b = os.read(fd, 1)
        if b in (b"\x1b", b"\x03"):
            return None
        if b in (b"\r", b"\n"):
            return buf if buf else None
        if b in (b"\x7f", b"\x08"):
            buf = buf[:-1]
            continue
        try:
            buf += b.decode("utf-8")
        except UnicodeDecodeError:
            pass


def resolve_argv(leaf, ctx, text=None, digit=None):
    spec = ACTIONS[leaf.action]
    kind = spec[0]
    if kind == "prompt":
        return spec[2](ctx, text)
    if kind == "digit":
        return _tab_digit(ctx, digit)
    return spec[1](ctx)


def main():
    env_brief = {k: v[:120] for k, v in os.environ.items() if k.startswith("HERDR")}
    log(f"start pid={os.getpid()} env={env_brief}")
    tree = build_tree(load_tree())
    ctx = resolve_context()
    log(f"ctx resolved: {ctx}")

    if not sys.stdin.isatty():
        render([], tree)
        print()
        return

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    path = []   # display names of entered groups, e.g. ["+tabs"]
    node = tree  # children of current group (or root)
    stack = []  # (name, children) pairs to climb back out
    status = ""
    try:
        tty.setraw(fd)
        render(path, node)
        while True:
            ch = read_key(fd)
            if ch is None:
                continue
            if ch == "ESC":
                return
            if ch == "BKSP":
                if stack:
                    name, parent = stack.pop()
                    path.pop()
                    node = parent
                    status = ""
                    render(path, node)
                continue
            hit = next((n for n in node if isinstance(n, Group) and n.key == ch), None)
            if hit is not None:
                stack.append((hit.name, node))
                path.append(hit.name)
                node = hit.children
                status = ""
                render(path, node)
                continue
            leaf = next((n for n in node if isinstance(n, Leaf) and n.matches(ch)), None)
            digit = None
            if leaf is None and ch.isdigit():
                leaf = next(
                    (n for n in node if isinstance(n, Leaf)
                     and n.action and ACTIONS[n.action][0] == "digit"),
                    None,
                )
                digit = int(ch) if leaf else None
            if leaf is None:
                status = f"unbound: {ch!r}"
                render(path, node, status)
                continue
            if leaf.action is None:
                status = f"{leaf.key} is native-prefix only: ctrl+f12 {leaf.key}"
                render(path, node, status)
                continue
            text = None
            spec = ACTIONS[leaf.action]
            if spec[0] == "prompt":
                text = prompt_line(fd, spec[1])
                if text is None:
                    render(path, node)
                    continue
            try:
                argv = resolve_argv(leaf, ctx, text=text, digit=digit)
            except Exception as e:  # noqa: BLE001
                status = str(e)[:120]
                log(f"resolve error: {e!r}")
                render(path, node, status)
                continue
            rc, _, err = run_herdr(argv)
            if rc == 0:
                return  # success: exiting closes the popup
            status = (err.strip() or f"exit {rc}")[:120]
            render(path, node, status)
    finally:
        import termios as t
        t.tcsetattr(fd, t.TCSADRAIN, old)
        log("exit")


if __name__ == "__main__":
    main()
