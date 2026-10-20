# opencode-tray

**English** · [简体中文](README.zh-Hans.md)

A tray icon for the **OpenCode V2** background service on Windows — for people who work in
the OpenCode TUI (developed against it from Windows Terminal / PowerShell 7).

OpenCode V2 keeps a background server that owns every conversation. Two things follow from
that, and both are mildly annoying without an indicator:

- **Closing every window does not stop OpenCode.** The server keeps running, and keeps
  holding memory and CPU. Nothing on screen tells you whether it is up.
- **You sometimes have to restart that server yourself** — installing a plugin, for one.
  The usual route is to quit OpenCode and type `opencode service restart` in a terminal,
  which is a lot of ceremony for a single command.

It is also the fastest way back to recent work: the menu lists your recent sessions across
all projects, and clicking one opens it in the OpenCode TUI.

```
OpenCode Service: Running v2.0.32
─────────────────────────────────
Sessions                          ▸  ● OpenCode Windows tray…
─────────────────────────────────
Restart service / Stop service / Start service
─────────────────────────────────
Language                          ▸  ● English
                                     ○ 简体中文
─────────────────────────────────
Quit
```

## What you get

- **A tri-state icon** — green: the server is running; grey: it is not; red: it is not
  responding, or authentication failed. Hover for the version, the PID and the number of
  active sessions.
- **Restart / stop / start from the menu**, without touching a terminal. This is the point:
  one click instead of quitting OpenCode and typing a command.
- **Recent sessions, one click away** — the 15 most recent sessions across *all* projects,
  running ones first, each with a relative timestamp. Click one and it opens in the OpenCode
  TUI, in the right directory.
- **English and Simplified Chinese**, switchable from the *Language* submenu; English by
  default. The choice is remembered in `settings.json`.
- **A single instance**, plus a desktop shortcut and a logon entry if you want them.

It is a **pure observer**. It never starts the server behind your back: it reads the
service's own registration file and talks to its local HTTP API. The server only changes when
you pick a menu item.

## Requirements

- Windows 10/11
- Python 3.9+ (developed and tested on 3.13.5)
- [`pystray`](https://pypi.org/project/pystray/) and [`Pillow`](https://pypi.org/project/Pillow/)
  — the only third-party dependencies, and only for the tray itself:
  ```
  pip install pystray Pillow
  ```
- OpenCode V2 installed locally (the `opencode2` command). The script finds it on `PATH`,
  falls back to `%APPDATA%\npm`, and checks that the major version is 2.

## Usage

```
pythonw opencode_tray.pyw                     # start the tray (no console window)
python  opencode_tray.pyw --selftest          # run the self test, no window, no side effects
python  opencode_tray.pyw --demo 5            # show the tray for 5 s, then exit
python  opencode_tray.pyw --install-startup   # start at logon  (HKCU\...\Run)
python  opencode_tray.pyw --uninstall-startup
python  opencode_tray.pyw --install-shortcut  # desktop shortcut
python  opencode_tray.pyw --uninstall-shortcut
```

You can also double-click `start-tray.cmd`. It checks that the `pythonw` on your `PATH` can
import `pystray` and `Pillow` before launching, because `pythonw` has no console and would
otherwise fail silently.

Starting it again while it is already running does something useful: the running instance is
asked to re-add its tray icon. Windows occasionally drops a notification icon (typically when
the logon entry fires before the shell is ready), and without this you would have to find and
kill the process to get it back.

The icon it draws is written to `opencode_tray.ico` (gitignored) when you create a shortcut,
so no binary asset is committed.

## Layout

| File | Purpose |
|---|---|
| `opencode_service.py` | Data layer: service health, session list, lifecycle, launching. **Standard library only.** |
| `opencode_tray.pyw`  | Tray UI: icon, menu, polling, autostart, desktop shortcut. Needs `pystray` + `Pillow`. |
| `i18n.py`            | Message catalogue (English / Simplified Chinese) and the saved preference. Standard library only. |
| `win_shortcut.py`    | Creates and reads Windows `.lnk` files through raw ctypes COM. Standard library only. |
| `start-tray.cmd`     | Convenience launcher that checks the dependencies first. |

The split is deliberate: `opencode_service.py`, `i18n.py` and `win_shortcut.py` are all
testable without a display, and their `--selftest` runs on a machine with no `pystray`.

## Verifying it works

```
python  i18n.py             --selftest   # 13 checks, incl. both catalogues defining the same keys
python  opencode_service.py --selftest   # 42 checks, incl. real end-to-end argument fidelity
python  opencode_tray.pyw   --selftest   # 63 checks, incl. the rendered Win32 menu state
python  win_shortcut.py     --selftest   # 9 checks, .lnk round-trip and AppUserModelID tagging
```

These are not smoke tests. Among other things they really start `cmd.exe` and assert that an
awkward directory name arrives byte-identical, and they query the *rendered* Win32 menu with
`GetMenuState` rather than trusting the descriptor, which always re-evaluates.

## Notes on the Windows quirks

Several things here look odd until you know why.

- **Notifications are named by an AppUserModelID, not by the interpreter.** A process started
  as `pythonw.exe` inherits that executable's `FileDescription`, so the balloons were headed
  "Python". The tray declares `qysnb.opencode-tray` and writes a Start Menu shortcut carrying
  the same ID, which is what lets the shell resolve the name. Both halves are required; the
  shortcut is created on first run and removed by `--uninstall-shortcut`.
- **Session launching goes through a temporary `.cmd` file.** Passing a directory straight to
  `subprocess` mangles it: the MSVCRT quoting layer turns literal quotes into part of the
  value, and `&` / `^` in a path get eaten by `cmd`. Writing the command into a batch file
  lets `cmd` parse it exactly once. Measured across 7 awkward directory names, this was the
  only variant that preserved all of them.
- **`cmd.exe` is preferred over PowerShell here** — for the launcher only, not for your own
  shell. `subprocess` with `shell=True` broke all seven test paths, and PowerShell's
  `Start-Process -ArgumentList` splits a directory containing spaces into several arguments.
- **Sessions open in Windows Terminal** when `wt.exe` is present, and the launcher sets
  `TERM=xterm-256color`. Windows Terminal does *not* set `TERM` itself, and without it the TUI
  falls back to degraded rendering.
- **The menu is rebuilt explicitly.** `pystray` does not build the menu when it is shown on
  Windows — it reuses a cached `HMENU` — so the menu is refreshed whenever relevant state
  changes, and never while it is on screen: rebuilding then destroys the handle Windows is
  tracking and breaks hover highlighting.
- **Autostart uses the registry, not the Startup folder**, because the Startup folder on some
  hardened machines refuses `.cmd` / `.bat` / `.vbs` outright.
- **The autostart entry and the shortcut pin the interpreter explicitly** (the `pythonw` next
  to the Python that ran the install), because `shutil.which` picks a different Python on
  machines with several installs.
- **The icon is re-added once, shortly after startup**, and again whenever another launch
  signals it. `Shell_NotifyIcon` can fail while the shell is still starting up, and `pystray`
  ignores the return value — the process then runs perfectly with no icon at all.

## Adding a language

Add a block to `_STRINGS` in `i18n.py` and an entry to `LANGUAGE_NAMES`. `i18n.py --selftest`
fails if the new block does not define exactly the same keys and placeholders as the others.

## License

MIT — see [LICENSE](LICENSE).
