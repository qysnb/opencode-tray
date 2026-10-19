# -*- coding: utf-8 -*-
"""
OpenCode 后台服务探测层(数据层,不依赖任何 GUI)

只读本机 OpenCode 后台服务的注册文件与 HTTP API,提供:
  - 服务健康状态判定(运行中 / 无响应 / 认证失败 / 未运行)
  - 全局会话列表(含运行状态)
  - 服务生命周期(重启 / 停止 / 启动)
  - 在新终端接管某个会话

用法:
    python opencode_service.py --selftest   # 自检:真实探测服务 + 纯函数断言,不开窗口

依赖: 无(仅标准库)。GUI 部分见 opencode_tray.pyw。

设计要点(详见 docs/design.md):
  - 探测**不走** `opencode api`:该命令在无健康服务时会自动启动服务,会让调用方产生副作用。
  - 会话列表用 HTTP `/api/session`(全局)而非 `opencode session list`(仅当前项目)。
  - 鉴权只有 HTTP Basic 可用,用户名固定 "opencode"。
  - 子进程一律经 cmd.exe 启动:本机 PowerShell 执行策略会拦截 opencode.ps1。
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

# 以 pythonw 运行时没有 stdout/stderr,必须容错
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 服务状态
RUNNING = "running"            # service.json 存在且 /api/info 应答正常
UNREACHABLE = "unreachable"    # service.json 存在但连接超时/被拒/其它 HTTP 错误
UNAUTHORIZED = "unauthorized"  # service.json 存在但密码已失效(401)
NO_SERVICE = "no_service"      # service.json 缺失或损坏

# HTTP 探测结果
HTTP_OK = "ok"
HTTP_UNAUTHORIZED = "unauthorized"
HTTP_UNREACHABLE = "unreachable"

# 状态 -> 文案键(实际文案见 i18n.py,随语言切换)
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
    """状态的中文/英文名称。"""
    return i18n._(STATE_KEYS.get(state, state))


def state_detail(state: str) -> str:
    """状态的补充说明(为什么不可用)。"""
    key = DETAIL_KEYS.get(state)
    return i18n._(key) if key else ""


DEFAULT_TIMEOUT = 5.0   # 单次 HTTP 请求超时(秒)
SESSION_LIMIT = 15      # 托盘菜单里最多列出的会话数
TITLE_MAX = 40          # 会话标题截断长度
CLI_TIMEOUT = 90        # 调用 CLI 的超时(服务启停可能较慢)

# 子进程不弹控制台窗口(仅 Windows 有效)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def state_file_path() -> Path:
    """后台服务的注册文件:记录 url / pid / password。"""
    return Path(os.path.expanduser("~")) / ".local" / "state" / "opencode" / "service.json"


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class ServerStatus:
    """一次服务探测的结果。"""
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
    """一条会话(用于托盘菜单)。"""
    id: str
    title: str
    directory: str
    running: bool
    updated: int  # epoch 毫秒

    def menu_label(self) -> str:
        """菜单里显示的文本。"""
        title = self.title.strip() or i18n._("session.untitled")
        if len(title) > TITLE_MAX:
            title = title[:TITLE_MAX] + "…"
        mark = "●" if self.running else "○"
        text = f"{mark} {title} · {self.id[-6:]}"
        if not self.running and self.updated:
            text += f" · {_ago(self.updated)}"
        # Windows 菜单把 & 当助记符前缀,必须转义
        return text.replace("&", "&&")


def _ago(epoch_ms: int) -> str:
    """把 epoch 毫秒转成"3 分钟前" / "3m ago" 这类相对时间。"""
    delta = time.time() - epoch_ms / 1000.0
    if delta < 60:
        return i18n._("time.just_now")
    if delta < 3600:
        return i18n._("time.minutes_ago", count=int(delta // 60))
    if delta < 86400:
        return i18n._("time.hours_ago", count=int(delta // 3600))
    return i18n._("time.days_ago", count=int(delta // 86400))


# ---------------------------------------------------------------------------
# 服务注册文件
# ---------------------------------------------------------------------------

def read_service(path=None) -> dict | None:
    """
    读取 service.json。文件缺失、无法解析或字段不全时返回 None
    (按"未运行"处理,不抛异常)。
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
# HTTP 探测
# ---------------------------------------------------------------------------

