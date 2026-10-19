# -*- coding: utf-8 -*-
"""
Probe layer for the OpenCode background service (data layer, no GUI).

Reads the local service registration file and its HTTP API. It provides:
  - service health (running / not responding / auth failed / not running)
  - the global session list, with running state
  - the service lifecycle (restart / stop / start)
  - attaching to a session in a new terminal

Usage:
    python opencode_service.py --selftest   # real probe + pure-function assertions, no window

Dependencies: none (standard library only). The GUI lives in opencode_tray.pyw.

Design notes:
  - Probing never uses `opencode api`: that command auto-starts the service when none is
    healthy, which would give a read-only probe side effects.
  - The session list uses HTTP `/api/session` (global), not `opencode session list`
    (current project only).
  - Only HTTP Basic works for auth, with the fixed username "opencode".
  - Subprocesses always go through cmd.exe: this machine's PowerShell execution policy
    blocks opencode.ps1.
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import i18n

# pythonw has no stdout/stderr, so every write has to tolerate None
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Service states
RUNNING = "running"            # service.json present and /api/info answers
UNREACHABLE = "unreachable"    # service.json present but timeout / refused / other HTTP error
UNAUTHORIZED = "unauthorized"  # service.json present but the password is stale (401)
NO_SERVICE = "no_service"      # service.json missing or corrupt

# HTTP probe outcomes
HTTP_OK = "ok"
HTTP_UNAUTHORIZED = "unauthorized"
HTTP_UNREACHABLE = "unreachable"

# State -> message key (the text lives in i18n.py and follows the language)
STATE_KEYS = {
    RUNNING: "state.running",
    UNREACHABLE: "state.unreachable",
    UNAUTHORIZED: "state.unauthorized",
    NO_SERVICE: "state.no_service",
}

DETAIL_KEYS = {
    UNREACHABLE: "state.detail.unreachable",
    UNAUTHORIZED: "state.detail.unauthorized",
    NO_SERVICE: "state.detail.no_service",
}


def state_label(state: str) -> str:
    """Human-readable name for a state, in the current language."""
    return i18n._(STATE_KEYS.get(state, state))


def state_detail(state: str) -> str:
    """Extra detail for a state (why it is unavailable)."""
    key = DETAIL_KEYS.get(state)
    return i18n._(key) if key else ""


DEFAULT_TIMEOUT = 5.0   # per-request HTTP timeout, seconds
SESSION_LIMIT = 15      # how many sessions the tray menu lists
TITLE_MAX = 40          # session title truncation length
CLI_TIMEOUT = 90        # CLI timeout; starting/stopping the service can be slow

# Do not pop a console window for subprocesses (Windows only)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def state_file_path() -> Path:
    """The service registration file: records url / pid / password."""
    return Path(os.path.expanduser("~")) / ".local" / "state" / "opencode" / "service.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ServerStatus:
    """The result of one service probe."""
    state: str
    version: str | None = None
    pid: int | None = None
    url: str | None = None
    detail: str = ""

    @property
    def is_running(self) -> bool:
        return self.state == RUNNING

    @property
    def label(self) -> str:
        return state_label(self.state)

    def tooltip(self) -> str:
        head = i18n._("tooltip.base", app=i18n._("app.name"), label=self.label)
        if self.state == RUNNING:
            if self.version:
                head += i18n._("tooltip.version", version=self.version)
            if self.pid:
                head += i18n._("tooltip.pid", pid=self.pid)
        elif self.detail:
            head += i18n._("tooltip.detail", detail=self.detail)
        return head


@dataclass
class SessionItem:
    """A session, as shown in the tray menu."""
    id: str
    title: str
    directory: str
    running: bool
    updated: int  # epoch milliseconds

    def menu_label(self) -> str:
        """The text shown in the menu."""
        title = self.title.strip() or i18n._("session.untitled")
        if len(title) > TITLE_MAX:
            title = title[:TITLE_MAX] + "…"
        mark = "●" if self.running else "○"
        text = f"{mark} {title} · {self.id[-6:]}"
        if not self.running and self.updated:
            text += f" · {_ago(self.updated)}"
        # Windows treats & as a mnemonic prefix, so it has to be escaped
        return text.replace("&", "&&")


def _ago(epoch_ms: int) -> str:
    """Turn epoch milliseconds into a relative time such as "3m ago"."""
    delta = time.time() - epoch_ms / 1000.0
    if delta < 60:
        return i18n._("time.just_now")
    if delta < 3600:
        return i18n._("time.minutes_ago", count=int(delta // 60))
    if delta < 86400:
        return i18n._("time.hours_ago", count=int(delta // 3600))
    return i18n._("time.days_ago", count=int(delta // 86400))


# ---------------------------------------------------------------------------
# Service registration file
# ---------------------------------------------------------------------------

def read_service(path=None) -> dict | None:
    """
    Read service.json. Returns None when the file is missing, unparsable or
    incomplete (treated as "not running"; nothing raises).
    """
    target = Path(path) if path else state_file_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("url") or not data.get("password"):
        return None
    return data


# ---------------------------------------------------------------------------
# HTTP probes
# ---------------------------------------------------------------------------

def _auth_header(password: str) -> str:
    """The local API only accepts HTTP Basic, with the fixed username "opencode"."""
    token = base64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
    return "Basic " + token


def fetch_json(url: str, password: str, timeout: float = DEFAULT_TIMEOUT):
    """
    Issue an authenticated GET. Returns (outcome, payload|None), where outcome is
    outcome ∈ {HTTP_OK, HTTP_UNAUTHORIZED, HTTP_UNREACHABLE}。
    """
    request = urllib.request.Request(url, headers={"Authorization": _auth_header(password)})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HTTP_OK, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return HTTP_UNAUTHORIZED, None
        return HTTP_UNREACHABLE, None
    except (urllib.error.URLError, OSError, ValueError):
        # URLError (refused / timeout / DNS), OSError and JSON decode errors (ValueError)
        # all count as "unreachable"
        return HTTP_UNREACHABLE, None


def classify(service, outcome: str) -> str:
    """
    Pure function: decide the service state from the registration file and the probe outcome.

    A None service means service.json is missing or corrupt, which counts as not running
    regardless of the probe outcome.
    """
    if service is None:
        return NO_SERVICE
    if outcome == HTTP_OK:
        return RUNNING
    if outcome == HTTP_UNAUTHORIZED:
        return UNAUTHORIZED
    return UNREACHABLE


def probe_server(timeout: float = DEFAULT_TIMEOUT, path=None) -> ServerStatus:
    """
    Probe the service once. service.json is re-read every round: a restart changes both
    the port and the password.
    """
    service = read_service(path)
    if service is None:
        return ServerStatus(state=NO_SERVICE, detail=STATE_DETAILS[NO_SERVICE])

    url = service["url"]
    outcome, info = fetch_json(url.rstrip("/") + "/api/info", service["password"], timeout)
    state = classify(service, outcome)

    version = service.get("version")
    pid = service.get("pid")
    if state == RUNNING and isinstance(info, dict):
        version = info.get("version") or version
        pid = info.get("pid") or pid

    return ServerStatus(
        state=state,
        version=version,
        pid=pid,
        url=url,
        detail="" if state == RUNNING else state_detail(state),
    )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def list_sessions(limit: int = SESSION_LIMIT, timeout: float = DEFAULT_TIMEOUT, path=None):
    """
    List global sessions, running ones first, then by most recently updated, up to limit.
    Returns None when the service is unavailable or the request fails, so the caller can
    show "session list unavailable".
    """
    service = read_service(path)
    if service is None:
        return None
    base = service["url"].rstrip("/")
    password = service["password"]

    outcome, payload = fetch_json(base + "/api/session", password, timeout)
    if outcome != HTTP_OK or not isinstance(payload, dict):
        return None

    # Set of running sessions (used for the filled bullet)
    running = set()
    active_outcome, active_payload = fetch_json(base + "/api/session/active", password, timeout)
    if active_outcome == HTTP_OK and isinstance(active_payload, dict):
        for session_id, info in (active_payload.get("data") or {}).items():
            if isinstance(info, dict) and info.get("type") == "running":
                running.add(session_id)

    items = []
    for row in payload.get("data") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        stamp = row.get("time") or {}
        items.append(SessionItem(
            id=row["id"],
            title=row.get("title") or i18n._("session.untitled"),
            directory=(row.get("location") or {}).get("directory") or "",
            running=row["id"] in running,
            updated=int(stamp.get("updated") or stamp.get("created") or 0),
        ))

    items.sort(key=lambda s: (not s.running, -s.updated))
    return items[:limit]


# ---------------------------------------------------------------------------
# Locating the CLI
# ---------------------------------------------------------------------------
# Important: a failed probe must NEVER be cached forever.
# `opencode2 --version` starts a large native binary; a cold start (at logon, on a busy
# machine) can take seconds or time out. Caching "not found" permanently meant that a
# single failed probe greyed out the tray's service menu until the tray was restarted.
# Now: a success is kept; a failure is retried after a backoff.

CLI_PROBE_TIMEOUT = 20.0    # timeout for one `--version` probe, seconds
CLI_RETRY_SECONDS = 60.0    # retry interval after a failure, seconds

_cli_cache = {"path": None, "next_check": 0.0}


def _probe_cli_version(exe: str) -> str | None:
    """Run `--version`; return the version string, or None on failure."""
    try:
        proc = subprocess.run(
            ["cmd.exe", "/c", exe, "--version"],
            capture_output=True, text=True, timeout=CLI_PROBE_TIMEOUT,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _VERSION_RE.search((proc.stdout or "") + (proc.stderr or ""))
    return match.group(0) if match else None


def _cli_candidates():
    """
    Yield CLI candidate paths in order.

    PATH first, then an explicit fallback to the npm global directory: PATH may be
    incomplete in environments such as logon autostart, so which alone is not enough.
    """
    seen = set()
    for name in ("opencode2", "opencode"):
        exe = shutil.which(name)
        if exe and exe.lower() not in seen:
            seen.add(exe.lower())
            yield exe

    roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    for name in ("opencode2.cmd", "opencode.cmd"):
        candidate = Path(roaming) / "npm" / name
        if candidate.exists() and str(candidate).lower() not in seen:
            seen.add(str(candidate).lower())
            yield str(candidate)


def _find_cli() -> str | None:
    """Return the candidate whose major version is 2 (V1 owns the opencode name here)."""
    for exe in _cli_candidates():
        version = _probe_cli_version(exe)
        if version and version.split(".")[0] == "2":
            return exe
    return None


def resolve_cli(refresh: bool = False) -> str | None:
    """
    Locate a usable OpenCode V2 CLI; return its full path, or None.

    A successful resolution is reused indefinitely; a failure is only remembered for
    CLI_RETRY_SECONDS and then retried, so one transient failure (a cold-start timeout, an
    incomplete PATH) cannot grey out the menu for good.
    """
    now = time.monotonic()
    cached = _cli_cache["path"]
    if not refresh:
        if cached and os.path.exists(cached):
            return cached
        if now < _cli_cache["next_check"]:
            return None     # failed recently; wait out the backoff

    found = _find_cli()
    _cli_cache["path"] = found
    _cli_cache["next_check"] = now + CLI_RETRY_SECONDS
    return found


def _run_service(subcommand: str, timeout: float = CLI_TIMEOUT):
    """Run `opencode service <subcommand>`. Returns (ok, message)."""
    cli = resolve_cli()
    if cli is None:
        return False, i18n._("cli.not_found")
    try:
        proc = subprocess.run(
            ["cmd.exe", "/c", cli, "service", subcommand],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return False, i18n._("service.timeout", command=subcommand, seconds=int(timeout))
    except (OSError, subprocess.SubprocessError) as exc:
        return False, i18n._("service.failed_exec", command=subcommand, error=exc)

    if proc.returncode != 0:
        lines = [ln for ln in ((proc.stderr or "") + (proc.stdout or "")).splitlines() if ln.strip()]
        tail = lines[-1] if lines else "(no output)"
        return False, i18n._("service.failed_code", command=subcommand,
                             code=proc.returncode, tail=tail)
    return True, i18n._("service.done", command=subcommand)


def restart_server():
    return _run_service("restart")


def stop_server():
    return _run_service("stop")


def start_server():
    return _run_service("start")


# ---------------------------------------------------------------------------
# Attaching to a session in a new terminal
# ---------------------------------------------------------------------------

# When the tray is started by Explorer or the Run key, TERM is absent from the
# environment; measured, the TUI then falls back to degraded rendering and does not
# look like what the user normally sees. Set it to the local daily value explicitly.
LAUNCH_TERM = "xterm-256color"


def bat_quote(text: str) -> str:
    """
    Pure function: quote one argument for batch syntax.

    In a batch file % starts variable expansion and must be doubled for a literal %;
    inside quotes & ^ ( ) ; need no special handling.
    """
    return '"' + text.replace("%", "%%") + '"'


def build_launcher_script(cli: str, session_id: str, directory: str,
                          term: str | None = LAUNCH_TERM) -> str:
    """
    Pure function: build the contents of the temporary launcher (.cmd).

    Why the command is not passed straight to cmd as arguments (measured with a probe):
      - subprocess quotes arguments by MSVCRT rules, while cmd.exe has its own quoting.
          Quoting by hand made subprocess escape the quotes again, so they ended up as
          literal characters and the directory gained a trailing space; opencode reported
          `ENOENT: no such file or directory, chdir '...test '`.
      - Passing the raw string fixes the quotes, but a directory containing & or ^ is then
          eaten by cmd as a separator or escape (measured: `A&B` -> `A`, `A^B` -> `AB`).
      - Writing the directory into the batch file lets cmd parse it exactly once.
          Measured over 7 awkward directory names (spaces / & / ^ / % / ( ) / ;): all arrived intact.
    """
    lines = ["@echo off"]
    if term:
        lines.append(f"set TERM={term}")
    lines.append(f"cd /d {bat_quote(directory)}")
    lines.append(f"{bat_quote(cli)} --session {session_id} {bat_quote(directory)}")
    return "\n".join(lines) + "\n"


def build_terminal_command(launcher: Path):
    """
    Pure function: return (argv, creationflags) - which terminal opens the launcher.

    Prefer Windows Terminal: this machine's default terminal is the classic conhost
    (the delegation keys are all zero) while the user actually works in Windows Terminal.
    conhost has weaker fonts and VT support, and the same launcher rendered visibly
    differently there. Without wt.exe, fall back to conhost via start.
    """
    wt = shutil.which("wt")
    if wt:
        # cmd.exe /k keeps the window open after the session exits, so errors stay visible
        return [wt, "cmd.exe", "/k", str(launcher)], 0
    return (
        ["cmd.exe", "/c", "start", "", "cmd.exe", "/k", str(launcher)],
        getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )


def _launcher_path(session_id: str) -> Path:
    """
    Path for the temporary launcher. Kept in the temp directory so this path itself
    contains no metacharacters.

    Named by session id, so one session keeps one file and they do not pile up.
    """
    import tempfile
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "_-")
    return Path(tempfile.gettempdir()) / f"opencode_tray_session_{safe}.cmd"


def _write_launcher(path: Path, script: str) -> None:
    """Write the launcher. cmd.exe reads batch files in the ANSI code page, so non-ASCII
    paths have to be written as mbcs."""
    try:
        path.write_text(script, encoding="mbcs")
    except (LookupError, UnicodeEncodeError):
        path.write_text(script, encoding="utf-8")


def open_session(session: SessionItem):
    """Attach to this session in a new terminal window. Returns (ok, message)."""
    cli = resolve_cli()
    if cli is None:
        return False, i18n._("cli.not_found")

    launcher = _launcher_path(session.id)
    try:
        _write_launcher(launcher, build_launcher_script(cli, session.id, session.directory))
    except OSError as exc:
        return False, i18n._("session.launcher_write_failed", error=exc)

    cwd = session.directory if session.directory and os.path.isdir(session.directory) else None
    argv, flags = build_terminal_command(launcher)
    try:
        subprocess.Popen(argv, cwd=cwd, creationflags=flags)
    except OSError as exc:
        return False, i18n._("session.terminal_failed", error=exc)
    return True, i18n._("session.opened", id=session.id[-6:])


# ---------------------------------------------------------------------------
# Self test
# ---------------------------------------------------------------------------

def _check_cli_retry() -> list:
    """
    Regression test: a failed CLI probe must be retried automatically.

    A permanent cache of the failure meant that one cold-start timeout at logon kept the
    tray's service menu greyed out forever. This asserts that:
      - probes are not repeated inside the backoff window (no subprocess per poll)
      - a probe is retried once the backoff expires
    """
    results = []
    real_find = globals()["_find_cli"]
    attempts = {"count": 0}

    def fake_find():
        attempts["count"] += 1
        return None

    saved = dict(_cli_cache)
    globals()["_find_cli"] = fake_find
    try:
        _cli_cache["path"] = None
        _cli_cache["next_check"] = 0.0

        results.append(_check("returns None on failure", resolve_cli() is None))
        after_first = attempts["count"]
        results.append(_check("actually attempted a probe", after_first >= 1, f"{after_first}"))

        resolve_cli()
        results.append(_check("no repeat probe inside the backoff window",
                              attempts["count"] == after_first, f"{attempts['count']} attempts"))

        _cli_cache["next_check"] = 0.0        # pretend the backoff expired
        resolve_cli()
        results.append(_check("retries after the backoff (the actual regression)",
                              attempts["count"] > after_first, f"{attempts['count']} attempts"))
    finally:
        globals()["_find_cli"] = real_find
        _cli_cache.update(saved)
    return results


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


# Directory names deliberately contain cmd metacharacters and spaces: this is exactly
# where the quoting bug put a trailing space in the path
TRICKY_DIRS = [
    ("plain", r"F:\proj\demo"),
    ("spaces", r"F:\proj\open code test"),
    ("ampersand", r"F:\proj\A&B"),
    ("caret", r"F:\proj\A^B"),
    ("percent", r"F:\proj\50%off"),
    ("parens and semicolon", r"F:\proj\A(B);C"),
]


def _check_quoting_end_to_end() -> list:
    """
    Run the launcher through a real cmd and record the arguments the target received.

    This is the regression test for that bug: asserting on the assembled string is not
    enough, Windows' own quoting rules have to actually run.
    """
    import tempfile

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        captured = tmp_path / "argv.txt"
        probe = tmp_path / "probe.py"
        probe.write_text(
            "import sys\n"
            f"open(r'{captured}', 'w', encoding='utf-8').write(repr(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        # Use a batch file as the "CLI", mimicking the %* forwarding of opencode2.cmd, so the
        # batch layer is covered too
        shim = tmp_path / "shim.cmd"
        shim.write_text(f'@ECHO off\n"{sys.executable}" "{probe}" %*\n', encoding="mbcs")

        for name, directory in TRICKY_DIRS:
            launcher = tmp_path / f"launch_{abs(hash(name))}.cmd"
            _write_launcher(launcher, build_launcher_script(str(shim), "ses_probe", directory))
            if captured.exists():
                captured.unlink()
            try:
                subprocess.run(
                    ["cmd.exe", "/c", str(launcher)],
                    capture_output=True, timeout=60, creationflags=_NO_WINDOW,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                results.append(_check(f"directory arrives intact: {name}", False, str(exc)))
                continue
            got = captured.read_text(encoding="utf-8") if captured.exists() else "(no output)"
            expected = repr(["--session", "ses_probe", directory])
            results.append(_check(f"directory arrives intact: {name}", got == expected,
                                  got if got != expected else ""))
    return results


def selftest() -> int:
    """Self test: pure-function assertions plus a real probe. No side effects: it never
    starts/stops the service and never opens a terminal."""
    import tempfile

    results = []
    print("== 1. classify() state decision (constructed data) ==")
    fake = {"url": "http://127.0.0.1:1", "password": "x", "version": "2.0.32", "pid": 1}
    results.append(_check("no service.json -> not running", classify(None, HTTP_OK) == NO_SERVICE))
    results.append(_check("present + 200 -> running", classify(fake, HTTP_OK) == RUNNING))
    results.append(_check("present + 401 -> auth failed",
                              classify(fake, HTTP_UNAUTHORIZED) == UNAUTHORIZED))
    results.append(_check("present + timeout -> not responding",
                              classify(fake, HTTP_UNREACHABLE) == UNREACHABLE))

    print("== 2. read_service() tolerance ==")
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "nope.json"
        results.append(_check("missing file -> None", read_service(missing) is None))

        broken = Path(tmp) / "broken.json"
        broken.write_text("{ this is not json", encoding="utf-8")
        results.append(_check("corrupt content -> None", read_service(broken) is None))

        incomplete = Path(tmp) / "incomplete.json"
        incomplete.write_text('{"url": "http://x"}', encoding="utf-8")
        results.append(_check("no password -> None", read_service(incomplete) is None))

        good = Path(tmp) / "good.json"
        good.write_text(json.dumps(fake), encoding="utf-8")
        results.append(_check("complete fields -> read back", read_service(good) == fake))

    print("== 3. probe_server() live probe ==")
    status = probe_server()
    print(f"  state={status.label} version={status.version} pid={status.pid} url={status.url}")
    print(f"  tooltip: {status.tooltip()}")
    results.append(_check("returns a valid state", status.state in STATE_KEYS))

    print("== 4. list_sessions() live session list ==")
    sessions = list_sessions()
    if sessions is None:
        print("  (service unavailable, returns None - expected when the state does not match)")
    else:
        print(f"  {len(sessions)} entries (limit {SESSION_LIMIT}):")
        for item in sessions[:5]:
            print(f"    {item.menu_label()}")
        results.append(_check("within the limit", len(sessions) <= SESSION_LIMIT))
        results.append(_check("running sessions sort first", all(
            not sessions[i].running or sessions[i - 1].running for i in range(1, len(sessions))
        )))

    print("== 5. CLI resolution ==")
    cli = resolve_cli()
    print(f"  CLI = {cli}")
    results.append(_check("found a V2 CLI", cli is not None))

    print("== 6. Command assembly (pure, not executed) ==")
    results.append(_check("batch quoting: % becomes %%", bat_quote("50%off") == '"50%%off"'))
    results.append(_check("batch quoting: other metacharacters untouched",
                              bat_quote("A&B^C") == '"A&B^C"'))
    script = build_launcher_script("C:/x/opencode2.CMD", "ses_abc123", r"F:\proj")
    results.append(_check("launcher carries --session and the id", "--session ses_abc123" in script))
    results.append(_check("launcher cds to the session directory", 'cd /d "F:\\proj"' in script))
    results.append(_check("launcher does not rely on PowerShell", "powershell" not in script.lower()))
    # TERM is absent when the tray starts from Explorer or the Run key, which degrades the TUI
    results.append(_check(f"launcher sets TERM={LAUNCH_TERM}",
                          f"set TERM={LAUNCH_TERM}" in script))
    results.append(_check("term=None leaves TERM unset",
                          "set TERM" not in build_launcher_script(
                              "C:/x/opencode2.CMD", "ses_a", r"F:\p", term=None)))

    print("== 7. Terminal choice (pure, not executed) ==")
    has_wt = shutil.which("wt") is not None
    if has_wt:
        launcher = Path("C:/tmp/launch.cmd")
        argv, flags = build_terminal_command(launcher)
        results.append(_check("prefers Windows Terminal",
                              Path(argv[0]).name.lower() == "wt.exe", argv[0]))
        results.append(_check("wt runs cmd /k on the launcher",
                              argv[1:] == ["cmd.exe", "/k", str(launcher)], str(argv)))
        results.append(_check("wt needs no CREATE_NEW_CONSOLE", flags == 0))
    else:
        results.append(_check("without wt it falls back to conhost",
                              argv[:4] == ["cmd.exe", "/c", "start", ""], str(argv)))

    print("== 8. Arguments arrive intact (end to end, real cmd) ==")
    results += _check_quoting_end_to_end()

    print("== 9. SessionItem menu rendering ==")
    long_title = "a deliberately long session title " * 20
    item = SessionItem(id="ses_abcdef123456", title=long_title, directory="", running=False, updated=0)
    label = item.menu_label()
    results.append(_check("long title is truncated", len(label) < len(long_title)))
    amp = SessionItem(id="ses_x", title="A & B", directory="", running=True, updated=0)
    results.append(_check("& escaped as &&", "&&" in amp.menu_label()))
    results.append(_check("running uses a filled bullet", "●" in amp.menu_label()))

    print("== 10. A failed CLI probe is not cached forever (regression) ==")
    results += _check_cli_retry()

    passed = sum(results)
    total = len(results)
    print(f"\nSelf test: {passed}/{total} passed")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenCode background service probe layer")
    parser.add_argument("--selftest", action="store_true", help="run the self test and exit")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
