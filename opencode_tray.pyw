# -*- coding: utf-8 -*-
"""
OpenCode 后台服务托盘指示器

在 Windows 任务栏通知区域显示一个图标,指示后台服务是否在工作,并提供
重启 / 停止 / 启动服务、查看并用新终端接管会话等操作。

用法:
    pythonw opencode_tray.pyw                    # 启动托盘(无控制台窗口)
    python  opencode_tray.pyw --demo 5           # 显示 5 秒后自动退出(自动化验证用)
    python  opencode_tray.pyw --install-startup     # 安装开机自启(注册表)
    python  opencode_tray.pyw --uninstall-startup   # 移除开机自启
    python  opencode_tray.pyw --install-shortcut     # 创建桌面快捷方式
    python  opencode_tray.pyw --uninstall-shortcut   # 删除桌面快捷方式

图标含义:
    绿(实心) 服务运行中   灰 服务未运行   红 无响应或认证失败

依赖: pystray, Pillow(数据层 opencode_service.py 与快捷方式层 win_shortcut.py 仅用标准库)
设计: docs/design.md

注意: 探测不走 `opencode api`(它在无服务时会自动启动服务,产生副作用),
      因此本程序是纯观察者,只有你主动点菜单才会改变服务状态。
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

import opencode_service as svc
import win_shortcut

# 以 pythonw 启动时没有标准流:记下这一点供 _inform 判断,
# 再把它们接到空设备,避免任何意外的 print/argparse 写 None 而崩溃。
HAS_CONSOLE = sys.stdout is not None
if not HAS_CONSOLE:
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")

APP_NAME = "OpenCode 服务"
POLL_SECONDS = 3.0     # 状态轮询间隔
MESSAGE_TTL = 8.0      # 菜单里"上次操作结果"的保留时长
BUSY_TIMEOUT = 120.0   # 看门狗:操作占用超过此时长就放行菜单,避免永久变灰
ICON_SIZE = 64

ICON_COLORS = {
    svc.RUNNING: (46, 184, 92, 255),        # 绿:运行中
    svc.NO_SERVICE: (150, 150, 150, 255),   # 灰:未运行
    svc.UNREACHABLE: (214, 69, 65, 255),    # 红:无响应
    svc.UNAUTHORIZED: (214, 69, 65, 255),   # 红:认证失败
}


# ---------------------------------------------------------------------------
# 图标绘制
# ---------------------------------------------------------------------------

def draw_icon(color):
    """画一个实心圆 + 白色终端提示符字形。16×16 缩放下仍可辨识。"""
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((3, 3, ICON_SIZE - 4, ICON_SIZE - 4), fill=color)
    white = (255, 255, 255, 235)
    # 两道横杠,示意终端提示符
    draw.rectangle((20, 22, 44, 28), fill=white)
    draw.rectangle((20, 36, 36, 42), fill=white)
    return image


ICON_FILE = Path(__file__).resolve().with_name("opencode_tray.ico")


def ensure_icon_file() -> Path:
    """
    生成托盘图标的 .ico(供桌面快捷方式使用),已存在且非空则直接复用。
    用 Pillow 现画,所以仓库里不需要提交二进制图标;该文件由 .gitignore 忽略。
    """
    if ICON_FILE.exists() and ICON_FILE.stat().st_size > 0:
        return ICON_FILE
    image = draw_icon(ICON_COLORS[svc.RUNNING])
    image.save(ICON_FILE, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
    return ICON_FILE


# ---------------------------------------------------------------------------
# 单实例保护
# ---------------------------------------------------------------------------
# 用命名互斥体而不是锁文件:进程无论正常退出还是崩溃,句柄都会由内核释放,
# 不会留下需要人工清理的过期锁。
# "Local\" 前缀表示只在当前登录会话内生效(一台机器多用户各自可跑一个托盘)。

MUTEX_NAME = r"Local\OpenCodeServerTray.SingleInstance"
ERROR_ALREADY_EXISTS = 183

# 模块级持有句柄,进程存活期间不释放
_mutex_handle = None


def acquire_single_instance():
    """
    尝试取得单实例锁。返回 (是否拿到了锁, 说明)。

    拿不到说明已经有一个托盘在跑,调用方应当提示用户并退出。
    """
    global _mutex_handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_error = ctypes.get_last_error()

    if not handle:
        # 拿不到互斥体本身:不阻塞用户,放行并说明
        return True, f"单实例检查不可用(错误码 {last_error}),继续启动"
    _mutex_handle = handle

    if last_error == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        _mutex_handle = None
        return False, "OpenCode 托盘已经在运行了"
    return True, ""


# ---------------------------------------------------------------------------
# 开机自启
# ---------------------------------------------------------------------------
# 为什么用注册表而不是「启动」文件夹:
# 本机「启动」文件夹对 .cmd / .bat / .vbs 一律返回 PermissionError(Errno 13),
# 属防持久化加固 —— 实测 .lnk 与 .txt 可写,.cmd/.bat/.vbs 被拒。
# 改用 HKCU\...\Run:纯标准库 winreg、无需 COM、不受 PowerShell 执行策略影响,
# 且同样会出现在「任务管理器 → 启动」列表中,便于用户查看与禁用。

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "OpenCodeTray"
DESKTOP_SHORTCUT_NAME = "OpenCode 托盘指示器.lnk"

# 菜单里的生命周期动作:kind -> 可调用对象(返回 (是否成功, 说明))
SERVICE_ACTIONS = {
    "restart": svc.restart_server,
    "stop": svc.stop_server,
    "start": svc.start_server,
}


def _preferred_pythonw() -> str:
    """
    自启与快捷方式该用哪个 pythonw。

    优先「当前正在运行本脚本的解释器」旁边的 pythonw —— 这样**安装时用的解释器**
    与**以后启动时用的解释器**必然一致。

    不用 `shutil.which("pythonw")`:PATH 上可能有多个 Python(本机实测有 Conda 3.13.5、
    Python 3.14、以及 WindowsApps 的 PythonManager 垫片),`which` 的结果会随 PATH 变化,
    可能选到一个没装 pystray 的解释器 —— 实测就选错了。
    """
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.exists():
        return str(exe)
    sibling = exe.with_name("pythonw.exe")
    if sibling.exists():
        return str(sibling)
    return shutil.which("pythonw") or str(exe)


def _autostart_command() -> str:
    """登录时要执行的命令行:pythonw + 本脚本的绝对路径。"""
    script = Path(__file__).resolve()
    return f'"{_preferred_pythonw()}" "{script}"'


def startup_status():
    """返回当前登记的自启命令;未安装返回 None。"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_VALUE_NAME)[0]
    except OSError:
        return None


