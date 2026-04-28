# Terminator Profile Changer

A [Terminator](https://gnome-terminator.org/) plugin that automatically switches the terminal profile based on the foreground command running in each pane.

No shell integration required — detection is purely local via `/proc`.

## What it does

Define rules like:

| Command | Argument (glob) | Profile |
|---------|-----------------|---------|
| `ssh` | `*staging*` | `yellow` |
| `ssh` | `*prod*` | `red` |
| `top` | *(empty)* | `dark` |
| `python3` | *(empty)* | `solarized` |

When you run `ssh user@prod-db`, Terminator switches that pane to your `red` profile. When you exit back to the shell, it reverts to `default`. Profiles you set manually are never clobbered.

## Requirements

- Terminator
- Python 3
- Linux (uses `/proc`)

## Install

```bash
git clone https://github.com/Etienne-GN/terminator-profile-changer.git
cd terminator-profile-changer
bash install.sh
```

Then:
1. Restart Terminator (plugins are scanned at startup).
2. **Preferences > Plugins** — enable `ProfileSwitcher`.
3. Right-click any terminal pane → **Profile Switcher > Preferences** to add rules.

## Uninstall

```bash
bash install.sh --uninstall
```

Then disable `ProfileSwitcher` in Preferences > Plugins and restart Terminator.

## Configuring rules

Rules are managed via the GUI: right-click any terminal → **Profile Switcher > Preferences**.

Each rule has three fields:

- **Command** — exact process name as reported by `/proc/<pid>/comm`. Note: the kernel truncates this to 15 characters.
- **Argument** — case-insensitive [`fnmatch`](https://docs.python.org/3/library/fnmatch.html) glob matched against `argv[1:]` joined with spaces. Leave empty to match any invocation of the command.
- **Profile** — the Terminator profile to apply. Must exist in Preferences > Profiles.

**First matching rule wins.** When no rule matches and the shell is back in the foreground, the profile reverts to `default` — but only if this plugin had previously changed it, so manually-selected profiles are left alone.

## How it works

The plugin polls each terminal's PTY file descriptor every second using `os.tcgetpgrp()` to get the foreground process group, then reads `/proc/<pgid>/comm` and `/proc/<pgid>/cmdline`. This requires no shell hooks, no changes to remote hosts, and works with any shell.

## License

MIT
