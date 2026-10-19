# -*- coding: utf-8 -*-
"""
Tiny message catalogue for the tray indicator.

Two languages ship today: English (the default) and Simplified Chinese. Both
blocks below must define exactly the same keys — the self test asserts that, so
a missing or misspelled key fails loudly instead of silently falling back.

The preference lives in ``settings.json`` next to this file, so the project stays
portable: copy the folder anywhere and it keeps its language.

用法:
    python i18n.py --selftest

依赖: 无(仅标准库)
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
# 语言自称(不翻译),顺序即菜单顺序
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
        "single.hint": "请查看任务栏右下角的通知区域(可能收在「显示隐藏的图标」里)。右键图标可以重启 / 停止 / 启动服务,或打开会话。",
        "single.check_unavailable": "单实例检查不可用(错误码 {code}),仍继续启动",

        # --- demo ---
        "demo.summary": "托盘已运行 {seconds:g} 秒并正常退出(最终状态 {state},{polls} 次轮询,会话 {sessions})",
    },
}


def language_names() -> dict:
    """语言代码 -> 自称(始终不翻译)。"""
    return dict(LANGUAGE_NAMES)


def settings_path() -> Path:
    """语言偏好存放位置:与本模块同目录的 settings.json(便携、可随文件夹整体搬走)。"""
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
    """当前语言代码。未显式设置时读 settings.json,再退回默认(English)。"""
    global _current
    if _current is None:
        _current = _read_saved_language() or DEFAULT_LANGUAGE
    return _current


def set_language(code: str, persist: bool = True) -> str:
    """
    切换语言并返回实际生效的代码。

    写入失败不抛异常:目录只读时语言在当前进程内仍然生效,只是下次启动不记住。
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
    取一条文案。找不到的键原样返回(便于发现漏配),插值用 str.format。

    format 失败(例如占位符名字写错)也原样返回,绝不让缺一条文案把托盘拖垮。
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
    """某语言定义的全部键(自检用)。"""
    return set(_STRINGS.get(language, {}))


def selftest() -> int:
    results = []

    def check(label, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
        results.append(ok)

    print("== 1. 语言表键集合一致(漏配会静默退回英文,必须显式检查) ==")
    en_keys = all_keys("en")
    zh_keys = all_keys("zh-Hans")
    check("英文表非空", len(en_keys) > 30, f"{len(en_keys)} 条")
    check("两种语言键集合完全相同", en_keys == zh_keys,
          f"仅英文 {sorted(en_keys - zh_keys)} / 仅中文 {sorted(zh_keys - en_keys)}")
    check("声明了两种语言", set(LANGUAGE_NAMES) == {"en", "zh-Hans"}, str(list(LANGUAGE_NAMES)))

    print("== 2. 非空文案 ==")
    empty = [k for k in en_keys if not _STRINGS["en"][k].strip() or not _STRINGS["zh-Hans"][k].strip()]
    check("没有空文案", not empty, str(empty))

    print("== 3. 占位符一致(否则一种语言会丢参数) ==")
    import re
    pattern = re.compile(r"\{(\w+)")
    mismatched = []
    for key in sorted(en_keys):
        en_fields = set(pattern.findall(_STRINGS["en"][key]))
        zh_fields = set(pattern.findall(_STRINGS["zh-Hans"][key]))
        if en_fields != zh_fields:
            mismatched.append(f"{key}: en={sorted(en_fields)} zh={sorted(zh_fields)}")
    check("两种语言占位符名称一致", not mismatched, "; ".join(mismatched[:3]))

    print("== 4. 切换语言真的改变输出 ==")
    original = current()
    try:
        set_language("en", persist=False)
        english = _("state.running")
        set_language("zh-Hans", persist=False)
        chinese = _("state.running")
        check("en 与 zh-Hans 输出不同", english != chinese, f"{english!r} vs {chinese!r}")
        check("英文界面取自英文表", english == "Running", english)
        check("中文界面取自中文表", chinese == "运行中", chinese)

        print("== 5. 插值 ==")
        set_language("en", persist=False)
        check("带参数插值", _("tooltip.active", count=3) == " · active 3", _("tooltip.active", count=3))
        set_language("zh-Hans", persist=False)
        check("中文带参数插值", _("tooltip.active", count=3) == " · 活跃 3", _("tooltip.active", count=3))

        print("== 6. 容错 ==")
        set_language("en", persist=False)
        check("未知键原样返回", _("no.such.key") == "no.such.key")
        check("缺参数不抛异常", isinstance(_("tooltip.active"), str))
        check("非法语言代码退回默认", set_language("xx-YY", persist=False) == DEFAULT_LANGUAGE)
    finally:
        set_language(original, persist=False)

    passed, total = sum(results), len(results)
    print(f"\n自检结果:{passed}/{total} 通过")
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
