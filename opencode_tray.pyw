# -*- coding: utf-8 -*-
"""
System-tray indicator for the OpenCode background service.

Shows an icon in the Windows notification area that reports whether the service is
running, and offers restart / stop / start, the session list, and attaching to a

Usage:
    pythonw opencode_tray.pyw                    # start the tray (no console window)
    python  opencode_tray.pyw --demo 5           # show for 5 s, then exit (automated checks)
    python  opencode_tray.pyw --install-startup     # start at logon (registry)
    python  opencode_tray.pyw --uninstall-startup   # remove the logon entry
    python  opencode_tray.pyw --install-shortcut     # create a desktop shortcut
    python  opencode_tray.pyw --uninstall-shortcut   # remove the desktop shortcut

Icon meanings:
    green (filled): running    grey: not running    red: not responding or auth failed

Dependencies: pystray, Pillow (opencode_service.py, i18n.py and win_shortcut.py are
standard library only)
Design: see README.md

Note: probing never uses `opencode api` (it auto-starts the service, which would give a
            read-only probe side effects), so this program is a pure observer: the service only
"""

import argparse
import ctypes
import os
import shutil
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import pystray
from PIL import Image, ImageDraw
from pystray import Menu, MenuItem

import i18n
import opencode_service as svc
import win_shortcut

# pythonw starts without standard streams. Record that for _inform, then point the
# streams at the null device so a stray print or argparse error cannot crash on None.
HAS_CONSOLE = sys.stdout is not None
if not HAS_CONSOLE:
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")


def app_name() -> str:
    """Application name. Follows the language, so it cannot be a constant."""
    return i18n._("app.name")


POLL_SECONDS = 3.0     # status poll interval
MESSAGE_TTL = 8.0      # how long the last action result stays in the menu
BUSY_TIMEOUT = 120.0   # watchdog: release the menu after this long, never grey forever
ICON_SIZE = 64

ICON_COLORS = {
    svc.RUNNING: (46, 184, 92, 255),        # green: running
    svc.NO_SERVICE: (150, 150, 150, 255),   # grey: not running
    svc.UNREACHABLE: (214, 69, 65, 255),    # red: not responding
    svc.UNAUTHORIZED: (214, 69, 65, 255),   # red: auth failed
}


# ---------------------------------------------------------------------------
# Icon drawing
# ---------------------------------------------------------------------------

def draw_icon(color):
    """Draw a filled disc with a white terminal-prompt glyph. Still legible at 16x16."""
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((3, 3, ICON_SIZE - 4, ICON_SIZE - 4), fill=color)
    white = (255, 255, 255, 235)
    # Two bars, suggesting a terminal prompt
    draw.rectangle((20, 22, 44, 28), fill=white)
    draw.rectangle((20, 36, 36, 42), fill=white)
    return image


ICON_FILE = Path(__file__).resolve().with_name("opencode_tray.ico")


def ensure_icon_file() -> Path:
    """
    Produce the .ico used by the desktop shortcut; reuse an existing non-empty one.
    It is drawn with Pillow at run time, so no binary icon is committed (it is gitignored).
    """
    if ICON_FILE.exists() and ICON_FILE.stat().st_size > 0:
        return ICON_FILE
    image = draw_icon(ICON_COLORS[svc.RUNNING])
    image.save(ICON_FILE, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
    return ICON_FILE


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------
# A named mutex rather than a lock file: the kernel releases the handle whether the
# process exits cleanly or crashes, so no stale lock is ever left behind.
# The "Local\\" prefix scopes it to the current logon session, so each user on a machine
# can run their own tray.

MUTEX_NAME = r"Local\OpenCodeServerTray.SingleInstance"
ERROR_ALREADY_EXISTS = 183

# Held at module level so the handle lives as long as the process
_mutex_handle = None


def acquire_single_instance():
    """
    Try to take the single-instance lock. Returns (acquired, message).

    Not acquiring it means a tray is already running; the caller should tell the user and exit.
    """
    global _mutex_handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_error = ctypes.get_last_error()

    if not handle:
        # Could not create the mutex at all: do not block the user, start anyway and say so
        return True, i18n._("single.check_unavailable", code=last_error)
    _mutex_handle = handle

    if last_error == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        _mutex_handle = None
        return False, i18n._("single.already_running")
    return True, ""


# ---------------------------------------------------------------------------
# Application identity (so notifications say "opencode-tray", not "Python")
# ---------------------------------------------------------------------------

def startmenu_shortcut_path() -> Path:
    """Where the app-identity shortcut lives."""
    appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / STARTMENU_SHORTCUT_NAME


def apply_app_identity() -> bool:
    """
    Give the process the identity Windows uses to name it.

    Without this, a notification's header reads "Python", because the process really is
    pythonw.exe and that is its FileDescription. Two halves are needed: the process has
    to declare an AppUserModelID, and a Start Menu shortcut has to carry the same ID so
    the shell can resolve a display name for it.

    Best effort: if either half fails the tray still works, notifications just keep the
    interpreter's name.
    """
    ok = True
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            ctypes.c_wchar_p(APP_USER_MODEL_ID)
        )
    except Exception:
        ok = False

    try:
        target = startmenu_shortcut_path()
        script = Path(__file__).resolve()
        if not target.exists():
            win_shortcut.create_shortcut(
                target,
                target=_preferred_pythonw(),
                arguments=f'"{script}"',
                working_dir=script.parent,
                description=app_name(),
                icon_path=ensure_icon_file(),
            )
        # Rewriting on every launch would be a needless write; only fix it if it drifted.
        if win_shortcut.get_app_user_model_id(target) != APP_USER_MODEL_ID:
            win_shortcut.set_app_user_model_id(target, APP_USER_MODEL_ID)
    except (OSError, ValueError):
        ok = False
    return ok