def _auth_header(password: str) -> str:
    """OpenCode 的本地 API 只接受 HTTP Basic,用户名固定 "opencode"。"""
    token = base64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
    return "Basic " + token


def fetch_json(url: str, password: str, timeout: float = DEFAULT_TIMEOUT):
    """
    发一个带鉴权的 GET。返回 (outcome, payload|None),
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
        # URLError(连接被拒/超时/DNS)、OSError、JSON 解析失败(ValueError)都归为"不可达"
        return HTTP_UNREACHABLE, None


def classify(service, outcome: str) -> str:
    """
    纯函数:由"注册文件内容"与"HTTP 探测结果"判定服务状态。

    service 为 None 表示 service.json 缺失或损坏,此时无论探测结果如何都算未运行。
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
    探测一次服务状态。每轮都重新读 service.json —— 服务重启后端口与密码都会变。
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
# 会话
# ---------------------------------------------------------------------------

def list_sessions(limit: int = SESSION_LIMIT, timeout: float = DEFAULT_TIMEOUT, path=None):
    """
    列出全局会话,running 的排前面,其余按最近更新倒序,最多 limit 条。
    服务不可用或请求失败时返回 None(调用方据此显示"会话列表不可用")。
    """
    service = read_service(path)
    if service is None:
        return None
    base = service["url"].rstrip("/")
    password = service["password"]

    outcome, payload = fetch_json(base + "/api/session", password, timeout)
    if outcome != HTTP_OK or not isinstance(payload, dict):
        return None

    # 活跃会话集合(用于打 ● 标记)
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
# CLI 定位
# ---------------------------------------------------------------------------
# 关键:探测失败**绝不能永久缓存**。
# `opencode2 --version` 要启动一个很大的原生二进制,冷启动(例如开机自启、系统繁忙时)
# 可能要好几秒甚至超时。曾经把「没找到」永久缓存下来,于是开机那次探测一旦失败,
# 托盘的「重启/停止/启动服务」就会一直灰着,只能重启托盘才能恢复。
# 现在改为:找到就长期用;没找到则退避一段时间后自动重试。

CLI_PROBE_TIMEOUT = 20.0    # 单次 `--version` 探测上限(秒)
CLI_RETRY_SECONDS = 60.0    # 没找到时的重试间隔(秒)

_cli_cache = {"path": None, "next_check": 0.0}


def _probe_cli_version(exe: str) -> str | None:
    """跑一次 `--version`,成功返回版本号,否则 None。"""
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
    依次产出的 CLI 候选路径。

    先走 PATH;再显式兜底到 npm 全局安装目录 —— 开机自启等环境下 PATH 可能不完整,
    不能只依赖 shutil.which。
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
    """在候选里找主版本号为 2 的那个(本机 V1 占用了 opencode 命令名)。"""
    for exe in _cli_candidates():
        version = _probe_cli_version(exe)
        if version and version.split(".")[0] == "2":
            return exe
    return None


def resolve_cli(refresh: bool = False) -> str | None:
    """
    定位可用的 OpenCode V2 CLI,返回可执行文件完整路径或 None。

    成功的解析会被长期复用;失败的结果只保留 CLI_RETRY_SECONDS 秒,之后自动重试,
    避免一次瞬时失败(冷启动超时 / PATH 不完整)让菜单项永久变灰。
    """
    now = time.monotonic()
    cached = _cli_cache["path"]
    if not refresh:
        if cached and os.path.exists(cached):
            return cached
        if now < _cli_cache["next_check"]:
            return None     # 刚试过没找到,等退避窗口过去再试

    found = _find_cli()
    _cli_cache["path"] = found
    _cli_cache["next_check"] = now + CLI_RETRY_SECONDS
    return found


