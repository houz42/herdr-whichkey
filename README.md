# Which-Key for Herdr

A [which-key.nvim](https://github.com/folke/which-key.nvim) style key popup for
[Herdr](https://herdr.dev): press the prefix key and a popup shows the available
keys as a hierarchy of groups. Descend into a group, press a leaf key, and the
command is dispatched through the Herdr CLI. The popup closes itself.

```
f12 › +tabs — which-key for herdr

  n             New tab
  r             Rename tab
  x             Close tab
  ]             Next tab
  [             Previous tab
  1..9          Switch to tab 1-9
```

- Hierarchical groups: `p` panes, `t` tabs, `w` workspaces, `s` session
- Mnemonic leaf keys: `n` new, `x` close, `r` rename/reload, `s`/`v` split
  down/right, `]`/`[` next/previous
- Breadcrumb header, `bksp` climbs up, `esc` closes
- Popup is sized to its content on every open
- Commands with no Herdr CLI/socket equivalent (help, detach, copy mode,
  resize mode, last pane) are shown greyed and stay on the native prefix
- Rename actions prompt inline

## Requirements

- Herdr >= 0.9.0
- `python3` (standard library only; no build step, no dependencies)

## Install

```sh
herdr plugin install houz42/herdr-whichkey
```

## Setup: bind it to your prefix key

Plugins cannot bind keys themselves, so one config edit is needed. One-shot:

```sh
herdr plugin action invoke houz42.whichkey.setup
```

This backs up `~/.config/herdr/config.toml`, binds `f12` to the popup, moves
the native prefix to `ctrl+f12` if it was `f12`, and reloads the server. It
is idempotent and refuses to clobber an existing `f12` binding.

Your other `prefix+...` keybindings do **not** need to change — the popup has
its own mnemonic key tree and dispatches through the CLI; everything it cannot
dispatch stays on the native prefix.

Or edit manually — add this at the **end** of the `[keys]` section
(everything after an array-of-tables header belongs to it):

```toml
[[keys.command]]
key = "f12"
type = "plugin_action"
command = "houz42.whichkey.open"
description = "which-key popup"
```

If `f12` is your Herdr prefix, move the native prefix first — the popup
replaces silent prefix mode:

```toml
[keys]
prefix = "ctrl+f12"
```

Then `herdr server reload-config`. Press `f12` for the popup; the native
prefix (`ctrl+f12`) still serves the greyed-out commands.

## Usage

- `f12` opens the popup at the root groups
- A group key (`p`/`t`/`w`/`s`) descends; the header shows the path
  (`f12 › +tabs`)
- A leaf key runs the command against the pane/tab/workspace you were focused
  on and closes the popup
- `bksp` climbs one level, `esc` closes
- Rename leaves open an inline prompt at the bottom row

## Customize the keys

The key tree is a JSON file, seeded on first run:

```sh
herdr plugin config-dir houz42.whichkey   # contains keys.json
```

Edit `keys.json` to rebind, regroup, or relabel. Each row is
`["key", "label", action]`; `action` is a name from the `ACTIONS` table in
`whichkey.py`, `null` marks a native-prefix-only key, and a nested list makes
a group. The popup re-measures and resizes itself to fit.

## Develop locally

```sh
git clone https://github.com/houz42/herdr-whichkey.git
cd herdr-whichkey
herdr plugin link .
```

`herdr plugin log list --plugin houz42.whichkey` shows action runs; the TUI
writes a debug log under `$(herdr plugin config-dir houz42.whichkey)/../..`
— `$HERDR_PLUGIN_STATE_DIR/whichkey.log`
(`~/.local/state/herdr/plugins/houz42.whichkey/whichkey.log`).

## How it works

- `open_popup.py` (the keybound action) measures the key tree and opens the
  popup through the raw socket API with explicit `width`/`height`, since the
  CLI has no popup size flags; it also forwards the invocation context
  (focused pane/tab/workspace) into the popup process
- `whichkey.py` (the popup process) renders the tree, reads raw keys, and
  dispatches through `$HERDR_BIN_PATH` — the entire Herdr CLI is the plugin
  API

Herdr 0.9.0 popups are always centered; there is no anchor/position option.
If upstream adds one (e.g. `bottom-right`), it is a one-line manifest change.