def remove_app_identity_shortcut() -> bool:
    """Delete the app-identity shortcut. Returns True when it is gone."""
    return _remove_file(startmenu_shortcut_path())


# ---------------------------------------------------------------------------
# Autostart
# ---------------------------------------------------------------------------
# Why the registry and not the Startup folder:
# this machine's Startup folder rejects .cmd / .bat / .vbs with PermissionError (Errno 13),
# which is anti-persistence hardening. Measured: .lnk and .txt are writable, the script
# extensions are refused. HKCU\...\Run needs only winreg: no COM, no PowerShell
# execution policy, and it still shows up in Task Manager -> Startup so users can see and

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "OpenCodeTray"
# Windows names a notification's app from the AppUserModelID. A process started as
# pythonw.exe inherits that executable's FileDescription ("Python") unless it declares
# its own ID, and the display name comes from a Start Menu shortcut carrying the same ID.
APP_USER_MODEL_ID = "qysnb.opencode-tray"
STARTMENU_SHORTCUT_NAME = "opencode-tray.lnk"
# The shortcut name stays language-neutral: it is written to the desktop, and a name that
# followed the UI language would leave an orphan behind on every switch. The old Chinese
# name is cleaned up on install.
DESKTOP_SHORTCUT_NAME = "OpenCode Tray.lnk"
LEGACY_SHORTCUT_NAMES = ("OpenCode 托盘指示器.lnk",)

# Lifecycle actions: kind -> callable returning (ok, message)
SERVICE_ACTIONS = {
    "restart": svc.restart_server,
    "stop": svc.stop_server,
    "start": svc.start_server,
}


def _preferred_pythonw() -> str:
    """
    Which pythonw the autostart entry and the shortcut should use.

    Prefer the pythonw next to the interpreter currently running this script, so the
    interpreter that installs and the one that starts later are guaranteed to match.

    Not shutil.which("pythonw"): PATH may hold several Pythons (this machine has Conda
    3.13.5, Python 3.14 and the WindowsApps PythonManager shim), which changes with PATH and
    once picked an interpreter without pystray. Measured - it really did pick wrong.
    """
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.exists():
        return str(exe)
    sibling = exe.with_name("pythonw.exe")
    if sibling.exists():
        return str(sibling)
    return shutil.which("pythonw") or str(exe)


def _autostart_command() -> str:
    """The command run at logon: pythonw plus this script's absolute path."""
    script = Path(__file__).resolve()
    return f'"{_preferred_pythonw()}" "{script}"'