def _run_service(subcommand: str, timeout: float = CLI_TIMEOUT):
    """执行 `opencode service <subcommand>`。返回 (是否成功, 说明)。"""
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
# 在新终端接管会话
# ---------------------------------------------------------------------------

# 托盘由资源管理器 / 开机自启拉起时,环境里没有 TERM;实测没有 TERM 时
# opencode 的 TUI 会走降级渲染,界面与用户日常所见明显不同。
# 这里显式设成与本机日常终端相同的值,保证渲染一致。
LAUNCH_TERM = "xterm-256color"


def bat_quote(text: str) -> str:
    """
    纯函数:按批处理语法给一个参数加引号。

    批处理里 % 会触发变量展开,要写成 %% 才是字面量;引号内的 & ^ ( ) ; 都无需处理。
    """
    return '"' + text.replace("%", "%%") + '"'


def build_launcher_script(cli: str, session_id: str, directory: str,
                          term: str | None = LAUNCH_TERM) -> str:
    """
    纯函数:生成临时启动器(.cmd)的内容。

    为什么不把命令直接塞进 cmd 的参数里(踩过的坑,已用探针实测):
      - subprocess 按 MSVCRT 规则给参数加引号,cmd.exe 有自己的一套引号规则。
        手工加引号会被 subprocess 再转义一次,引号变成**字面字符** —— 实测目录末尾
        会多出一个空格,opencode 报 `ENOENT: no such file or directory, chdir '...test '`,
        这正是用户遇到的 bug。
      - 只传原始字符串虽然能修好引号,但目录名含 & 或 ^ 时会被 cmd 当成分隔符/转义符
        吃掉(实测 `A&B` -> `A`,`A^B` -> `AB`)。
      - 把目录写进批处理**文件内容**,cmd 就只按自己的规则解析一次。
        实测 7 种刁钻目录名(空格 / & / ^ / % / ( ) / ;) 全部原样到达。
    """
    lines = ["@echo off"]
    if term:
        lines.append(f"set TERM={term}")
    lines.append(f"cd /d {bat_quote(directory)}")
    lines.append(f"{bat_quote(cli)} --session {session_id} {bat_quote(directory)}")
    return "\n".join(lines) + "\n"


def build_terminal_command(launcher: Path):
    """
    纯函数:返回 (argv, creationflags) —— 用什么终端打开这个启动器。

    优先 Windows Terminal:本机默认终端是**经典 conhost**(委派键全为 0),
    而用户日常用的是 Windows Terminal。conhost 的字体与 VT 支持较差,
    实测同一个启动器在 conhost 里 TUI 渲染与平时明显不一致。
    找不到 wt.exe 时退回经典 conhost(start 新开一个控制台窗口)。
    """
    wt = shutil.which("wt")
    if wt:
        # cmd.exe /k:会话退出后保留窗口,便于看到报错
        return [wt, "cmd.exe", "/k", str(launcher)], 0
    return (
        ["cmd.exe", "/c", "start", "", "cmd.exe", "/k", str(launcher)],
        getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )


def _launcher_path(session_id: str) -> Path:
    """
    临时启动器路径。放在临时目录里,保证这个路径本身不含元字符。

    按会话 ID 命名,所以同一个会话只留一个文件,不会无限增长。
    """
    import tempfile
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "_-")
    return Path(tempfile.gettempdir()) / f"opencode_tray_session_{safe}.cmd"


def _write_launcher(path: Path, script: str) -> None:
    """写启动器脚本。cmd.exe 按 ANSI 代码页读取批处理,中文路径必须按 mbcs 写。"""
    try:
        path.write_text(script, encoding="mbcs")
    except (LookupError, UnicodeEncodeError):
        path.write_text(script, encoding="utf-8")


def open_session(session: SessionItem):
    """在新终端窗口里接管该会话(工作目录设为会话所属目录)。返回 (是否成功, 说明)。"""
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
# 自检
# ---------------------------------------------------------------------------

