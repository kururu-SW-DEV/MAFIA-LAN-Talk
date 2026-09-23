# -*- coding: utf-8 -*-
"""test_v1100_fixes.py — Opus 6차 리뷰: 방장/AI 이름 사칭 차단, 나간 사람 재접속 오판, 죽은 피고인 재판 무효 등."""
import argparse, importlib.util, os, queue, shutil, socket, sys, time
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
import mafia_ai
from mafia_core import GameCore, Phase

ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


tmp = os.path.join(BASE, "tmp_v1100"); shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(os.path.join(tmp, "A")); os.makedirs(os.path.join(tmp, "B"))
open(os.path.join(tmp, "A", "firewall_notice_done"), "w").close()
open(os.path.join(tmp, "B", "firewall_notice_done"), "w").close()
A_PORT, B_PORT = 60083, 60084

root = tk.Tk(); root.geometry("1000x700"); root.withdraw()

app = lm.App(root, argparse.Namespace(name="방장", port=A_PORT, datadir=os.path.join(tmp, "A")),
             [f"127.0.0.1:{B_PORT}"])
app._select(("mgame",)); root.update()
core = app.core
core.players.clear()
for n, a in (("방장", False), ("이팀장B", False), ("철수", True), ("영희", True), ("미나", True)):
    core.join(n, is_ai=a)
core.phase = Phase.DAY; core.day_no = 1
app.mafia_active = True
app.mafia_host_mode = True
app._recruiting = False



from mafia_net import encode
B=("127.0.0.1", B_PORT)
def setup_host():
    app.core.lobby_reset(); app.core.players.clear()
    for n, a in (("방장", False), ("이팀장B", False), ("철수", True), ("영희", True), ("미나", True)):
        app.core.join(n, is_ai=a)
    for _n, _r in (("방장","citizen"),("이팀장B","citizen"),("철수","mafia"),("영희","citizen"),("미나","citizen")):
        app.core.players[_n]["role"] = _r
    app.core.phase = Phase.DAY; app.core.day_no = 1
    app.mafia_active = True; app.mafia_host_mode = True; app._recruiter_host = "방장"
    app._reset_ident(); app._ident()["이팀장B"] = B
    app._mafia_tokens = {"이팀장B": "TOK"}
    app._mafia_left = set(); app._mafia_disconnected = set()
try:
    app._select_mafia_room(("mgame",)); root.update()
    setup_host()
    app.engine.name = "방장"
    # 1) 방장·AI 이름 사칭 거부
    for t, kw in (("leave_game", dict(name="방장", tok="x")), ("night_action", dict(actor="철수", role="police", target="영희")),
                  ("user_say", dict(name="방장", text="hi")), ("vote_cast", dict(voter="미나", target="영희"))):
        ok = app._proto_authorized(t, dict(kw, t=t), kw.get("name") or kw.get("actor") or kw.get("voter"), B)
        check(f"방장·AI 명의 {t} 거부", ok is False)
    check("정상 참가자 명의는 통과", app._proto_authorized("user_say", {"t":"user_say","name":"이팀장B","text":"x"}, "이팀장B", B) is True)
    # 2) 나간 사람은 재접속으로 오판하지 않음
    app._on_mafia_proto_msg(encode("leave_game", name="이팀장B", tok="TOK"), "이팀장B", B)
    check("leave_game으로 사망", app.core.players["이팀장B"]["alive"] is False)
    check("_mafia_left에 기록", "이팀장B" in app._mafia_left)
    n = len(app.mafia_history)
    app._mafia_poll_disconnects()
    check("폴링이 '다시 연결되었습니다'를 내지 않음", not any("다시 연결" in r.get("text","") for r in app.mafia_history[n:]))
    n = len(app.mafia_history)
    app._on_mafia_proto_msg(encode("leave_game", name="이팀장B", tok="TOK"), "이팀장B", B)
    check("중복 leave_game은 조용히 무시", len(app.mafia_history) == n)
    # 3) 죽은 피고인 재판은 무효(처형·직업 공개 없음)
    setup_host()
    app.core.set_defendant("이팀장B")
    app.core.players["이팀장B"]["alive"] = False
    n = len(app.mafia_history)
    app._enter_night_sequence = lambda *a, **k: None
    app._resolve_defense("이팀장B"); root.update()
    txt = " ".join(r.get("text","") for r in app.mafia_history[n:])
    check("재판 무효 안내", "무효" in txt)
    check("직업 공개·처형 문구 없음", "직업 공개" not in txt and "처형 확정" not in txt)
    check("defendant 정리됨", app.core.defendant is None)
    # 4) 공백 있는 이름의 경찰 조사 기록
    import re
    m = re.search(r"\]\s*(.+?)님은 (마피아입니다|마피아가 아닙니다)", "🕵 [밤 조사 결과 — 나에게만] 홍 길동님은 마피아입니다")
    check("공백 이름 조사 결과 파싱", m and m.group(1) == "홍 길동")
    # 5) 클라이언트는 방장이 아닌 사람의 user_say를 받지 않음
    app.mafia_host_mode = False; app._recruiter_host = "다른방장"; app.mafia_active = True
    app._reset_ident()
    check("클라이언트: 제3자 user_say 거부", app._proto_authorized("user_say", {"t":"user_say","name":"철수","text":"x"}, "철수", B) is False)
finally:
    try: app._cancel_mafia_timer()
    except Exception: pass
    try: root.destroy()
    except Exception: pass

print("V1100 FIXES", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