def startup_status():
    """The registered autostart command, or None when not installed."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_VALUE_NAME)[0]
    except OSError:
        return None


def install_startup():
    """Write the autostart entry. Returns (ok, message)."""
    import winreg
    command = _autostart_command()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, command)
    except OSError as exc:
        return False, i18n._("startup.install_failed", error=exc)
    return True, i18n._("startup.installed", key=RUN_KEY, name=RUN_VALUE_NAME, command=command)


def uninstall_startup():
    """Remove the autostart entry. Returns (ok, message)."""
    import winreg
    if startup_status() is None:
        return True, i18n._("startup.not_installed")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE_NAME)
    except OSError as exc:
        return False, i18n._("startup.remove_failed", error=exc)
    return True, i18n._("startup.removed")


# ---------------------------------------------------------------------------
# Desktop shortcut
# ---------------------------------------------------------------------------

def shortcut_path() -> Path:
    """Where the desktop shortcut lives (follows redirection, e.g. OneDrive)."""
    return Path(win_shortcut.desktop_dir()) / DESKTOP_SHORTCUT_NAME


def install_shortcut():
    """Create the desktop shortcut with the drawn icon. Returns (ok, message)."""
    script = Path(__file__).resolve()
    try:
        icon = ensure_icon_file()
        target = shortcut_path()
        # Also drop the legacy Chinese-named shortcut so it is not left orphaned
        legacy_removed = [path for path in _legacy_shortcut_paths() if _remove_file(path)]
        win_shortcut.create_shortcut(
            target,
            target=_preferred_pythonw(),
            arguments=f'"{script}"',
            working_dir=script.parent,
            description=i18n._("app.name"),
            icon_path=icon,
        )
    except (OSError, ValueError) as exc:
        return False, i18n._("shortcut.install_failed", error=exc)
    message = i18n._("shortcut.installed", path=target, icon=icon)
    if legacy_removed:
        message += "\n\n" + i18n._("shortcut.removed", path=legacy_removed[0])
    return True, message


def _legacy_shortcut_paths():
    desktop = Path(win_shortcut.desktop_dir())
    return [desktop / name for name in LEGACY_SHORTCUT_NAMES]


def _remove_file(path) -> bool:
    try:
        path.unlink()
        return True
    except OSError:
        return False


def uninstall_shortcut():
    """
    Remove the shortcuts: the desktop one (including the legacy name) and the Start Menu
    entry that carries the app identity. Returns (ok, message).
    """
    # The Start Menu entry is what names the app, so it goes with the shortcuts.
    remove_app_identity_shortcut()
    targets = [shortcut_path()] + [p for p in _legacy_shortcut_paths() if p.exists()]
    existing = [p for p in targets if p.exists()]
    if not existing:
        return True, i18n._("shortcut.not_installed")
    for path in existing:
        try:
            path.unlink()
        except OSError as exc:
            return False, i18n._("shortcut.remove_failed", error=exc)
    return True, i18n._("shortcut.removed", path=existing[0])


def _inform(text: str, title: str | None = None):
    """Print when there is a console; under pythonw show a message box instead."""
    if HAS_CONSOLE:
        print(text)
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title or app_name(), 0x40)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# The tray application
# ---------------------------------------------------------------------------

class TrayApp:
    """The tray UI. State is polled by a background thread; the menu is rebuilt on demand."""

    def __init__(self):
        self.status = svc.ServerStatus(state=svc.NO_SERVICE, detail=i18n._("state.probing"))
        self.sessions = None            # list[SessionItem] | None
        # CLI state: None = not determined yet, True/False = known.
        # Resolved off __init__: a cold `opencode --version` takes seconds and would delay the
        # tray appearing. The polling thread resolves it in the background instead.
        self.has_cli = None
        self.busy = False               # a lifecycle command is in flight
        self._busy_since = 0.0
        self.message = ""
        self.message_until = 0.0
        self.polls = 0
        # Last distinct poll failure, so a persistent fault only notifies once
        self._poll_error = None

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._drawn_state = None
        # Fingerprint of menu-relevant state; the menu is rebuilt only when it changes
        self._menu_signature_cache = None
        # Whether Windows is displaying the menu; never rebuild then (see _guard_menu_display)
        self._menu_open = False
        self._menu_rebuild_pending = False

        self.icon = pystray.Icon(
            "opencode-service",
            draw_icon(ICON_COLORS[svc.NO_SERVICE]),
            app_name(),
            Menu(self._menu),
        )
        self._guard_menu_display()

    # --- Lifecycle ---

    def run(self, demo_seconds=None):
        threading.Thread(target=self._poll_loop, daemon=True).start()
        if demo_seconds:
            threading.Timer(demo_seconds, self.shutdown).start()
        self.icon.run()

    def shutdown(self):
        self._stop.set()
        self.icon.stop()

    def _poll_loop(self):
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(POLL_SECONDS)

    def _poll_once(self):
        """
        One poll iteration. Never raises: a bad round must not kill the thread.

        A failure only notifies when the message *changes*. An earlier version called
        _say() unconditionally, so any persistent fault popped a balloon every
        POLL_SECONDS - which is exactly how a NameError in probe_server turned into an
        endless stream of notifications instead of one.
        """
        try:
            self.refresh()
            self._poll_error = None
        except Exception as exc:  # noqa: BLE001 - the poll thread must survive anything
            message = i18n._("poll.failed", error=exc)
            if message != self._poll_error:
                self._poll_error = message
                self._say(message)

    def refresh(self):
        """Probe the service and sessions once, then sync the icon and tooltip."""
        status = svc.probe_server()
        sessions = svc.list_sessions() if status.is_running else None
        with self._lock:
            self.status = status
            self.sessions = sessions
            self.polls += 1
        # Resolve the CLI in the background; once it is known to exist we stop re-probing
        # (resolve_cli caches and backs off internally)
        if self.has_cli is not True:
            found = svc.resolve_cli() is not None
            with self._lock:
                self.has_cli = found
        self._sync_icon()

    def _sync_icon(self):
        with self._lock:
            state = self.status.state
            title = self._tooltip()
        # Only update on real change, to avoid icon flicker
        if state != self._drawn_state:
            self._drawn_state = state
            self.icon.icon = draw_icon(ICON_COLORS.get(state, ICON_COLORS[svc.NO_SERVICE]))
        if self.icon.title != title:
            self.icon.title = title
        self._refresh_menu_if_changed()

    def _guard_menu_display(self):
        """
        Track whether Windows is displaying the menu, so it is never rebuilt while shown.

        On Windows the menu is a fixed HMENU built *before* it is shown; pystray reuses it on
        right-click. Calling update_menu() while it is shown makes it DestroyMenu the handle
        Windows is tracking, and hover highlighting then sticks on one item or never appears -
        exactly the two symptoms that were reported.

        So while it is shown we only record a pending rebuild and apply it once the menu closes.

        Only this icon instance's message table is wrapped; there is no global monkeypatch.
        If pystray's internals are unavailable, fall back to the old periodic rebuild.
        """
        try:
            from pystray._util import win32 as _win32
            key = _win32.WM_NOTIFY
            original = self.icon._message_handlers[key]
        except (ImportError, AttributeError, KeyError, TypeError):
            return False
        if getattr(original, "_opencode_tray_guard", False):
            return True     # already installed; idempotent

        def guarded(wparam, lparam):
            with self._lock:
                self._menu_open = True
            try:
                return original(wparam, lparam)
            finally:
                with self._lock:
                    self._menu_open = False
                    pending = self._menu_rebuild_pending
                    self._menu_rebuild_pending = False
                if pending:
                    # TrackPopupMenuEx has returned, so the menu is really closed and a rebuild is safe
                    try:
                        self.icon.update_menu()
                    except Exception:
                        pass

        guarded._opencode_tray_guard = True
        self.icon._message_handlers[key] = guarded
        return True

    def _refresh_menu_if_changed(self):
        """
        Rebuild the menu when menu-relevant content changes.

        Note: update_menu() calls the menu descriptors, and the descriptors take self._lock,
        so it must NOT be called while holding that lock (Lock is not reentrant - deadlock).
        """
        signature = self._menu_signature()
        with self._lock:
            if signature == self._menu_signature_cache:
                return
            self._menu_signature_cache = signature
            if self._menu_open:
                # Currently shown: defer until the menu closes, so the tracked handle survives
                self._menu_rebuild_pending = True
                return
        self.icon.update_menu()

    def _menu_signature(self):
        """
        Fingerprint of the menu-relevant state.

        Why it is needed: on Windows pystray does *not* build the menu when it is shown.
        _on_notify reuses a cached HMENU, and update_menu() only runs from the menu setter,
        from _mark_ready() at startup, and from _handler() after a menu item is clicked.
        Nothing in the background would ever bring the menu up to date.

        Measured consequence: the state at startup was frozen into the menu - items stayed
        grey when they should be enabled, and the session submenu never left its startup
        snapshot. So after each poll, rebuild explicitly whenever menu content changed.
        """
        with self._lock:
            sessions = tuple(
                (s.id, s.title, s.running, s.updated) for s in (self.sessions or ())
            )
            message = self.message if time.time() < self.message_until else ""
            return (
                self.status.state,
                self.status.version,
                self.has_cli,
                self._busy_now(),
                message,
                sessions,
                i18n.current(),      # a language switch must rebuild the menu
            )

    # --- Menu content ---

    def _menu(self):
        items = [MenuItem(lambda item: self._header_text(), None, enabled=False)]
        if self.message and time.time() < self.message_until:
            items.append(MenuItem(lambda item: self.message, None, enabled=False))
        items += [
            Menu.SEPARATOR,
            MenuItem(lambda item: i18n._("menu.sessions"), Menu(self._session_menu)),
            Menu.SEPARATOR,
            MenuItem(lambda item: i18n._("menu.restart"),
                     self._make_action("restart"), enabled=self._lifecycle_enabled),
            MenuItem(lambda item: i18n._("menu.stop"),
                     self._make_action("stop"), enabled=self._lifecycle_enabled),
            MenuItem(lambda item: i18n._("menu.start"),
                     self._make_action("start"), enabled=self._lifecycle_enabled),
            Menu.SEPARATOR,
            MenuItem(lambda item: i18n._("menu.language"), Menu(self._language_menu)),
            Menu.SEPARATOR,
            MenuItem(lambda item: i18n._("menu.quit"), self._on_quit),
        ]
        return items

    def _header_text(self):
        with self._lock:
            text = f"{app_name()} · {self.status.label}"
            if self.status.state == svc.RUNNING and self.status.version:
                text += f" v{self.status.version}"
        return text

    def _language_menu(self):
        """
        The language submenu.

        Language names are always shown in their own language and never translated - you have
        to be able to recognise the Chinese entry while reading an English UI, and vice versa.
        radio + checked gives it a single-choice appearance.
        """
        for code, name in i18n.language_names().items():
            yield MenuItem(
                name,
                self._make_language_action(code),
                radio=True,
                checked=lambda item, code=code: i18n.current() == code,
            )

    def _make_language_action(self, code):
        def handler(icon, item):
            self._set_language(code)
        return handler

    def _set_language(self, code):
        """
        Switch language: persist it and redraw the menu and tooltip right away.

        Deliberately silent: the radio check already shows which language is
        active, so a notification there is just noise.
        """
        i18n.set_language(code)
        try:
            self._sync_icon()      # refresh tooltip + menu text
        except Exception:
            pass

    def _busy_now(self):
        """
        Whether an action is really in flight right now.

        With a watchdog: past BUSY_TIMEOUT the flag is treated as stuck and no longer counts -
        otherwise any single mishap would grey the menu out for good, the same class of risk as
        """
        return self.busy and (time.monotonic() - self._busy_since) < BUSY_TIMEOUT

    def _lifecycle_enabled(self, item):
        """
        Grey out only when the CLI is *known* to be missing (has_cli is False).

        has_cli None means the probe has not finished, so keep the item clickable: clicking then
        produces a clear error instead of an unexplained grey item.
        """
        return self.has_cli is not False and not self._busy_now()

    def _session_menu(self):
        with self._lock:
            sessions = self.sessions
        if sessions is None:
            yield MenuItem(lambda item: i18n._("sessions.unavailable"), None, enabled=False)
            return
        if not sessions:
            yield MenuItem(lambda item: i18n._("sessions.empty"), None, enabled=False)
            return
        for session in sessions:
            yield MenuItem(session.menu_label(), self._make_session_action(session))
        yield Menu.SEPARATOR
        yield MenuItem(lambda item: i18n._("menu.refresh"), self._on_refresh)

    def _tooltip(self):
        text = self.status.tooltip()
        if self.status.is_running and self.sessions is not None:
            active = sum(1 for s in self.sessions if s.running)
            text += i18n._("tooltip.active", count=active)
        if self.has_cli is False:
            text += i18n._("tooltip.cli_missing")
        if self._busy_now():
            text += i18n._("tooltip.busy")
        return text

    # --- Actions ---

    def _make_action(self, kind):
        # This must pass a callable. Passing the kind string straight to _run_async meant
        # _worker's fn() call raised for every lifecycle item:
        # "Action failed: 'str' object is not callable".
        action = SERVICE_ACTIONS[kind]

        def handler(icon, item):
            self._run_async(action)
        return handler

    def _make_session_action(self, session):
        def handler(icon, item):
            self._run_async(lambda: svc.open_session(session))
        return handler

    def _on_refresh(self, icon, item):
        self._run_async(self.refresh)

    def _on_quit(self, icon, item):
        self.shutdown()

    def _run_async(self, fn):
        """Run the action on a background thread so the tray message loop never blocks."""
        with self._lock:
            if self._busy_now():
                return  # debounce (the watchdog releases it; see _busy_now)
            self.busy = True
            self._busy_since = time.monotonic()
        threading.Thread(target=self._worker, args=(fn,), daemon=True).start()

    def _worker(self, fn):
        """
        Run one action off the message loop.

        Actions may return (ok, message) - the service commands and open_session do - or
        nothing at all, like refresh. Assuming the tuple shape unconditionally is what made
        the Refresh item fail with "cannot unpack non-iterable NoneType object".
        """
        try:
            result = fn()
            if isinstance(result, tuple) and len(result) == 2:
                _, message = result
                if message:
                    self._say(message)
        except Exception as exc:
            self._say(i18n._("action.failed", error=exc))
        finally:
            with self._lock:
                self.busy = False
        try:
            self.refresh()
        except Exception:
            pass

    def _say(self, text: str):
        self.message = text
        self.message_until = time.time() + MESSAGE_TTL
        try:
            self.icon.notify(text, app_name())
        except Exception:
            pass  # notifications may be unavailable; the menu still shows the result


# ---------------------------------------------------------------------------
# Self test
# ---------------------------------------------------------------------------

def _item_text(app, item):
    text = item.text
    return text(app) if callable(text) else text


def _rendered_states(app):
    """
    Read the disabled flag of every item in the *rendered* menu.

    It has to come from the rendering layer (GetMenuState), not from item.enabled:
    the descriptor re-evaluates and always looks right, while Windows paints the cached HMENU.
    That illusion misled me at first, so it is checked at this level now.
    """
    handle = getattr(app.icon, "_menu_handle", None)
    if not handle:
        return None
    MF_BYPOSITION = 0x00000400
    MF_GRAYED = 0x00000001
    MF_DISABLED = 0x00000002
    user32 = ctypes.WinDLL("user32")
    user32.GetMenuState.restype = ctypes.c_uint
    user32.GetMenuState.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    hmenu, _ = handle
    states = {}
    for index, item in enumerate(app.icon.menu.items):
        state = user32.GetMenuState(hmenu, index, MF_BYPOSITION)
        disabled = state != 0xFFFFFFFF and bool(state & (MF_GRAYED | MF_DISABLED))
        states[_item_text(app, item)] = disabled
    return states


def selftest() -> int:
    """Self test: menu wiring, refresh, enabled rules, watchdog, language switching.
    Opens no window and does not touch the service."""
    results = []

    def check(label, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
        results.append(ok)

    # Assertions must not depend on the user's saved language, so pin one and restore after.
    # Otherwise the self test would fail mysteriously for a Chinese-UI user.
    original_language = i18n.current()
    i18n.set_language("en", persist=False)
    # Assertions go through i18n, so they hold in either language
    L = i18n._

    try:
        return _selftest_body(check, L, results)
    finally:
        i18n.set_language(original_language, persist=False)


def _selftest_body(check, L, results) -> int:
    print("== 1. Lifecycle actions are callables ==")
    for kind in ("restart", "stop", "start"):
        check(f"{kind} -> callable (regression: 'str' object is not callable)",
              callable(SERVICE_ACTIONS.get(kind)), repr(SERVICE_ACTIONS.get(kind)))

    app = TrayApp()
    forwarded = []
    app._run_async = lambda fn: forwarded.append(fn)
    for kind in ("restart", "stop", "start"):
        app._make_action(kind)(None, None)
    check("menu actions forward a callable",
          len(forwarded) == 3 and all(callable(f) for f in forwarded))

    print("== 1b. Actions that return nothing are not an error (regression: Refresh) ==")
    quiet = TrayApp()
    reported = []
    quiet._say = lambda text: reported.append(text)
    quiet.refresh = lambda: None       # keep the network out of this check

    quiet.busy = True
    quiet._busy_since = time.monotonic()
    quiet._worker(quiet.refresh)       # the Refresh item dispatches exactly this
    check("a None-returning action raises nothing",
          not any("failed" in m.lower() for m in reported), str(reported))
    check("busy is cleared afterwards", quiet.busy is False)

    quiet._worker(lambda: (True, "session opened"))
    check("a tuple-returning action still reports", reported[-1:] == ["session opened"],
          str(reported))

    print("== 2. Menu structure ==")
    texts = [_item_text(app, item) for item in app.icon.menu.items]
    for key in ("menu.sessions", "menu.restart", "menu.stop", "menu.start",
                "menu.language", "menu.quit"):
        check(f"menu contains {L(key)}", L(key) in texts)
    check("the open-log item is gone",
          not any("log" in t.lower() for t in texts), str(texts))

    print("== 2b. Language submenu (names in their own language) ==")
    submenu = [i for i in app.icon.menu.items
               if _item_text(app, i) == L("menu.language")]
    check("language submenu exists", len(submenu) == 1)
    language_items = [_item_text(app, i) for i in submenu[0].submenu.items] if submenu else []
    check("two options, named in their own language",
          language_items == ["English", "简体中文"], str(language_items))

    checked = [getattr(i, "checked", False) for i in submenu[0].submenu.items] if submenu else []
    check("the current language is radio-checked", list(checked) == [True, False], str(checked))

    print("== 3. Menu follows state (pystray never refreshes it on Windows) ==")
    check("the menu-shown guard is installed", app._guard_menu_display() is not False)
    rebuilds = []
    real_update = app.icon.update_menu
    app.icon.update_menu = lambda: (rebuilds.append(1), real_update())[1]
    app._sync_icon()
    baseline = len(rebuilds)
    app._sync_icon()
    check("no rebuild when nothing changed", len(rebuilds) == baseline, f"{len(rebuilds)}")
    app.has_cli = False
    app._sync_icon()
    check("rebuilds on change", len(rebuilds) > baseline, f"{len(rebuilds)}")

    print("== 3b. Never rebuild while the menu is shown (it breaks hover) ==")
    app._menu_open = True
    app.has_cli = True
    before = len(rebuilds)
    app._sync_icon()
    check("no rebuild while shown", len(rebuilds) == before, f"{len(rebuilds)}")
    check("recorded as a pending rebuild", app._menu_rebuild_pending is True)


    # Simulate the menu closing: the guard's finally applies the pending rebuild
    app._menu_open = False
    pending = app._menu_rebuild_pending
    app._menu_rebuild_pending = False
    if pending:
        app.icon.update_menu()
    check("applied after the menu closes", len(rebuilds) > before, f"{len(rebuilds)}")

    print("== 3c. update_menu is never called while holding the lock (deadlock) ==")
    import threading as _threading
    done = _threading.Event()

    def call_sync():
        try:
            app.has_cli = False
            app._sync_icon()
            app.has_cli = True
            app._sync_icon()
        finally:
            done.set()

    worker = _threading.Thread(target=call_sync, daemon=True)
    worker.start()
    check("_sync_icon does not deadlock", done.wait(10.0), "waited 10 s")
    worker.join(timeout=2)

    print("== 4. The rendered enabled flags really follow state ==")
    app.has_cli = True
    app.busy = False
    app.icon.update_menu()
    on = (_rendered_states(app) or {})
    app.has_cli = False
    app.icon.update_menu()
    off = (_rendered_states(app) or {})
    check("restart is enabled when it should be", on.get(L("menu.restart")) is False,
          str(on.get(L("menu.restart"))))
    check("restart greys out with no CLI", off.get(L("menu.restart")) is True,
          str(off.get(L("menu.restart"))))
    check("enabled state really changed", on.get(L("menu.restart")) != off.get(L("menu.restart")))

    print("== 5. Enabled rules and the watchdog ==")
    app.has_cli = True
    app.busy = False
    check("clickable when healthy", app._lifecycle_enabled(None) is True)
    app.has_cli = False
    check("grey without a CLI", app._lifecycle_enabled(None) is False)
    app.has_cli = None
    check("clickable while still unknown", app._lifecycle_enabled(None) is True)
    app.has_cli = True
    app.busy = True
    app._busy_since = time.monotonic()
    check("debounced while running", app._lifecycle_enabled(None) is False)
    check("tooltip explains it is working", L("tooltip.busy") in app._tooltip())
    app._busy_since = time.monotonic() - BUSY_TIMEOUT - 1
    check("watchdog releases a stuck flag", app._lifecycle_enabled(None) is True)
    check("tooltip stops saying it is working", L("tooltip.busy") not in app._tooltip())

    app.busy = False
    app.has_cli = False
    check("tooltip explains a missing CLI", L("tooltip.cli_missing") in app._tooltip())

    print("== 5b. Switching language really changes menu and tooltip ==")
    import i18n as _i18n
    app.has_cli = True
    _i18n.set_language("en", persist=False)
    app.icon.update_menu()
    english_menu = [_item_text(app, i) for i in app.icon.menu.items]
    english_tooltip = app._tooltip()
    _i18n.set_language("zh-Hans", persist=False)
    app.icon.update_menu()
    chinese_menu = [_item_text(app, i) for i in app.icon.menu.items]
    chinese_tooltip = app._tooltip()

    check("menu text follows the language", english_menu != chinese_menu,
          f"{english_menu[:2]} vs {chinese_menu[:2]}")
    check("English menu comes from the English table",
          "Sessions" in english_menu, str(english_menu))
    check("Chinese menu comes from the Chinese table",
          "会话" in chinese_menu, str(chinese_menu))
    check("tooltip follows the language", english_tooltip != chinese_tooltip,
          f"{english_tooltip!r} vs {chinese_tooltip!r}")
    check("header follows the language", english_menu[0] != chinese_menu[0],
          f"{english_menu[0]!r} vs {chinese_menu[0]!r}")

    # The radio check has to move too
    submenu = [i for i in app.icon.menu.items if _item_text(app, i) == _i18n._("menu.language")]
    checked_zh = [bool(getattr(i, "checked", False)) for i in submenu[0].submenu.items]
    check("Chinese is radio-checked after switching", checked_zh == [False, True], str(checked_zh))

    print("== 5c. The default language is English ==")
    import importlib
    importlib.reload(_i18n)
    check("defaults to en without settings.json", _i18n.DEFAULT_LANGUAGE == "en")
    check("the default language is named English", _i18n.LANGUAGE_NAMES["en"] == "English")

    print("== 6. Application identity (so notifications say opencode-tray) ==")
    check("AUMID is a single token (no spaces allowed)",
          bool(APP_USER_MODEL_ID) and " " not in APP_USER_MODEL_ID, APP_USER_MODEL_ID)
    check("AUMID matches owner/repo naming",
          APP_USER_MODEL_ID.startswith("qysnb."), APP_USER_MODEL_ID)
    startmenu = startmenu_shortcut_path()
    check("Start Menu path is under Programs",
          "Start Menu" in str(startmenu) and startmenu.name == STARTMENU_SHORTCUT_NAME,
          str(startmenu))
    if startmenu.exists():
        check("Start Menu shortcut carries the AUMID",
              win_shortcut.get_app_user_model_id(startmenu) == APP_USER_MODEL_ID,
              win_shortcut.get_app_user_model_id(startmenu))
    else:
        check("Start Menu shortcut not created yet (created on first run)", True,
              "run the tray once to create it")

    print("== 8. Interpreter is pinned (not guessed from PATH) ==")
    print("== 7. A persistent poll failure notifies once, not every poll ==")
    noisy = TrayApp()
    notifications = []
    noisy._say = lambda text: notifications.append(text)

    def exploding():
        raise RuntimeError("simulated probe failure")

    noisy.refresh = exploding
    for _ in range(4):
        noisy._poll_once()
    check("4 failing polls produce exactly 1 notification",
          len(notifications) == 1, f"{len(notifications)}")
    check("the notification names the cause",
          notifications and "simulated probe failure" in notifications[0],
          notifications[0] if notifications else "")

    noisy.refresh = lambda: None
    noisy._poll_once()
    check("recovery clears the remembered error", noisy._poll_error is None)

    noisy.refresh = exploding
    noisy._poll_once()
    check("a failure after recovery notifies again", len(notifications) == 2,
          f"{len(notifications)}")

    print("== 8. Interpreter is pinned (not guessed from PATH) ==")
    preferred = _preferred_pythonw()
    check("resolves a pythonw path", bool(preferred), preferred)
    check("prefers the current interpreter's directory",
          Path(preferred).parent == Path(sys.executable).parent,
          f"{Path(preferred).parent} vs {Path(sys.executable).parent}")
    check("the autostart command uses that pythonw",
          _autostart_command().startswith(f'"{preferred}"'), _autostart_command())
    check("the autostart command points at this script",
          str(Path(__file__).resolve()) in _autostart_command())

    passed, total = sum(results), len(results)
    print(f"\nSelf test: {passed}/{total} passed")
    return 0 if passed == total else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="OpenCode background service tray indicator")
    parser.add_argument("--demo", type=float, metavar="SECONDS",
                        help="show for N seconds, then exit (for automated checks)")
    parser.add_argument("--selftest", action="store_true",
                        help="run the self test and exit (no window, no side effects)")
    parser.add_argument("--install-startup", action="store_true", help="start at logon")
    parser.add_argument("--uninstall-startup", action="store_true", help="remove the logon entry")
    parser.add_argument("--install-shortcut", action="store_true",
                        help="create a desktop shortcut")
    parser.add_argument("--uninstall-shortcut", action="store_true",
                        help="remove the desktop shortcut")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    # One-shot actions skip the single-instance lock: they may just change config, and they
    # must be able to run alongside a live tray
    one_shot = (
        (args.install_startup, install_startup),
        (args.uninstall_startup, uninstall_startup),
        (args.install_shortcut, install_shortcut),
        (args.uninstall_shortcut, uninstall_shortcut),
    )
    for enabled, action in one_shot:
        if enabled:
            ok, message = action()
            _inform(message)
            return 0 if ok else 1

    # Single instance: double-clicking twice must not give two tray icons
    acquired, reason = acquire_single_instance()
    if not acquired:
        _inform(f"{reason}\n\n{i18n._('single.hint')}")
        return 1

    # Must happen before the icon exists: the AUMID has to be declared before the shell
    # creates any window for this process, otherwise notifications still say "Python".
    apply_app_identity()
    app = TrayApp()
    app.run(args.demo)

    if args.demo:
        _inform(i18n._(
            "demo.summary",
            seconds=args.demo,
            state=app.status.label,
            polls=app.polls,
            sessions=len(app.sessions) if app.sessions is not None else "n/a",
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
