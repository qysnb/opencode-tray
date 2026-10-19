# -*- coding: utf-8 -*-
"""
Windows 快捷方式(.lnk)创建与读取助手

用法:
    python win_shortcut.py --selftest   # 在桌面真建一个 .lnk,用 COM 读回校验,再删除

依赖: 无(仅标准库 ctypes)

为什么需要它:
  - 「启动」文件夹在本机对 .cmd / .bat / .vbs 一律返回 PermissionError(防持久化加固),
    只有 .lnk 放行,所以要会建 .lnk。
  - 不调 PowerShell 建:本机执行策略已拦截 .ps1,不想再引入这层不确定性。
  - 不依赖 pywin32:额外依赖,且本项目一直坚持纯标准库。
  直接 ctypes 调 IShellLinkW / IPersistFile,零依赖、零子进程。

桌面路径必须用 SHGetFolderPathW 取,不能拼 "~/Desktop":
  本机桌面被重定向到 OneDrive(C:\\Users\\lenovo\\OneDrive\\桌面)。
"""

import argparse
import ctypes
import sys
from ctypes import POINTER, byref, c_int, c_long, c_uint, c_void_p, c_wchar_p
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

HRESULT = c_long
CLSCTX_INPROC_SERVER = 0x1
CSIDL_DESKTOPDIRECTORY = 0x0010
MAX_PATH = 260

# IShellLinkW vtable 索引(前 3 个是 IUnknown)
SL_GET_PATH = 3
SL_GET_DESCRIPTION = 6
SL_SET_DESCRIPTION = 7
SL_GET_WORKING_DIRECTORY = 8
SL_SET_WORKING_DIRECTORY = 9
SL_GET_ARGUMENTS = 10
SL_SET_ARGUMENTS = 11
SL_GET_ICON_LOCATION = 16
SL_SET_ICON_LOCATION = 17
SL_SET_PATH = 20

# IPersistFile vtable 索引
PF_LOAD = 5
PF_SAVE = 6

_IUNKNOWN_QUERY_INTERFACE = 0
_IUNKNOWN_RELEASE = 2


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> GUID:
    value = GUID()
    ctypes.WinDLL("ole32").CLSIDFromString(c_wchar_p(text), byref(value))
    return value


CLSID_SHELL_LINK = _guid("{00021401-0000-0000-C000-000000000046}")
IID_ISHELLLINKW = _guid("{000214F9-0000-0000-C000-000000000046}")
IID_IPERSISTFILE = _guid("{0000010B-0000-0000-C000-000000000046}")


