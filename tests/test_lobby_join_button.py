# -*- coding: utf-8 -*-
"""로비에서 [참가 신청] 버튼이 [참가자 모집] 왼쪽에 항상 보이고, 모집 알림을 놓쳐도 신청할 수 있는지."""
import argparse, importlib.util, os, shutil, socket, sys
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
from mafia_net import encode

tmp = os.path.join(BASE, "tmp_lobbyjoin"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60061, datadir=tmp), [])
ALL = True
sysmsgs = []
_orig = app.add_mafia_system
app.add_mafia_system = lambda t, *a, **k: (sysmsgs.append(t), _orig(t, *a, **k))[1]


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


app._select(("mgame",)); root.update(); root.update_idletasks()
j, s = app.mafia_join_btn, app.mafia_start_btn
check("로비에서 [참가 신청] 버튼이 표시됨", j.winfo_ismapped())
check("[참가 신청]이 [참가자 모집]의 왼쪽에 있음", j.winfo_x() + j.winfo_width() <= s.winfo_x() + 2)
j.invoke() if hasattr(j, "invoke") else app.mafia_toggle_join_clicked(); root.update()
check("접속 상대가 없으면 안내만 하고 신청 상태로 바꾸지 않음",
      any("접속 중인 상대가 없습니다" in m for m in sysmsgs) and not getattr(app, "_my_joined", False))

# 상대가 있는 것처럼: 브로드캐스트 기록
sent = []
app._mafia_broadcast = lambda t, **kw: sent.append((t, kw))
app.engine.peers[("127.0.0.1", 9)] = {"name": "방장", "last": 9e12}
app.mafia_toggle_join_clicked(); root.update()
check("모집 알림 없이도 접속자에게 참가 신청을 보냄", ("recruit_join", {"name": "나"}) in sent)
check("방장이 응답하기 전에는 신청 완료로 표시하지 않음", not getattr(app, "_my_joined", False))

# 방장 쪽: 모집 중 신청을 받으면 신청자에게 개인 쪽지로 모집 상태를 다시 알림
app.engine.peers.clear()
app._start_recruitment(); root.update()
check("방장이 모집 중이면 [참가 신청]이 숨겨짐", not app.mafia_join_btn.winfo_ismapped())
priv = []
app._mafia_send_private = lambda name, t, **kw: priv.append((name, t, kw))
app._on_mafia_proto_msg(encode("recruit_join", name="친구"), "친구", None); root.update()
check("방장 명단에 신청자가 추가됨", "친구" in app._recruited_humans)
check("신청자에게 recruit_start를 개인 쪽지로 돌려줌",
      any(n == "친구" and t == "recruit_start" and "친구" in kw["players"] for n, t, kw in priv))
app.mafia_cancel_recruit_clicked(); root.update()
check("모집을 취소하면 다시 로비 배치([참가 신청] 표시)", app.mafia_join_btn.winfo_ismapped())
root.destroy()
print("LOBBY JOIN PASSED" if ALL else "LOBBY JOIN FAILED")
sys.exit(0 if ALL else 1)
