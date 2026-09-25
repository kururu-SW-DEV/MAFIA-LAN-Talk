# -*- coding: utf-8 -*-
"""v1.116 — 채팅방을 열면 입력창이 한글 입력 상태로 시작한다(한국어 키보드 배열일 때만)."""
import ctypes, os, sys
sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(BASE); sys.path.insert(0, APP)
if __name__ != "__main__":
    sys.exit(0)
import tkinter as tk
import winapi
ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


r = tk.Tk(); t = tk.Text(r); t.pack(); r.update(); t.focus_force(); r.update()
u = ctypes.windll.user32; u.GetKeyboardLayout.restype = ctypes.c_void_p
korean = ((u.GetKeyboardLayout(0) or 0) & 0xFFFF) == 0x0412
res = winapi.force_korean_ime(t)
check("예외 없이 bool을 돌려줌", isinstance(res, bool))
check("한국어 배열이면 한글 입력 상태가 됨, 아니면 건드리지 않음(False)", res is True if korean else res is False)
src = open(os.path.join(APP, "app.py"), encoding="utf-8").read()
check("채팅방을 여는 경로에서 호출함", "force_korean_ime(self.entry)" in src)
r.destroy()
print("KOREAN IME PASSED" if ALL else "KOREAN IME FAILED"); sys.exit(0 if ALL else 1)