def _check_cli_retry() -> list:
    """
    回归测试:CLI 探测**失败**后必须会自动重试。

    曾经把失败结果永久缓存,于是开机自启时那次冷启动超时一旦发生,
    托盘的「重启/停止/启动服务」就永久变灰。这里断言:
      - 退避窗口内不重复探测(避免每轮轮询都 spawn 进程)
      - 退避窗口过后会自动重试
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

        results.append(_check("失败时返回 None", resolve_cli() is None))
        after_first = attempts["count"]
        results.append(_check("确实尝试过探测", after_first >= 1, f"{after_first} 次"))

        resolve_cli()
        results.append(_check("退避窗口内不重复探测(省开销)",
                              attempts["count"] == after_first, f"{attempts['count']} 次"))

        _cli_cache["next_check"] = 0.0        # 模拟退避窗口已过
        resolve_cli()
        results.append(_check("退避窗口过后会自动重试(关键回归点)",
                              attempts["count"] > after_first, f"{attempts['count']} 次"))
    finally:
        globals()["_find_cli"] = real_find
        _cli_cache.update(saved)
    return results


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


# 目录名故意含 cmd 元字符与空格 —— 曾经在这里踩过引号 bug(目录末尾多一个空格)
TRICKY_DIRS = [
    ("普通", r"F:\proj\demo"),
    ("含空格", r"F:\proj\open code test"),
    ("含与号", r"F:\proj\A&B"),
    ("含脱字符", r"F:\proj\A^B"),
    ("含百分号", r"F:\proj\50%off"),
    ("含括号分号", r"F:\proj\A(B);C"),
]


def _check_quoting_end_to_end() -> list:
    """
    真实起 cmd 跑一遍启动器,用探针记下目标程序收到的参数,断言目录原样到达。

    这是针对「引号变成字面字符 / 元字符被吃掉」那次 bug 的回归测试:
    只断言字符串拼装是不够的,必须让 Windows 的引号规则真的过一遍。
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
        # 用一个批处理当"CLI",模仿 opencode2.cmd 的 %* 转发,把批处理层也覆盖进来
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
                results.append(_check(f"目录原样到达: {name}", False, str(exc)))
                continue
            got = captured.read_text(encoding="utf-8") if captured.exists() else "(无输出)"
            expected = repr(["--session", "ses_probe", directory])
            results.append(_check(f"目录原样到达: {name}", got == expected, got if got != expected else ""))
    return results


