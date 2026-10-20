# -*- coding: utf-8 -*-
"""
Tiny message catalogue for the tray indicator.

Two languages ship today: English (the default) and Simplified Chinese. Both
blocks below must define exactly the same keys — the self test asserts that, so
a missing or misspelled key fails loudly instead of silently falling back.

The preference lives in ``settings.json`` next to this file, so the project stays
portable: copy the folder anywhere and it keeps its language.

Usage:
    python i18n.py --selftest

Dependencies: none (standard library only).
"""

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

DEFAULT_LANGUAGE = "en"
# Language names in their own language (never translated); order = menu order
LANGUAGE_NAMES = {
    "en": "English",
    "zh-Hans": "简体中文",
}

_STRINGS = {
    "en": {
        # --- application ---
        "app.name": "OpenCode Service",

        # --- service state ---
        "state.running": "Running",
        "state.unreachable": "Not responding",
        "state.unauthorized": "Auth failed",
        "state.no_service": "Not running",
        "state.probing": "Probing…",
        "state.detail.unreachable": "not responding (the process may have crashed)",
        "state.detail.unauthorized": "auth failed (service.json is stale)",
        "state.detail.no_service": "not running (not installed, or disabled)",

        # --- tooltip ---
        "tooltip.base": "{app}: {label}",
        "tooltip.version": " v{version}",
        "tooltip.pid": " · PID {pid}",
        "tooltip.detail": " — {detail}",
        "tooltip.active": " · active {count}",
        "tooltip.cli_missing": " · CLI not found (service actions unavailable)",
        "tooltip.busy": " · working…",

        # --- menu ---
        "menu.sessions": "Sessions",
        "menu.refresh": "Refresh",
        "menu.restart": "Restart service",
        "menu.stop": "Stop service",
        "menu.start": "Start service",
        "menu.language": "Language",
        "menu.quit": "Quit",
        "sessions.unavailable": "(session list unavailable)",
        "sessions.empty": "(no sessions)",

        # --- sessions ---
        "session.untitled": "(untitled)",
        "session.opened": "Opened session {id} in a new terminal window",
        "session.launcher_write_failed": "Could not write the session launcher: {error}",
        "session.terminal_failed": "Could not open a terminal: {error}",

        # --- relative time ---
        "time.just_now": "just now",
        "time.minutes_ago": "{count}m ago",
        "time.hours_ago": "{count}h ago",
        "time.days_ago": "{count}d ago",

        # --- CLI ---
        "cli.not_found": "Cannot find the OpenCode V2 CLI (opencode2 / opencode)",

        # --- service lifecycle ---
        "service.done": "service {command} finished",
        "service.timeout": "service {command} timed out after {seconds}s",
        "service.failed_exec": "service {command} failed to run: {error}",
        "service.failed_code": "service {command} failed (exit code {code}): {tail}",

        # --- actions ---
        "action.failed": "Action failed: {error}",
        "poll.failed": "Probe failed: {error}",

        # --- autostart ---
        "startup.installed": "Autostart enabled:\nHKCU\\{key}\\{name}\n\n{command}\n\nYou can see and temporarily disable it in Task Manager → Startup.",
        "startup.removed": "Autostart removed",
        "startup.not_installed": "Autostart was not installed",
        "startup.install_failed": "Could not write the autostart entry: {error}",
        "startup.remove_failed": "Could not remove the autostart entry: {error}",

        # --- desktop shortcut ---
        "shortcut.installed": "Desktop shortcut created:\n{path}\n\nIcon: {icon}",
        "shortcut.removed": "Desktop shortcut removed: {path}",
        "shortcut.not_installed": "There was no desktop shortcut",
        "shortcut.install_failed": "Could not create the desktop shortcut: {error}",
        "shortcut.remove_failed": "Could not remove the desktop shortcut: {error}",

        # --- single instance ---
        "single.already_running": "OpenCode Tray is already running.",
        "single.reassert_sent": "It was asked to re-add its tray icon.",
        "single.hint": "Look in the notification area at the bottom right of the taskbar (it may be tucked under \"Show hidden icons\"). Right-click the icon to restart, stop or start the service, or to open a session.",
        "single.check_unavailable": "Single-instance check unavailable (error {code}); starting anyway",

        # --- demo ---
        "demo.summary": "The tray ran for {seconds:g}s and exited cleanly (final state {state}, {polls} polls, sessions {sessions})",
    },

    "zh-Hans": {
        # --- application ---
        "app.name": "OpenCode 服务",

        # --- service state ---
        "state.running": "运行中",
        "state.unreachable": "无响应",
        "state.unauthorized": "认证失败",
        "state.no_service": "未运行",
        "state.probing": "正在探测…",
        "state.detail.unreachable": "无响应(可能已崩溃)",
        "state.detail.unauthorized": "认证失败(service.json 已过期)",
        "state.detail.no_service": "未运行(未安装 / 已禁用)",

        # --- tooltip ---
        "tooltip.base": "{app}:{label}",
        "tooltip.version": " v{version}",
        "tooltip.pid": " · PID {pid}",
        "tooltip.detail": " — {detail}",
        "tooltip.active": " · 活跃 {count}",
        "tooltip.cli_missing": " · 未找到 CLI(服务操作不可用)",
        "tooltip.busy": " · 正在执行操作…",

        # --- menu ---
        "menu.sessions": "会话",
        "menu.refresh": "刷新",
        "menu.restart": "重启服务",
        "menu.stop": "停止服务",
        "menu.start": "启动服务",
        "menu.language": "语言",
        "menu.quit": "退出",
        "sessions.unavailable": "(会话列表不可用)",
        "sessions.empty": "(暂无会话)",

        # --- sessions ---
        "session.untitled": "(无标题)",
        "session.opened": "已在新的终端窗口打开会话 {id}",
        "session.launcher_write_failed": "写入会话启动器失败:{error}",
        "session.terminal_failed": "打开终端失败:{error}",

        # --- relative time ---
        "time.just_now": "刚刚",
        "time.minutes_ago": "{count} 分钟前",
        "time.hours_ago": "{count} 小时前",
        "time.days_ago": "{count} 天前",

        # --- CLI ---
        "cli.not_found": "找不到 OpenCode V2 CLI(opencode2 / opencode)",

        # --- service lifecycle ---
        "service.done": "service {command} 完成",
        "service.timeout": "service {command} 超时({seconds} 秒)",
        "service.failed_exec": "service {command} 执行失败:{error}",
        "service.failed_code": "service {command} 失败(返回码 {code}):{tail}",

        # --- actions ---
        "action.failed": "操作失败:{error}",
        "poll.failed": "探测出错:{error}",

        # --- autostart ---
        "startup.installed": "已装上开机自启:\nHKCU\\{key}\\{name}\n\n{command}\n\n可在「任务管理器 → 启动」里看到并临时禁用。",
        "startup.removed": "已移除开机自启",
        "startup.not_installed": "开机自启本来就没有安装",
        "startup.install_failed": "写入开机自启失败:{error}",
        "startup.remove_failed": "移除开机自启失败:{error}",

        # --- desktop shortcut ---
        "shortcut.installed": "已在桌面创建快捷方式:\n{path}\n\n图标:{icon}",
        "shortcut.removed": "已删除桌面快捷方式:{path}",
        "shortcut.not_installed": "桌面快捷方式本来就没有",
        "shortcut.install_failed": "创建桌面快捷方式失败:{error}",
        "shortcut.remove_failed": "删除桌面快捷方式失败:{error}",

        # --- single instance ---
        "single.already_running": "OpenCode 托盘已经在运行了。",
        "single.reassert_sent": "已通知它重新挂上托盘图标。",
        "single.hint": "请查看任务栏右下角的通知区域(可能收在「显示隐藏的图标」里)。右键图标可以重启 / 停止 / 启动服务,或打开会话。",
        "single.check_unavailable": "单实例检查不可用(错误码 {code}),仍继续启动",

        # --- demo ---
        "demo.summary": "托盘已运行 {seconds:g} 秒并正常退出(最终状态 {state},{polls} 次轮询,会话 {sessions})",
    },
}


