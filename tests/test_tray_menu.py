# -*- coding: utf-8 -*-
"""test_tray_menu.py — 트레이 아이콘 우클릭 메뉴: [항상 위에 표시] [Windows 시작 시 자동 실행] [알림음] 체크 + [프로그램 종료]."""
import argparse, ctypes, importlib.util, os, shutil, socket, sys, threading, time
if __name__ != "__main__":
    sys.exit(0)
sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(BASE); sys.path.insert(0, APP)
spec = importlib.util.spec_from_file_location("lan_messenger", os.path.join(APP, "lan_messenger.py"))
lm = importlib.util.module_from_spec(spec)
_rb = socket.socket.bind
socket.socket.bind = lambda s, a: _rb(s, ("127.0.0.1", a[1])) if isinstance(a, tuple) and a[0] in ("", "0.0.0.0") else _rb(s, a)
spec.loader.exec_module(lm)
import tkinter as tk
import app as appmod
import winapi

ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


# ---- 1) 트레이 메시지 분류 ----
c = winapi.classify_tray_event
check("좌클릭(0x0202)·더블클릭·풍선 클릭은 창 열기", c(0x0202) == "left" and c(0x0203) == "left" and c(0x0405) == "left")
check("우클릭(0x0205)과 WM_CONTEXTMENU(0x007B)는 메뉴", c(0x0205) == "menu" and c(0x007B) == "menu")
check("버전 4처럼 위 16비트에 아이콘 ID가 실려도 인식", c((1 << 16) | 0x0205) == "menu" and c((1 << 16) | 0x007B) == "menu" and c((1 << 16) | 0x0202) == "left")
check("마우스 이동(0x0200) 등 다른 이벤트는 무시", c(0x0200) is None and c(0) is None)

tmp = os.path.join(BASE, "tmp_traymenu"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("900x600"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60191, datadir=tmp), [])
root.update()

startup = {"on": False}
calls = []
appmod.is_run_at_startup_enabled = lambda: startup["on"]
appmod.set_run_at_startup = lambda on: (startup.update(on=bool(on)) or True)
app._quit = lambda: calls.append("quit")

# ---- 2) 메뉴 구성과 체크 상태 ----
root.attributes("-topmost", False)
app.engine.set_notify_sound_enabled(True)
ent = app._tray_menu_entries()
labels = [e[1] for e in ent if e[0] is not None]
check("메뉴 항목: 항상 위에 표시 / Windows 시작 시 자동 실행 / 알림음 / 프로그램 종료", labels == ["항상 위에 표시", "Windows 시작 시 자동 실행", "알림음", "프로그램 종료"])
check("구분선이 종료 항목 앞에 있음", ent[-2][0] is None and ent[-1][1] == "프로그램 종료")
st = {e[1]: e[2] for e in ent if e[0] is not None}
check("체크 상태: 항상 위 꺼짐·자동 실행 꺼짐·알림음 켜짐", st["항상 위에 표시"] is False and st["Windows 시작 시 자동 실행"] is False and st["알림음"] is True)

# ---- 3) 항목 실행 ----
app._tray_menu_dispatch(app._TRAY_ID_TOPMOST); root.update()
check("[항상 위에 표시]를 고르면 켜지고 체크가 반영됨", bool(root.attributes("-topmost")) and {e[1]: e[2] for e in app._tray_menu_entries() if e[0]}["항상 위에 표시"])
app._tray_menu_dispatch(app._TRAY_ID_TOPMOST); root.update()
check("다시 고르면 꺼짐", not bool(root.attributes("-topmost")))
app._tray_menu_dispatch(app._TRAY_ID_STARTUP)
check("[Windows 시작 시 자동 실행]을 고르면 켜지고 체크가 반영됨", startup["on"] and {e[1]: e[2] for e in app._tray_menu_entries() if e[0]}["Windows 시작 시 자동 실행"])
app._tray_menu_dispatch(app._TRAY_ID_STARTUP)
check("다시 고르면 꺼짐", not startup["on"])
app._tray_menu_dispatch(app._TRAY_ID_SOUND)
check("[알림음]을 고르면 꺼지고 체크가 해제됨", app.engine.notify_sound_enabled is False and not {e[1]: e[2] for e in app._tray_menu_entries() if e[0]}["알림음"])
app._tray_menu_dispatch(app._TRAY_ID_SOUND)
check("다시 고르면 켜짐", app.engine.notify_sound_enabled is True)
app._tray_menu_dispatch(app._TRAY_ID_QUIT)
check("[프로그램 종료]를 고르면 종료 처리(_quit)가 실행됨", calls == ["quit"])
app._tray_menu_dispatch(0)
check("메뉴를 취소(0)하면 아무 일도 없음", calls == ["quit"])