def selftest() -> int:
    """自检:纯函数断言 + 真实探测。不做任何有副作用的调用(不启停服务、不开终端)。"""
    import tempfile

    results = []
    print("== 1. classify() 状态判定(构造数据) ==")
    fake = {"url": "http://127.0.0.1:1", "password": "x", "version": "2.0.32", "pid": 1}
    results.append(_check("无 service.json -> 未运行", classify(None, HTTP_OK) == NO_SERVICE))
    results.append(_check("存在 + 200 -> 运行中", classify(fake, HTTP_OK) == RUNNING))
    results.append(_check("存在 + 401 -> 认证失败", classify(fake, HTTP_UNAUTHORIZED) == UNAUTHORIZED))
    results.append(_check("存在 + 超时 -> 无响应", classify(fake, HTTP_UNREACHABLE) == UNREACHABLE))

    print("== 2. read_service() 容错 ==")
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "nope.json"
        results.append(_check("文件缺失 -> None", read_service(missing) is None))

        broken = Path(tmp) / "broken.json"
        broken.write_text("{ this is not json", encoding="utf-8")
        results.append(_check("内容损坏 -> None", read_service(broken) is None))

        incomplete = Path(tmp) / "incomplete.json"
        incomplete.write_text('{"url": "http://x"}', encoding="utf-8")
        results.append(_check("缺 password -> None", read_service(incomplete) is None))

        good = Path(tmp) / "good.json"
        good.write_text(json.dumps(fake), encoding="utf-8")
        results.append(_check("字段完整 -> 正常读出", read_service(good) == fake))

    print("== 3. probe_server() 真实探测 ==")
    status = probe_server()
    print(f"  状态={status.label} version={status.version} pid={status.pid} url={status.url}")
    print(f"  tooltip: {status.tooltip()}")
    results.append(_check("返回合法状态", status.state in STATE_KEYS))

    print("== 4. list_sessions() 真实会话列表 ==")
    sessions = list_sessions()
    if sessions is None:
        print("  (服务不可用,返回 None —— 状态不匹配时属正常)")
    else:
        print(f"  共 {len(sessions)} 条(上限 {SESSION_LIMIT}):")
        for item in sessions[:5]:
            print(f"    {item.menu_label()}")
        results.append(_check("数量未超上限", len(sessions) <= SESSION_LIMIT))
        results.append(_check("running 排在前面", all(
            not sessions[i].running or sessions[i - 1].running for i in range(1, len(sessions))
        )))

    print("== 5. CLI 定位 ==")
    cli = resolve_cli()
    print(f"  CLI = {cli}")
    results.append(_check("找到 V2 CLI", cli is not None))

    print("== 6. 命令拼装(纯函数,不执行) ==")
    results.append(_check("批处理引号: % 转义为 %%", bat_quote("50%off") == '"50%%off"'))
    results.append(_check("批处理引号: 其余元字符不动", bat_quote("A&B^C") == '"A&B^C"'))
    script = build_launcher_script("C:/x/opencode2.CMD", "ses_abc123", r"F:\proj")
    results.append(_check("启动器含 --session 与会话 ID", "--session ses_abc123" in script))
    results.append(_check("启动器先 cd /d 到会话目录", 'cd /d "F:\\proj"' in script))
    results.append(_check("启动器不依赖 PowerShell", "powershell" not in script.lower()))
    # 托盘由资源管理器/开机自启启动时环境里没有 TERM,缺了会导致 TUI 降级渲染
    results.append(_check(f"启动器设置 TERM={LAUNCH_TERM}",
                          f"set TERM={LAUNCH_TERM}" in script))
    results.append(_check("term=None 时不设 TERM",
                          "set TERM" not in build_launcher_script(
                              "C:/x/opencode2.CMD", "ses_a", r"F:\p", term=None)))

    print("== 7. 终端选择(纯函数,不执行) ==")
    has_wt = shutil.which("wt") is not None
    if has_wt:
        launcher = Path("C:/tmp/launch.cmd")
        argv, flags = build_terminal_command(launcher)
        results.append(_check("优先 Windows Terminal", Path(argv[0]).name.lower() == "wt.exe", argv[0]))
        results.append(_check("wt 传 cmd /k 打开启动器",
                              argv[1:] == ["cmd.exe", "/k", str(launcher)], str(argv)))
        results.append(_check("wt 不需要 CREATE_NEW_CONSOLE", flags == 0))
    else:
        results.append(_check("无 wt 时退回 conhost",
                              argv[:4] == ["cmd.exe", "/c", "start", ""], str(argv)))

    print("== 8. 参数原样到达(端到端,起真实 cmd) ==")
    results += _check_quoting_end_to_end()

    print("== 9. SessionItem 菜单渲染 ==")
    long_title = "中文标题" * 20
    item = SessionItem(id="ses_abcdef123456", title=long_title, directory="", running=False, updated=0)
    label = item.menu_label()
    results.append(_check("长标题被截断", len(label) < len(long_title)))
    amp = SessionItem(id="ses_x", title="A & B", directory="", running=True, updated=0)
    results.append(_check("& 已转义为 &&", "&&" in amp.menu_label()))
    results.append(_check("running 用实心点", "●" in amp.menu_label()))

    print("== 10. CLI 探测失败不会被永久缓存(回归) ==")
    results += _check_cli_retry()

    passed = sum(results)
    total = len(results)
    print(f"\n自检结果:{passed}/{total} 通过")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenCode 后台服务探测层")
    parser.add_argument("--selftest", action="store_true", help="运行自检后退出")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
