#!/usr/bin/env python3
"""One-shot setup action for the whichkey plugin.

Edits ~/.config/herdr/config.toml so the plugin is actually reachable:

1. If the herdr prefix is f12, moves it to ctrl+f12 (the popup replaces
   silent prefix mode; the native prefix stays available for the few
   commands that have no CLI equivalent).
2. Appends a [[keys.command]] block binding bare f12 to the plugin action,
   inserted inside the [keys] section (an array-of-tables header swallows
   everything after it, so placement matters).

Idempotent: exits 0 without changes when the binding is already present.
Backs up config.toml before editing. Reloads the running server's config.
Run with: herdr plugin action invoke houz42.whichkey.setup
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

CONFIG = Path(os.environ.get("HERDR_CONFIG", Path.home() / ".config" / "herdr" / "config.toml"))
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
TRIGGER = "f12"
FALLBACK_PREFIX = "ctrl+f12"

BLOCK = f"""
# which-key popup (houz42.whichkey): {TRIGGER} opens the key hierarchy popup.
# Native prefix ({FALLBACK_PREFIX}) still serves help/detach/copy/resize/last-pane.
[[keys.command]]
key = "{TRIGGER}"
type = "plugin_action"
command = "houz42.whichkey.open"
description = "which-key popup"

"""


def main():
    text = CONFIG.read_text() if CONFIG.exists() else ""

    if "houz42.whichkey.open" in text:
        print("whichkey setup: binding already present, nothing to do")
        return

    if re.search(r'^\s*key\s*=\s*"' + TRIGGER + r'"', text, re.M):
        print(
            f"whichkey setup: {TRIGGER} is already used by another [[keys.command]]; "
            "edit config.toml manually",
            file=sys.stderr,
        )
        sys.exit(1)

    backup = CONFIG.with_suffix(f".toml.bak-whichkey-{time.strftime('%Y%m%d-%H%M%S')}")
    if CONFIG.exists():
        shutil.copy(CONFIG, backup)
        print(f"whichkey setup: backup at {backup}")

    if re.search(r'^\s*prefix\s*=\s*"' + TRIGGER + r'"', text, re.M):
        def move_prefix(m):
            return (
                f'{m.group(1)}prefix = "{FALLBACK_PREFIX}"'
                f"  # moved by whichkey setup; {TRIGGER} opens the popup"
            )
        text = re.sub(
            r'^(\s*)prefix\s*=\s*"' + TRIGGER + r'"',
            move_prefix,
            text,
            count=1,
            flags=re.M,
        )
        print(f"whichkey setup: native prefix moved to {FALLBACK_PREFIX}")

    m = re.search(r"^\[keys\]\s*$", text, re.M)
    if m:
        nxt = re.search(r"^\[", text[m.end():], re.M)
        insert_at = m.end() + (nxt.start() if nxt else len(text) - m.end())
        text = text[:insert_at] + BLOCK + text[insert_at:]
    else:
        text = text.rstrip() + "\n\n[keys]\n" + BLOCK

    CONFIG.write_text(text)
    print(f"whichkey setup: bound {TRIGGER} in {CONFIG}")

    try:
        r = subprocess.run([HERDR, "server", "reload-config"], capture_output=True, text=True)
    except OSError:
        print(f"whichkey setup: config written; reload manually with: herdr server reload-config")
        return
    out = (r.stdout or "") + (r.stderr or "")
    print(out.strip())
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