def install_startup():
    """写入注册表自启。返回 (是否成功, 说明)。"""
    import winreg
    command = _autostart_command()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, command)
    except OSError as exc:
        return False, f"写入开机自启失败:{exc}"
    return True, (
        f"已装上开机自启:\nHKCU\\{RUN_KEY}\\{RUN_VALUE_NAME}\n\n"
        f"{command}\n\n"
        "可在「任务管理器 → 启动」里看到并临时禁用。"
    )


def uninstall_startup():
    """移除注册表自启。返回 (是否成功, 说明)。"""
    import winreg
    if startup_status() is None:
        return True, "开机自启本来就没有安装"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE_NAME)
    except OSError as exc:
        return False, f"移除开机自启失败:{exc}"
    return True, "已移除开机自启"


# ---------------------------------------------------------------------------
# 桌面快捷方式
# ---------------------------------------------------------------------------

def shortcut_path() -> Path:
    """桌面快捷方式的落盘路径(跟随桌面重定向,如 OneDrive)。"""
    return Path(win_shortcut.desktop_dir()) / DESKTOP_SHORTCUT_NAME


def install_shortcut():
    """在桌面创建快捷方式(带自绘图标)。返回 (是否成功, 说明)。"""
    script = Path(__file__).resolve()
    try:
        icon = ensure_icon_file()
        target = shortcut_path()
        win_shortcut.create_shortcut(
            target,
            target=_preferred_pythonw(),
            arguments=f'"{script}"',
            working_dir=script.parent,
            description="OpenCode 后台服务状态托盘指示器",
            icon_path=icon,
        )
    except (OSError, ValueError) as exc:
        return False, f"创建桌面快捷方式失败:{exc}"
    return True, f"已在桌面创建快捷方式:\n{target}\n\n图标:{icon}"


