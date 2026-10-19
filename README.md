# opencode-tray

**English** · [简体中文](README.zh-Hans.md)

A Windows system-tray indicator for the **OpenCode** background service.

Right-click the tray icon to see whether the service is up, restart / stop / start it,
and jump straight into any of your OpenCode sessions. The interface is available in
English (default) and Simplified Chinese — switch it from the *Language* submenu; the
choice is remembered in `settings.json`.

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

## What it does

- **Tri-state icon** — green: the service is running; grey: not running; red: not responding
  or authentication failed. Hover for a tooltip with version, PID and active session count.
- **Session submenu** — the 15 most recent sessions across *all* projects, running ones first,
  with a relative timestamp. Click one to attach to it in a new terminal window.
- **Service lifecycle** — restart / stop / start, straight from the menu.
- **Two languages** — English by default, Simplified Chinese one click away.
- **Runs in the tray, not in your way** — a single instance (named mutex), and it can install
  itself to start at logon plus a desktop shortcut.

It is a **pure observer**. It never starts the background service behind your back: it reads the
service's own registration file and talks to its local HTTP API. The service only changes when
you pick a menu item.

## Requirements

- Windows 10/11
- Python 3.9+ (developed and tested on 3.13.5)
- [`pystray`](https://pypi.org/project/pystray/) and [`Pillow`](https://pypi.org/project/Pillow/)
  — the only third-party dependencies, and only for the tray itself:
  ```
  pip install pystray Pillow
  ```
- OpenCode V2 installed locally (the `opencode2` command). The script locates it on `PATH`,
  falling back to `%APPDATA%\npm`, and verifies that the major version is 2.

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

The split is deliberate: `opencode_service.py`, `i18n.py` and `win_shortcut.py` are all testable
without a display, and their `--selftest` runs on a machine with no `pystray` installed.

## Verifying it works

```
python  i18n.py             --selftest   # 13 checks, incl. both catalogues defining the same keys
python  opencode_service.py --selftest   # 35 checks, incl. real end-to-end argument fidelity
python  opencode_tray.pyw   --selftest   # 44 checks, incl. the rendered Win32 menu state
python  win_shortcut.py     --selftest   # 7 checks, .lnk round-trip incl. non-ASCII
```

These are not smoke tests. Among other things they really start `cmd.exe` and assert that an
awkward directory name arrives byte-identical, and they query the *rendered* Win32 menu with
`GetMenuState` rather than trusting the descriptor, which always re-evaluates.

## Notes on the Windows quirks

Several things here look odd until you know why.

- **Session launching goes through a temporary `.cmd` file.** Passing a directory straight to
  `subprocess` mangles it: the MSVCRT quoting layer turns literal quotes into part of the value,
  and `&` / `^` in a path get eaten by `cmd`. Writing the command into a batch file lets `cmd`
  parse it exactly once. Measured across 7 awkward directory names, this was the only variant
  that preserved all of them.
- **`cmd.exe` is preferred over PowerShell.** `subprocess` with `shell=True` broke all seven test
  paths, and PowerShell's `Start-Process -ArgumentList` splits a directory containing spaces into
  several arguments.
- **Sessions open in Windows Terminal** when `wt.exe` is present, and the launcher sets
  `TERM=xterm-256color`. Windows Terminal does *not* set `TERM` itself, and without it the TUI
  falls back to degraded rendering.
- **The menu is rebuilt explicitly.** `pystray` does not build the menu when it is shown on
  Windows — it reuses a cached `HMENU` — so the menu is refreshed whenever relevant state
  changes, and never while it is on screen: rebuilding then destroys the handle Windows is
  tracking and breaks hover highlighting.
- **Autostart uses the registry, not the Startup folder**, because the Startup folder on some
  hardened machines refuses `.cmd` / `.bat` / `.vbs` outright.
- **The autostart entry and the shortcut pin the interpreter explicitly** (the `pythonw` next to
  the Python that ran the install), because `shutil.which` picks a different Python on machines
  with several installs.

## Adding a language

Add a block to `_STRINGS` in `i18n.py` and an entry to `LANGUAGE_NAMES`. `i18n.py --selftest`
fails if the new block does not define exactly the same keys and placeholders as the others.

## License

MIT — see [LICENSE](LICENSE).
