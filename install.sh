#!/usr/bin/env bash
# Installer for the Terminator "Profile Switcher" plugin.
#
# Usage:
#   bash install.sh             # install (or update)
#   bash install.sh --uninstall # remove the plugin

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_SRC="$SCRIPT_DIR/profile_switcher.py"
PLUGIN_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/terminator/plugins"
PLUGIN_DST="$PLUGIN_DIR/profile_switcher.py"

if [[ "${1:-}" == "--uninstall" ]]; then
    if [[ -f "$PLUGIN_DST" ]]; then
        rm -f "$PLUGIN_DST"
        echo "Removed $PLUGIN_DST"
        echo "Disable 'ProfileSwitcher' in Terminator > Preferences > Plugins,"
        echo "then restart Terminator."
    else
        echo "Nothing to remove (no $PLUGIN_DST)."
    fi
    exit 0
fi

if [[ ! -f "$PLUGIN_SRC" ]]; then
    echo "Error: plugin file not found at $PLUGIN_SRC" >&2
    exit 1
fi

if ! command -v terminator >/dev/null 2>&1; then
    echo "Warning: 'terminator' not found in PATH." >&2
    echo "         Installing the plugin file anyway." >&2
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: 'python3' is required." >&2
    exit 1
fi

if ! python3 -c "import ast; ast.parse(open('$PLUGIN_SRC').read())" 2>/dev/null; then
    echo "Error: plugin failed Python syntax check." >&2
    exit 1
fi

mkdir -p "$PLUGIN_DIR"

if [[ -f "$PLUGIN_DST" ]]; then
    backup="$PLUGIN_DST.bak.$(date +%Y%m%d-%H%M%S)"
    cp -p "$PLUGIN_DST" "$backup"
    echo "Backed up existing plugin to $backup"
fi

cp "$PLUGIN_SRC" "$PLUGIN_DST"

cat <<MSG

Installed: $PLUGIN_DST

Next steps:
  1. (Re)start Terminator -- plugins are only scanned at startup.
  2. Open Preferences > Plugins, enable 'ProfileSwitcher'.
  3. Right-click any terminal > Profile Switcher > Preferences...
     to add rules.

Rule examples (Command, Argument, Profile):
  ssh      *staging*      yellow
  ssh      *prod*         red
  top      (empty)        dark
  python3  (empty)        solarized

Notes:
  - Empty Argument matches any invocation of that command.
  - Argument is a case-insensitive fnmatch glob matched against argv[1:]
    joined with spaces.
  - The Profile must already exist in Terminator (Preferences > Profiles).
  - Command is matched against /proc/<pid>/comm, truncated at 15 chars.
MSG