def uninstall_shortcut():
    """删除桌面快捷方式。返回 (是否成功, 说明)。"""
    target = shortcut_path()
    if not target.exists():
        return True, "桌面快捷方式本来就没有"
    try:
        target.unlink()
    except OSError as exc:
        return False, f"删除桌面快捷方式失败:{exc}"
    return True, f"已删除桌面快捷方式:{target}"


def _inform(text: str, title: str = APP_NAME):
    """有控制台就打印;以 pythonw 运行时没有控制台,改用消息框。"""
    if HAS_CONSOLE:
        print(text)
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 托盘应用
# ---------------------------------------------------------------------------

class TrayApp:
    """托盘界面。状态由后台线程轮询,菜单每次弹出时动态生成。"""

    def __init__(self):
        self.status = svc.ServerStatus(state=svc.NO_SERVICE, detail="正在探测…")
        self.sessions = None            # list[SessionItem] | None
        # CLI 状态:None=尚未确定,True/False=已确定。
        # 不在 __init__ 里同步解析 —— 冷启动时 `opencode --version` 要好几秒,
        # 会让托盘迟迟不出现;改由轮询线程在后台解析并回填。
        self.has_cli = None
        self.busy = False               # 服务生命周期命令执行中
        self._busy_since = 0.0
        self.message = ""
        self.message_until = 0.0
        self.polls = 0

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._drawn_state = None
        # 菜单相关状态的指纹:变了才重建菜单(见 _menu_signature 的说明)
        self._menu_signature_cache = None
        # 菜单是否正在被 Windows 显示;显示期间绝不能重建(见 _guard_menu_display)
        self._menu_open = False
        self._menu_rebuild_pending = False

        self.icon = pystray.Icon(
            "opencode-service",
            draw_icon(ICON_COLORS[svc.NO_SERVICE]),
            APP_NAME,
            Menu(self._menu),
        )
        self._guard_menu_display()

    # --- 生命周期 ---

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
            try:
                self.refresh()
            except Exception as exc:  # 轮询线程绝不能因单次异常死掉
                self._say(f"探测出错:{exc}")
            self._stop.wait(POLL_SECONDS)

    def refresh(self):
        """探测一次服务与会话,并同步图标/提示文本。"""
        status = svc.probe_server()
        sessions = svc.list_sessions() if status.is_running else None
        with self._lock:
            self.status = status
            self.sessions = sessions
            self.polls += 1
        # CLI 在后台解析并回填;一旦确认为存在就不再重复探测(resolve_cli 内部有缓存与退避)
        if self.has_cli is not True:
            found = svc.resolve_cli() is not None
            with self._lock:
                self.has_cli = found
        self._sync_icon()

    def _sync_icon(self):
        with self._lock:
            state = self.status.state
            title = self._tooltip()
        # 仅在真正变化时更新,避免图标闪烁
        if state != self._drawn_state:
            self._drawn_state = state
            self.icon.icon = draw_icon(ICON_COLORS.get(state, ICON_COLORS[svc.NO_SERVICE]))
        if self.icon.title != title:
            self.icon.title = title
        self._refresh_menu_if_changed()

    def _guard_menu_display(self):
        """
        记录「菜单正在被 Windows 显示」,使显示期间不重建菜单。

        Windows 上菜单是在**显示之前**就构建好的固定 HMENU,右击时 pystray 直接用它。
        若在菜单显示期间调用 `update_menu()`,它会 `DestroyMenu` 掉 Windows 正在跟踪的
        那个句柄 —— 表现就是鼠标悬停高亮**卡住不动**,或者**干脆不出现**
        (用户实际报告的正是这两种现象)。

        所以显示期只记下"待重建",等菜单关掉之后立刻补上。

        只包自己这个 icon 实例的消息处理表,不做全局 monkeypatch;
        如果拿不到 pystray 的内部结构,就退回原来的定时重建行为(功能不受影响)。
        """
        try:
            from pystray._util import win32 as _win32
            key = _win32.WM_NOTIFY
            original = self.icon._message_handlers[key]
        except (ImportError, AttributeError, KeyError, TypeError):
            return False
        if getattr(original, "_opencode_tray_guard", False):
            return True     # 已装过,幂等

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
                    # 此时 TrackPopupMenuEx 已返回,菜单确实关了,重建是安全的
                    try:
                        self.icon.update_menu()
                    except Exception:
                        pass

        guarded._opencode_tray_guard = True
        self.icon._message_handlers[key] = guarded
        return True

    def _refresh_menu_if_changed(self):
        """
        菜单相关内容变化时重建菜单。

        注意:`update_menu()` 会调用菜单描述符,而描述符会取 `self._lock`,
        所以**不能**在持锁状态下调用它(Lock 不可重入,会死锁)。
        """
        signature = self._menu_signature()
        with self._lock:
            if signature == self._menu_signature_cache:
                return
            self._menu_signature_cache = signature
            if self._menu_open:
                # 正在显示:推迟到菜单关闭后,避免毁掉被跟踪的句柄
                self._menu_rebuild_pending = True
                return
        self.icon.update_menu()

    def _menu_signature(self):
        """
        菜单相关状态的指纹。

        为什么需要它:pystray 在 Windows 上**不会在显示菜单时才生成菜单** ——
        `_on_notify` 直接用缓存的 HMENU,而 `update_menu()` 只在
        「菜单被赋值」「托盘启动」「点击菜单项之后」这三个时机被调用。
        也就是说:没有任何后台刷新会让菜单跟上状态变化。

        后果(实测确认):启动那一刻的状态会被**冻结**在菜单里 ——
        菜单项该亮不亮、会话子菜单永远停在启动时的快照,直到用户点一次菜单项。
        所以每次轮询后,只要菜单相关内容变了就显式重建。
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
            )

    # --- 菜单内容 ---

    def _menu(self):
        items = [MenuItem(lambda item: self._header_text(), None, enabled=False)]
        if self.message and time.time() < self.message_until:
            items.append(MenuItem(lambda item: self.message, None, enabled=False))
        items += [
            Menu.SEPARATOR,
            MenuItem("会话", Menu(self._session_menu)),
            Menu.SEPARATOR,
            MenuItem("重启服务", self._make_action("restart"), enabled=self._lifecycle_enabled),
            MenuItem("停止服务", self._make_action("stop"), enabled=self._lifecycle_enabled),
            MenuItem("启动服务", self._make_action("start"), enabled=self._lifecycle_enabled),
            Menu.SEPARATOR,
            MenuItem("退出", self._on_quit),
        ]
        return items

    def _header_text(self):
        with self._lock:
            text = f"{APP_NAME} · {self.status.label}"
            if self.status.state == svc.RUNNING and self.status.version:
                text += f" v{self.status.version}"
        return text

    def _busy_now(self):
        """
        当前是否真的在忙碌。

        带看门狗:超过 BUSY_TIMEOUT 视为已卡死,不再算忙碌 ——
        否则任何一次意外都会让菜单永久变灰(与 CLI 缓存那个 bug 同一类风险)。
        """
        return self.busy and (time.monotonic() - self._busy_since) < BUSY_TIMEOUT

    def _lifecycle_enabled(self, item):
        """
        只在**确定**找不到 CLI 时才变灰(has_cli is False)。

        has_cli 为 None 表示还没探测完,这时保持可点 —— 点了会得到一条明确的错误说明,
        比灰着一个不解释为什么的菜单项好。
        """
        return self.has_cli is not False and not self._busy_now()

    def _session_menu(self):
        with self._lock:
            sessions = self.sessions
        if sessions is None:
            yield MenuItem("(会话列表不可用)", None, enabled=False)
            return
        if not sessions:
            yield MenuItem("(暂无会话)", None, enabled=False)
            return
        for session in sessions:
            yield MenuItem(session.menu_label(), self._make_session_action(session))
        yield Menu.SEPARATOR
        yield MenuItem("刷新", self._on_refresh)

    def _tooltip(self):
        text = self.status.tooltip()
        if self.status.is_running and self.sessions is not None:
            active = sum(1 for s in self.sessions if s.running)
            text += f" · 活跃 {active}"
        if self.has_cli is False:
            text += " · 未找到 CLI(服务操作不可用)"
        if self._busy_now():
            text += " · 正在执行操作…"
        return text

    # --- 动作 ---

    def _make_action(self, kind):
        # 注意:这里必须传**可调用对象**。曾经直接传 kind(字符串)给 _run_async,
        # 而 _worker 会执行 fn(),于是点任何生命周期菜单项都报
        # `操作失败:'str' object is not callable`。
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
        """把动作丢到后台线程,避免阻塞托盘消息循环。"""
        with self._lock:
            if self._busy_now():
                return  # 防连点(看门狗超时后放行,见 _busy_now)
            self.busy = True
            self._busy_since = time.monotonic()
        threading.Thread(target=self._worker, args=(fn,), daemon=True).start()

    def _worker(self, fn):
        try:
            ok, message = fn()
            self._say(message)
        except Exception as exc:
            self._say(f"操作失败:{exc}")
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
            self.icon.notify(text, APP_NAME)
        except Exception:
            pass  # 气泡通知不可用时静默降级,菜单里仍能看到结果


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def _item_text(app, item):
    text = item.text
    return text(app) if callable(text) else text


def _rendered_states(app):
    """
    读**已渲染菜单**里每一项的禁用标志。

    必须查渲染层(GetMenuState),不能只查 `item.enabled` ——
    描述符会重新求值,永远显示"正确",而 Windows 实际画的是缓存的 HMENU。
    我一开始就是被描述符层的假象误导过。
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
    """自检:菜单接线、菜单刷新、灰亮规则、看门狗。不开窗口、不触碰服务。"""
    results = []

    def check(label, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
        results.append(ok)

    print("== 1. 生命周期动作是可调用对象 ==")
    for kind in ("restart", "stop", "start"):
        check(f"{kind} -> callable(回归:'str' object is not callable)",
              callable(SERVICE_ACTIONS.get(kind)), repr(SERVICE_ACTIONS.get(kind)))

    app = TrayApp()
    forwarded = []
    app._run_async = lambda fn: forwarded.append(fn)
    for kind in ("restart", "stop", "start"):
        app._make_action(kind)(None, None)
    check("菜单动作转发的是可调用对象",
          len(forwarded) == 3 and all(callable(f) for f in forwarded))

    print("== 2. 菜单结构 ==")
    texts = [_item_text(app, item) for item in app.icon.menu.items]
    for want in ("会话", "重启服务", "停止服务", "启动服务", "退出"):
        check(f"菜单含 {want}", want in texts)
    check("已移除「打开日志」", not any("日志" in t for t in texts))

    print("== 3. 菜单随状态刷新(pystray 在 Windows 上不会自动刷新) ==")
    check("已装上「菜单显示中」守卫", app._guard_menu_display() is not False)
    rebuilds = []
    real_update = app.icon.update_menu
    app.icon.update_menu = lambda: (rebuilds.append(1), real_update())[1]
    app._sync_icon()
    baseline = len(rebuilds)
    app._sync_icon()
    check("状态未变时不重建(省开销)", len(rebuilds) == baseline, f"{len(rebuilds)}")
    app.has_cli = False
    app._sync_icon()
    check("状态变化时重建菜单", len(rebuilds) > baseline, f"{len(rebuilds)}")

    print("== 3b. 菜单显示期间绝不重建(否则悬停高亮会错乱) ==")
    app._menu_open = True
    app.has_cli = True
    before = len(rebuilds)
    app._sync_icon()
    check("显示期间不重建", len(rebuilds) == before, f"{len(rebuilds)}")
    check("显示期间记为待重建", app._menu_rebuild_pending is True)

    # 模拟菜单关闭:守卫的 finally 会补上挂起的重建
    app._menu_open = False
    pending = app._menu_rebuild_pending
    app._menu_rebuild_pending = False
    if pending:
        app.icon.update_menu()
    check("菜单关闭后补上重建", len(rebuilds) > before, f"{len(rebuilds)}")

    print("== 3c. 不在持锁状态下调用 update_menu(会死锁) ==")
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
    check("_sync_icon 不会死锁", done.wait(10.0), "等待 10 秒")
    worker.join(timeout=2)

    print("== 4. 渲染层的灰/亮确实随状态变化 ==")
    app.has_cli = True
    app.busy = False
    app.icon.update_menu()
    on = (_rendered_states(app) or {})
    app.has_cli = False
    app.icon.update_menu()
    off = (_rendered_states(app) or {})
    check("可点状态下「重启服务」是亮的", on.get("重启服务") is False, str(on.get("重启服务")))
    check("确无 CLI 时「重启服务」变灰", off.get("重启服务") is True, str(off.get("重启服务")))
    check("灰/亮确实随状态改变", on.get("重启服务") != off.get("重启服务"))

    print("== 5. 灰亮规则与看门狗 ==")
    app.has_cli = True
    app.busy = False
    check("正常时可点", app._lifecycle_enabled(None) is True)
    app.has_cli = False
    check("确无 CLI 时变灰", app._lifecycle_enabled(None) is False)
    app.has_cli = None
    check("尚未确定时保持可点", app._lifecycle_enabled(None) is True)
    app.has_cli = True
    app.busy = True
    app._busy_since = time.monotonic()
    check("执行中防连点", app._lifecycle_enabled(None) is False)
    check("执行中 tooltip 有说明", "正在执行操作" in app._tooltip())
    app._busy_since = time.monotonic() - BUSY_TIMEOUT - 1
    check("卡死超时后看门狗放行", app._lifecycle_enabled(None) is True)
    check("卡死后 tooltip 不再说执行中", "正在执行操作" not in app._tooltip())

    app.busy = False
    app.has_cli = False
    check("确无 CLI 时 tooltip 说明原因", "未找到 CLI" in app._tooltip())

    print("== 6. 解释器固定(自启/快捷方式不能靠 PATH 猜) ==")
    preferred = _preferred_pythonw()
    check("解析出一个 pythonw 路径", bool(preferred), preferred)
    check("优先与当前解释器同目录",
          Path(preferred).parent == Path(sys.executable).parent,
          f"{Path(preferred).parent} vs {Path(sys.executable).parent}")
    check("自启命令用的是这个 pythonw",
          _autostart_command().startswith(f'"{preferred}"'), _autostart_command())
    check("自启命令指向本脚本",
          str(Path(__file__).resolve()) in _autostart_command())

    passed, total = sum(results), len(results)
    print(f"\n自检结果:{passed}/{total} 通过")
    return 0 if passed == total else 1


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="OpenCode 后台服务托盘指示器")
    parser.add_argument("--demo", type=float, metavar="SECONDS",
                        help="显示 N 秒后自动退出(用于自动化验证)")
    parser.add_argument("--selftest", action="store_true",
                        help="运行自检后退出(不开窗口,不触碰服务)")
    parser.add_argument("--install-startup", action="store_true", help="安装开机自启")
    parser.add_argument("--uninstall-startup", action="store_true", help="移除开机自启")
    parser.add_argument("--install-shortcut", action="store_true", help="创建桌面快捷方式")
    parser.add_argument("--uninstall-shortcut", action="store_true", help="删除桌面快捷方式")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    # 一次性动作不需要单实例锁(可能只是想改配置,且要与正在运行的托盘并存)
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

    # 单实例保护:双击两次不应出现两个托盘图标
    acquired, reason = acquire_single_instance()
    if not acquired:
        _inform(
            f"{reason}。\n\n"
            "请查看任务栏右下角的通知区域(可能收在「显示隐藏的图标」里),"
            "右键图标可以重启 / 停止 / 启动服务或查看会话。"
        )
        return 1

    app = TrayApp()
    app.run(args.demo)

    if args.demo:
        _inform(
            f"托盘已运行 {args.demo:g} 秒并正常退出"
            f"(最终状态 {app.status.label},轮询 {app.polls} 次,"
            f"会话 {len(app.sessions) if app.sessions is not None else '不可用'})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