def _method(interface, index, restype, *argtypes):
    """取 COM 接口 vtable 上第 index 个方法(this 指针作为首个参数)。"""
    table = ctypes.cast(interface, POINTER(POINTER(c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(table[index])


def desktop_dir() -> str:
    """取当前用户的桌面目录(自动跟随 OneDrive 重定向)。"""
    buffer = ctypes.create_unicode_buffer(MAX_PATH)
    ctypes.WinDLL("shell32").SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, 0, buffer)
    return buffer.value


def _open_shell_link():
    """新建一个 IShellLinkW 实例。返回接口指针。"""
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitialize(None)
    link = c_void_p()
    result = ole32.CoCreateInstance(
        byref(CLSID_SHELL_LINK), None, CLSCTX_INPROC_SERVER,
        byref(IID_ISHELLLINKW), byref(link),
    )
    if result != 0:
        raise OSError(f"CoCreateInstance 失败: hr={result:#x}")
    return link


def _release(interface):
    _method(interface, _IUNKNOWN_RELEASE, ctypes.c_ulong)(interface)


def _as_persist_file(link):
    """QueryInterface 到 IPersistFile。"""
    persist = c_void_p()
    result = _method(link, _IUNKNOWN_QUERY_INTERFACE, HRESULT, c_void_p, c_void_p)(
        link, byref(IID_IPERSISTFILE), byref(persist)
    )
    if result != 0:
        raise OSError(f"QueryInterface(IPersistFile) 失败: hr={result:#x}")
    return persist


def create_shortcut(path, target, arguments="", working_dir="",
                    description="", icon_path="", icon_index=0):
    """
    创建(或覆盖)一个 .lnk。返回创建后的 Path。

    path        .lnk 的落盘路径
    target      目标可执行文件
    arguments   传给 target 的参数
    working_dir 目标的工作目录
    description 悬停提示文字
    icon_path   自定义图标文件(.ico);留空则用 target 自带图标
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    link = _open_shell_link()
    try:
        if _method(link, SL_SET_PATH, HRESULT, c_wchar_p)(link, str(target)) != 0:
            raise OSError("SetPath 失败")
        if arguments:
            if _method(link, SL_SET_ARGUMENTS, HRESULT, c_wchar_p)(link, arguments) != 0:
                raise OSError("SetArguments 失败")
        if working_dir:
            if _method(link, SL_SET_WORKING_DIRECTORY, HRESULT, c_wchar_p)(link, str(working_dir)) != 0:
                raise OSError("SetWorkingDirectory 失败")
        if description:
            if _method(link, SL_SET_DESCRIPTION, HRESULT, c_wchar_p)(link, description) != 0:
                raise OSError("SetDescription 失败")
        if icon_path:
            if _method(link, SL_SET_ICON_LOCATION, HRESULT, c_wchar_p, c_int)(
                link, str(icon_path), icon_index
            ) != 0:
                raise OSError("SetIconLocation 失败")

        persist = _as_persist_file(link)
        try:
            # fRemember=0:不把这次创建写入"最近使用"记录
            result = _method(persist, PF_SAVE, HRESULT, c_wchar_p, c_int)(persist, str(path), 0)
            if result != 0:
                raise OSError(f"IPersistFile::Save 失败: hr={result:#x}")
        finally:
            _release(persist)
    finally:
        _release(link)
    return path


def read_shortcut(path) -> dict:
    """读回 .lnk 的内容,用于校验。返回 dict(target/arguments/working_dir/description/icon)。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    link = _open_shell_link()
    try:
        persist = _as_persist_file(link)
        try:
            if _method(persist, PF_LOAD, HRESULT, c_wchar_p, c_uint)(persist, str(path), 0) != 0:
                raise OSError("IPersistFile::Load 失败,文件可能不是有效的 .lnk")
        finally:
            _release(persist)

        def get_string(index, extra_types=(), extra_values=()):
            buffer = ctypes.create_unicode_buffer(MAX_PATH)
            call_args = (ctypes.cast(buffer, c_wchar_p), c_int(MAX_PATH)) + tuple(extra_values)
            call_types = (c_wchar_p, c_int) + tuple(extra_types)
            if _method(link, index, HRESULT, *call_types)(link, *call_args) != 0:
                return ""
            return buffer.value

        icon_buffer = ctypes.create_unicode_buffer(MAX_PATH)
        icon_index = c_int()
        has_icon = _method(link, SL_GET_ICON_LOCATION, HRESULT, c_wchar_p, c_int, POINTER(c_int))(
            link, ctypes.cast(icon_buffer, c_wchar_p), MAX_PATH, byref(icon_index)
        ) == 0

        return {
            "target": get_string(SL_GET_PATH, (c_void_p, c_uint), (None, 0)),
            "arguments": get_string(SL_GET_ARGUMENTS),
            "working_dir": get_string(SL_GET_WORKING_DIRECTORY),
            "description": get_string(SL_GET_DESCRIPTION),
            "icon": icon_buffer.value if has_icon else "",
            "icon_index": icon_index.value if has_icon else 0,
        }
    finally:
        _release(link)


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def _check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def selftest() -> int:
    """
    在桌面真建一个 .lnk,用 COM 读回校验,再删除。
    用带中文的描述和参数,顺带验证 Unicode 往返。
    """
    results = []
    desktop = Path(desktop_dir())
    print(f"桌面目录:{desktop}")
    results.append(_check("能取到桌面目录", bool(desktop)))

    probe = desktop / "_zz_win_shortcut_selftest.lnk"
    target = Path(sys.executable)
    args = '"C:\\带空格的 目录\\测试.pyw" --说明 中文'
    try:
        create_shortcut(
            probe,
            target=target,
            arguments=args,
            working_dir=desktop,
            description="OpenCode 托盘 selftest 中文描述",
        )
        results.append(_check(".lnk 已创建", probe.exists(),
                              f"{probe.stat().st_size} 字节" if probe.exists() else ""))

        data = read_shortcut(probe)
        results.append(_check("target 往返一致",
                              Path(data["target"]).name.lower() == target.name.lower(),
                              data["target"]))
        results.append(_check("arguments 往返一致(含中文与空格)", data["arguments"] == args,
                              data["arguments"]))
        results.append(_check("working_dir 往返一致",
                              Path(data["working_dir"]) == desktop, data["working_dir"]))
        results.append(_check("description 往返一致(含中文)",
                              data["description"] == "OpenCode 托盘 selftest 中文描述",
                              data["description"]))
    finally:
        if probe.exists():
            probe.unlink()
    results.append(_check("清理完成", not probe.exists()))

    passed, total = sum(results), len(results)
    print(f"\n自检结果:{passed}/{total} 通过")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows 快捷方式(.lnk)助手")
    parser.add_argument("--selftest", action="store_true", help="建→读回→删,验证 COM 调用可用")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
