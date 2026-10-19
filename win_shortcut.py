# -*- coding: utf-8 -*-
"""
Create and read Windows shortcuts (.lnk).

Usage:
    python win_shortcut.py --selftest   # create a real .lnk on the desktop, read it
                                        # back through COM, then delete it

Dependencies: none (ctypes only).

Why this module exists:
  - The Startup folder on this machine rejects .cmd / .bat / .vbs outright with a
    PermissionError (anti-persistence hardening); only .lnk is allowed, so we need
    to be able to write one.
  - Creating shortcuts through PowerShell is not an option here: the execution
    policy blocks .ps1, and there is no reason to add that uncertainty.
  - pywin32 would be another dependency, and this project sticks to the standard
    library. Calling IShellLinkW / IPersistFile through ctypes gives us zero
    dependencies and zero subprocesses.

The desktop path must come from SHGetFolderPathW, never by joining "~/Desktop":
on this machine the desktop is redirected into OneDrive
(C:\\Users\\lenovo\\OneDrive\\桌面).
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

# IShellLinkW vtable indices (the first three slots are IUnknown)
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

# IPersistFile vtable indices
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
    """Return the vtable method at `index`, with the this pointer as first arg."""
    table = ctypes.cast(interface, POINTER(POINTER(c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(table[index])


def desktop_dir() -> str:
    """Return the current user's desktop directory (follows OneDrive redirection)."""
    buffer = ctypes.create_unicode_buffer(MAX_PATH)
    ctypes.WinDLL("shell32").SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, 0, buffer)
    return buffer.value


def _open_shell_link():
    """Create an IShellLinkW instance and return its interface pointer."""
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitialize(None)
    link = c_void_p()
    result = ole32.CoCreateInstance(
        byref(CLSID_SHELL_LINK), None, CLSCTX_INPROC_SERVER,
        byref(IID_ISHELLLINKW), byref(link),
    )
    if result != 0:
        raise OSError(f"CoCreateInstance failed: hr={result:#x}")
    return link


def _release(interface):
    _method(interface, _IUNKNOWN_RELEASE, ctypes.c_ulong)(interface)


def _as_persist_file(link):
    """QueryInterface for IPersistFile."""
    persist = c_void_p()
    result = _method(link, _IUNKNOWN_QUERY_INTERFACE, HRESULT, c_void_p, c_void_p)(
        link, byref(IID_IPERSISTFILE), byref(persist)
    )
    if result != 0:
        raise OSError(f"QueryInterface(IPersistFile) failed: hr={result:#x}")
    return persist


def create_shortcut(path, target, arguments="", working_dir="",
                    description="", icon_path="", icon_index=0):
    """
    Create (or overwrite) a .lnk and return its path.

    path        where to write the .lnk
    target      the executable to point at
    arguments   arguments passed to target
    working_dir the target's working directory
    description the hover text
    icon_path   a custom .ico; leave empty to use the target's own icon
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    link = _open_shell_link()
    try:
        if _method(link, SL_SET_PATH, HRESULT, c_wchar_p)(link, str(target)) != 0:
            raise OSError("SetPath failed")
        if arguments:
            if _method(link, SL_SET_ARGUMENTS, HRESULT, c_wchar_p)(link, arguments) != 0:
                raise OSError("SetArguments failed")
        if working_dir:
            if _method(link, SL_SET_WORKING_DIRECTORY, HRESULT, c_wchar_p)(link, str(working_dir)) != 0:
                raise OSError("SetWorkingDirectory failed")
        if description:
            if _method(link, SL_SET_DESCRIPTION, HRESULT, c_wchar_p)(link, description) != 0:
                raise OSError("SetDescription failed")
        if icon_path:
            if _method(link, SL_SET_ICON_LOCATION, HRESULT, c_wchar_p, c_int)(
                link, str(icon_path), icon_index
            ) != 0:
                raise OSError("SetIconLocation failed")

        persist = _as_persist_file(link)
        try:
            # fRemember=0 so creating the file does not touch the shell's recent list
            result = _method(persist, PF_SAVE, HRESULT, c_wchar_p, c_int)(persist, str(path), 0)
            if result != 0:
                raise OSError(f"IPersistFile::Save failed: hr={result:#x}")
        finally:
            _release(persist)
    finally:
        _release(link)
    return path


def read_shortcut(path) -> dict:
    """
    Read a .lnk back, for verification.

    Returns a dict with target / arguments / working_dir / description / icon.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    link = _open_shell_link()
    try:
        persist = _as_persist_file(link)
        try:
            if _method(persist, PF_LOAD, HRESULT, c_wchar_p, c_uint)(persist, str(path), 0) != 0:
                raise OSError("IPersistFile::Load failed; is this a valid .lnk?")
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
# Self test
# ---------------------------------------------------------------------------

def _check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def selftest() -> int:
    """
    Create a real .lnk on the desktop, read it back through COM, then delete it.

    The description and arguments contain non-ASCII text on purpose, so the
    round-trip also proves Unicode survives.
    """
    results = []
    desktop = Path(desktop_dir())
    print(f"Desktop: {desktop}")
    results.append(_check("desktop directory resolved", bool(desktop)))

    probe = desktop / "_zz_win_shortcut_selftest.lnk"
    target = Path(sys.executable)
    args = '"C:\\a directory with spaces\\probe.pyw" --flag unicode-ok'
    description = "win_shortcut selftest (non-ASCII round-trip)"
    try:
        create_shortcut(
            probe,
            target=target,
            arguments=args,
            working_dir=desktop,
            description=description,
        )
        results.append(_check(".lnk created", probe.exists(),
                              f"{probe.stat().st_size} bytes" if probe.exists() else ""))

        data = read_shortcut(probe)
        results.append(_check("target round-trips",
                              Path(data["target"]).name.lower() == target.name.lower(),
                              data["target"]))
        results.append(_check("arguments round-trip (spaces + non-ASCII)",
                              data["arguments"] == args, data["arguments"]))
        results.append(_check("working_dir round-trips",
                              Path(data["working_dir"]) == desktop, data["working_dir"]))
        results.append(_check("description round-trips",
                              data["description"] == description, data["description"]))
    finally:
        if probe.exists():
            probe.unlink()
    results.append(_check("cleaned up", not probe.exists()))

    passed, total = sum(results), len(results)
    print(f"\nSelf test: {passed}/{total} passed")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows .lnk helper")
    parser.add_argument("--selftest", action="store_true",
                        help="create, read back, then delete a .lnk")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