# ---- 4) 훅이 세운 플래그를 메인 루프가 처리 ----
# v1.87 — 메뉴를 여는 실제 Win32 호출은 모달이라 메인 스레드를 막지 않으려고 별도 스레드에서
# 돌고, 선택 실행은 다음 _pump_body 틱에 넘어간다. 그래서 _pump_body() 한 번으로는 아직
# 반영되지 않을 수 있다 — 짧게 폴링한다.
picked = []
appmod.show_native_menu = lambda hwnd, entries: (picked.append([e[1] for e in entries if e[0]]) or app._TRAY_ID_SOUND)
app._tray_menu_pending = True
app._pump_body()
end = time.time() + 1.0
while time.time() < end and not (picked and app.engine.notify_sound_enabled is False):
    root.update(); app._pump_body(); time.sleep(0.02)
check("우클릭 플래그를 메인 루프(_pump)가 처리해 메뉴를 띄우고 선택을 실행함",
      app._tray_menu_pending is False and picked and app.engine.notify_sound_enabled is False)
app.engine.set_notify_sound_enabled(True)

# ---- 4-2) 훅 통합: 실제 WM_TRAYICON 메시지를 창에 보내 우클릭/좌클릭이 각각 처리되는지 ----
user32_ = ctypes.windll.user32
user32_.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
frame = int(root.wm_frame(), 16)
WM_TRAYICON = 0x0400 + 20
for _ in range(3):                                   # 창 이동 중에는 훅이 잠깐 내려가 있다 — 되살린 뒤 시험
    root.update(); app._dnd_rehook(); time.sleep(0.05)
shown = []
appmod.show_native_menu = lambda hwnd, entries: (shown.append(1) or 0)
for lp, name in (((1 << 16) | 0x0205, "우클릭(버전 4 형식)"), (0x0205, "우클릭(버전 0 형식)"), ((1 << 16) | 0x007B, "WM_CONTEXTMENU")):
    shown.clear()
    user32_.PostMessageW(frame, WM_TRAYICON, 1, lp)
    end = time.time() + 0.6
    while time.time() < end and not shown:
        root.update(); app._pump_body(); time.sleep(0.02)
    check(f"실제 트레이 메시지 {name}를 받으면 메뉴가 뜸", bool(shown))
clicked = []
_orig_click = app._on_notify_click
app._on_notify_click = lambda: clicked.append(1)
user32_.PostMessageW(frame, WM_TRAYICON, 1, (1 << 16) | 0x0202)
# v1.87 — 이 훅(WH_GETMESSAGE)은 창을 옮기는 동안 잠깐 내려갔다가 500ms 뒤 되살아난다
# (_dnd_on_configure/_dnd_rehook). 0.6초 창은 그 되살아나는 타이밍과 겹치면 가끔
# 첫 메시지를 놓쳐 실패했다 — 안 잡히면 한 번 더 보내고 창을 넉넉히 늘린다.
end = time.time() + 1.2
resent = False
while time.time() < end and not clicked:
    root.update(); app._pump_body(); time.sleep(0.02)
    if not clicked and not resent and time.time() > end - 0.6:
        user32_.PostMessageW(frame, WM_TRAYICON, 1, (1 << 16) | 0x0202)
        resent = True
check("좌클릭은 그대로 창 열기(_on_notify_click)로 동작하고 메뉴는 뜨지 않음", bool(clicked))
app._on_notify_click = _orig_click

# ---- 5) 실제 윈도우 기본 팝업 메뉴가 뜨는지(자동으로 ESC 입력) ----
user32 = ctypes.windll.user32
seen = {}


def helper():
    time.sleep(0.6)
    hwnd = user32.FindWindowW("#32768", None)          # 윈도우 팝업 메뉴의 클래스 이름
    seen["menu_window"] = bool(hwnd)
    user32.keybd_event(0x1B, 0, 0, None)                # ESC 눌러 닫기
    user32.keybd_event(0x1B, 0, 2, None)


th = threading.Thread(target=helper, daemon=True); th.start()
cmd = winapi.show_native_menu(int(root.wm_frame(), 16), [(1, "항상 위에 표시", False), (2, "Windows 시작 시 자동 실행", True),
                                                           (3, "알림음", True), (None, "", False), (4, "프로그램 종료", False)])
th.join(3)
check("실제 팝업 메뉴 창이 화면에 나타남", seen.get("menu_window") is True)
check("ESC로 취소하면 0을 돌려줌", cmd == 0)
try:
    root.destroy()
except Exception:
    pass
print("TRAY MENU", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