def language_names() -> dict:
    """Language code -> name in that language (never translated)."""
    return dict(LANGUAGE_NAMES)


def settings_path() -> Path:
    """Where the preference lives: settings.json next to this module (portable)."""
    return Path(__file__).resolve().with_name("settings.json")


def _read_saved_language():
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    language = data.get("language") if isinstance(data, dict) else None
    return language if language in _STRINGS else None


_current = None


def current() -> str:
    """Current language code, read from settings.json, falling back to the default."""
    global _current
    if _current is None:
        _current = _read_saved_language() or DEFAULT_LANGUAGE
    return _current


def set_language(code: str, persist: bool = True) -> str:
    """
    Switch language and return the code that took effect.

    A failed write does not raise: on a read-only directory the language still
    applies to this process, it just will not be remembered next time.
    """
    global _current
    if code not in _STRINGS:
        code = DEFAULT_LANGUAGE
    _current = code
    if persist:
        try:
            settings_path().write_text(
                json.dumps({"language": code}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
    return code


def _(key: str, **kwargs) -> str:
    """
    Look up a message. An unknown key is returned as-is so gaps stay visible;
    interpolation uses str.format.

    A formatting failure (for example a misspelled placeholder) also returns the
    raw text - one bad message must never take the tray down.
    """
    table = _STRINGS.get(current()) or _STRINGS[DEFAULT_LANGUAGE]
    text = table.get(key)
    if text is None:
        text = _STRINGS[DEFAULT_LANGUAGE].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def all_keys(language: str = DEFAULT_LANGUAGE):
    """Every key defined for a language (used by the self test)."""
    return set(_STRINGS.get(language, {}))


def selftest() -> int:
    results = []

    def check(label, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
        results.append(ok)

    print("== 1. Both catalogues define the same keys ==")
    en_keys = all_keys("en")
    zh_keys = all_keys("zh-Hans")
    check("English catalogue is populated", len(en_keys) > 30, f"{len(en_keys)} keys")
    check("key sets are identical", en_keys == zh_keys,
          f"en only {sorted(en_keys - zh_keys)} / zh only {sorted(zh_keys - en_keys)}")
    check("two languages declared", set(LANGUAGE_NAMES) == {"en", "zh-Hans"},
          str(list(LANGUAGE_NAMES)))

    print("== 2. No empty messages ==")
    empty = [k for k in en_keys if not _STRINGS["en"][k].strip() or not _STRINGS["zh-Hans"][k].strip()]
    check("no empty message", not empty, str(empty))

    print("== 3. Placeholders match (otherwise a language loses arguments) ==")
    import re
    pattern = re.compile(r"\{(\w+)")
    mismatched = []
    for key in sorted(en_keys):
        en_fields = set(pattern.findall(_STRINGS["en"][key]))
        zh_fields = set(pattern.findall(_STRINGS["zh-Hans"][key]))
        if en_fields != zh_fields:
            mismatched.append(f"{key}: en={sorted(en_fields)} zh={sorted(zh_fields)}")
    check("placeholder names are identical", not mismatched, "; ".join(mismatched[:3]))

    print("== 4. Switching language really changes the output ==")
    original = current()
    try:
        set_language("en", persist=False)
        english = _("state.running")
        set_language("zh-Hans", persist=False)
        chinese = _("state.running")
        check("en and zh-Hans differ", english != chinese, f"{english!r} vs {chinese!r}")
        check("English output comes from the English table", english == "Running", english)
        check("Chinese output comes from the Chinese table", chinese == "运行中", chinese)

        print("== 5. Interpolation ==")
        set_language("en", persist=False)
        check("interpolates", _("tooltip.active", count=3) == " · active 3",
              _("tooltip.active", count=3))
        set_language("zh-Hans", persist=False)
        check("interpolates in Chinese", _("tooltip.active", count=3) == " · 活跃 3",
              _("tooltip.active", count=3))

        print("== 6. Graceful degradation ==")
        set_language("en", persist=False)
        check("unknown key returned as-is", _("no.such.key") == "no.such.key")
        check("missing argument does not raise", isinstance(_("tooltip.active"), str))
        check("invalid language code falls back to the default",
              set_language("xx-YY", persist=False) == DEFAULT_LANGUAGE)
    finally:
        set_language(original, persist=False)

    passed, total = sum(results), len(results)
    print(f"\nSelf test: {passed}/{total} passed")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Message catalogue for the OpenCode tray indicator")
    parser.add_argument("--selftest", action="store_true", help="Validate the catalogue")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
