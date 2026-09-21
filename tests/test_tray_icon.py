# -*- coding: utf-8 -*-
"""test_tray_icon.py — 트레이 아이콘이 윈도우 기본 아이콘으로 떨어지지 않고, 트레이 크기(SM_CXSMICON)로 로드되는지.

- 개발 실행: app.ico 파일을 트레이 크기로 로드
- 단일 exe 시뮬레이션: app.ico가 없어도(PyInstaller --icon은 exe 리소스에만 들어감) exe 내장 아이콘을 사용
  (EXE 경로는 환경변수 TRAY_TEST_EXE 또는 scratchpad 빌드본이 있을 때만 검사)
"""
import ctypes, os, sys
from ctypes import wintypes
if __name__ != "__main__":
    sys.exit(0)
sys.stdout.reconfigure(encoding="utf-8")
if os.name != "nt":
    print("SKIP (Windows 전용)"); sys.exit(0)
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, APP)
import tkinter as tk
import winapi

ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", wintypes.LPVOID)]


def icon_size(h):
    ii = ICONINFO()
    ctypes.windll.user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
    if not ctypes.windll.user32.GetIconInfo(h, ctypes.byref(ii)):
        return None
    bm = BITMAP()
    g = ctypes.windll.gdi32
    g.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    g.GetObjectW(ii.hbmColor, ctypes.sizeof(bm), ctypes.byref(bm))
    g.DeleteObject.argtypes = [wintypes.HANDLE]
    g.DeleteObject(ii.hbmColor); g.DeleteObject(ii.hbmMask)
    return bm.bmWidth


root = tk.Tk(); root.withdraw()
n = winapi.Notifier.__new__(winapi.Notifier)
n.root = root
n._user32 = ctypes.windll.user32
n._shell32 = ctypes.windll.shell32
sm = ctypes.windll.user32.GetSystemMetrics(49)

# 1) 개발 실행(app.ico 있음)
h = n._load_tray_icon()
check("app.ico에서 트레이 아이콘을 로드함", bool(h))
check(f"트레이 크기(SM_CXSMICON={sm})로 로드됨 (실제 {icon_size(h)}px)", icon_size(h) == sm)
ctypes.windll.user32.DestroyIcon(h)

# 2) 단일 exe 시뮬레이션(app.ico 없음 + frozen)
exe = os.environ.get("TRAY_TEST_EXE", "")
if exe and os.path.exists(exe):
    real_dir, real_frozen, real_exec = winapi.resource_dir, getattr(sys, "frozen", False), sys.executable
    winapi.resource_dir = lambda: os.path.join(APP, "__no_such_dir__")
    sys.frozen = True; sys.executable = exe
    try:
        h2 = n._load_tray_icon()
    finally:
        winapi.resource_dir, sys.executable = real_dir, real_exec
        sys.frozen = real_frozen
        if not real_frozen:
            del sys.frozen
    check("app.ico가 없어도 exe 내장 아이콘을 가져옴", bool(h2))
    check(f"exe 내장 아이콘도 트레이 크기 근처 (실제 {icon_size(h2)}px)", bool(h2) and icon_size(h2) in (sm, 16, 20, 24, 32))
else:
    print("SKIP  exe 시뮬레이션 (TRAY_TEST_EXE 미지정)")
root.destroy()
print("TRAY ICON PASSED" if ALL else "TRAY ICON FAILED")
sys.exit(0 if ALL else 1)
