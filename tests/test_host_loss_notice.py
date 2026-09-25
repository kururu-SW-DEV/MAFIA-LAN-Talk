# -*- coding: utf-8 -*-
"""v1.115 — 참가자 쪽: 게임 중 방장 연결이 끊기면 15초에 경고, 다시 오면 복구 안내, 45초에 로비 복귀."""
import argparse, importlib.util, os, shutil, socket, sys, time
sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(BASE); sys.path.insert(0, APP)
if __name__ != "__main__":
    sys.exit(0)
spec = importlib.util.spec_from_file_location("lan_messenger", os.path.join(APP, "lan_messenger.py"))
lm = importlib.util.module_from_spec(spec)
_rb = socket.socket.bind
socket.socket.bind = lambda s, a: _rb(s, ("127.0.0.1", a[1])) if isinstance(a, tuple) and a[0] in ("", "0.0.0.0") else _rb(s, a)
spec.loader.exec_module(lm)
import tkinter as tk
from mafia_net import encode
ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


tmp = os.path.join(BASE, "tmp_hostloss"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.update()
app = lm.App(root, argparse.Namespace(name="클라", port=60141, datadir=tmp), [])
msgs = []
app.add_mafia_system = lambda text, **kw: msgs.append(text)
app._is_host_stub = False
app.mafia_active = True; app.mafia_host_mode = False
app._recruiter_host = "방장"
app._client_watch_reset()
app._note_host_alive("hb")
check("hb를 받으면 hb 수신 표시가 켜짐", app._host_hb_seen is True)

now = time.time()
app._mafia_last_host_ts = now - 5
check("5초 침묵은 조용", app._client_host_watch() is False and not msgs)
app._mafia_last_host_ts = now - 16
check("16초 침묵은 경고(종료 아님)", app._client_host_watch() is False and len(msgs) == 1 and "연결이 끊긴" in msgs[0])
app._client_host_watch()
check("경고는 한 번만", len(msgs) == 1)
app._mafia_last_host_ts = time.time()
app._client_host_watch()
check("소식이 다시 오면 복구 안내", len(msgs) == 2 and "다시 확인" in msgs[1] and app._host_warned is False)
app._mafia_last_host_ts = time.time() - 50
ended = app._client_host_watch()
check("45초 넘게 끊기면 게임을 접고 로비로", ended is True and app.mafia_active is False and "로비" in msgs[-1])

# hb를 한 번도 못 받은 옛 방장 + 접속 목록에 방장이 살아 있으면 15초 침묵만으로는 경고하지 않음
msgs.clear()
app.mafia_active = True; app._recruiter_host = "방장"
app._client_watch_reset()
app._mafia_peer_of = lambda n: ("127.0.0.1", 1)
app.engine.get_peer = lambda addr: {"last": time.time()}
app._mafia_last_host_ts = time.time() - 20
app._client_host_watch()
check("옛 방장(hb 없음)이 접속 목록에 살아 있으면 오경보 없음", not msgs)
app.engine.get_peer = lambda addr: None
app._client_host_watch()
check("접속 목록에서도 방장이 사라졌으면 경고", len(msgs) == 1)
root.destroy()
print("HOST LOSS PASSED" if ALL else "HOST LOSS FAILED"); sys.exit(0 if ALL else 1)
