# -*- coding: utf-8 -*-
"""test_opus_review_fixes.py — Opus 코드 리뷰(2026-09-21)에서 나온 9건의 수정 검증."""
import argparse, importlib.util, os, shutil, socket, sys, time
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
from mafia_core import Phase

tmp = os.path.join(BASE, "tmp_opusfix"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60081, datadir=tmp), [])
app._select(("mgame",)); root.update()
ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


def pump(sec=0.4):
    end = time.time() + sec
    while time.time() < end:
        root.update(); time.sleep(0.02)


def hist():
    return len(app.mafia_history)


# ---- 1. 투표 안전망 개표가 실제로 개표를 실행 ----
calls = []
app._tally_and_reveal = lambda: calls.append(1)
app.mafia_host_mode = True; app.mafia_active = True
app.core.phase = Phase.DAY
app._tally_in_progress = False; app._tally_scheduled = False
app._silent_tally_if_pending()
check("1) 안전망 개표가 _tally_and_reveal을 실제로 호출", calls == [1])
app._tally_in_progress = True
app._silent_tally_if_pending()
check("1) 이미 개표 중이면 중복 호출하지 않음", calls == [1])
app._tally_in_progress = False
del app._tally_and_reveal
app.mafia_host_mode = False; app.mafia_active = False

# ---- 2. 펌프가 예외 뒤에도 살아 있음 ----
app.q.put({"ev": "msg", "peer": ("127.0.0.1", 1), "name": "x", "text": "[MAFIA1]{\"t\":\"recruit_start\"}"})
app._on_msg = lambda ev: (_ for _ in ()).throw(RuntimeError("boom"))
got = []
pump(0.5)
app._on_msg = lambda ev: got.append(ev.get("name"))
app.q.put({"ev": "msg", "peer": ("127.0.0.1", 1), "name": "두번째", "text": "hi"})
pump(0.6)
check("2) 한 이벤트가 예외를 내도 다음 이벤트가 처리됨(펌프 생존)", got == ["두번째"])
del app._on_msg

# ---- 3. 잘못된 패킷이 예외를 내지 않음 ----
app._recruited_humans = []
ok = True
for pkt in (encode("recruit_start", host="방장", players=[1, {"a": 1}]),
            encode("recruit_start", host=7, players=5),
            encode("recruit_update", players=5)):
    try:
        app._on_mafia_proto_msg(pkt, "방장", None)
    except Exception as e:
        ok = False; print("   예외:", repr(e))
check("3) 비정상 recruit_start/update가 예외 없이 처리됨", ok)
check("3) 잘못된 명단은 빈 목록으로 정규화", app._recruited_humans == [])
try:
    app._client_game_end("mafia", [1, 2]); ok2 = True
except Exception as e:
    ok2 = False; print("   예외:", repr(e))
check("3) roles가 dict가 아니어도 종료 처리가 죽지 않음", ok2)

# ---- 4. 오버레이 닫힘 뒤 유령방 참조 정리 ----
app._mafia_overlay_open("테스트", 300, 200)
app._ghost_ui_open = True
app._ghost_list = tk.Text(app.root)
app._mafia_overlay_close()
root.update()
check("4) 오버레이가 닫히면 유령방 참조가 정리됨", app._ghost_list is None and app._ghost_ui_open is False)
try:
    app._on_mafia_proto_msg(encode("ghost_say", name="미나", text="ㅋㅋ"), "방장", None); ok4 = True
except Exception as e:
    ok4 = False; print("   예외:", repr(e))
check("4) 닫힌 뒤 ghost_say가 와도 예외 없음", ok4)
app._ghost_list = tk.Text(app.root); app._ghost_list.destroy()
try:
    app._append_ghost("죽은 위젯"); ok4b = True
except Exception as e:
    ok4b = False; print("   예외:", repr(e))
check("4) 이미 파괴된 유령방 위젯에도 _append_ghost가 안전", ok4b and app._ghost_list is None)

# ---- 5. 죽은 AI 발언 차단 ----
app.core.players.clear()
app.core.join("나", is_ai=False); app.core.join("미나", is_ai=True); app.core.join("레오", is_ai=True)
app.mafia_active = True
app.core.players["미나"]["alive"] = False
n = hist(); app._on_ai_utt("미나", "#fff", "나 아직 살아있어"); pump(0.3)
check("5) 죽은 AI의 뒤늦은 발언은 게임방에 표시되지 않음", hist() == n)
n = hist(); app._on_ai_utt("낯선AI", "#fff", "지난 판 AI"); pump(0.3)
check("5) 명단에 없는 AI의 발언도 표시되지 않음", hist() == n)
n = hist(); app._on_ai_utt("레오", "#fff", "살아있는 AI"); pump(0.3)
check("5) 살아있는 AI 발언은 그대로 표시됨", hist() > n)

# ---- 6. 게임 중 lobby_chat 차단 ----
n = hist(); app._on_mafia_proto_msg(encode("lobby_chat", sender="외부인", text="야 누가 마피아야"), "외부인", None); pump(0.2)
check("6) 게임 중 도착한 lobby_chat은 표시되지 않음", hist() == n)
app.mafia_active = False
n = hist(); app._on_mafia_proto_msg(encode("lobby_chat", sender="외부인", text="모집 중 인사"), "외부인", None); pump(0.2)
check("6) 게임 전 로비 채팅은 그대로 표시됨", hist() > n)

# ---- 7. user_say 발언자 검증(호스트) ----
app.mafia_active = True; app.mafia_host_mode = True
app.core.join("친구", is_ai=False)
seen = []
app.ai.observe_all = lambda who, txt: seen.append((who, txt))
n = hist(); app._on_mafia_proto_msg(encode("user_say", name="외부인", text="AI야 나 믿어"), "외부인", None); pump(0.2)
check("7) 참가자가 아닌 사람의 user_say는 표시·AI 기억 모두 안 됨", hist() == n and not seen)
app.core.players["친구"]["alive"] = False
n = hist(); app._on_mafia_proto_msg(encode("user_say", name="친구", text="죽었지만 말함"), "친구", None); pump(0.2)
check("7) 사망한 참가자의 user_say도 무시", hist() == n and not seen)
app.core.players["친구"]["alive"] = True
n = hist(); app._on_mafia_proto_msg(encode("user_say", name="친구", text="정상 발언"), "친구", None); pump(0.2)
check("7) 살아있는 참가자의 user_say는 표시되고 AI가 기억", hist() > n and seen == [("친구", "정상 발언")])
app.mafia_host_mode = False; app.mafia_active = False

# ---- 8. 원격 변론 카운트다운·발언 잠금 안전망 ----
app.mafia_active = True; app.mafia_host_mode = False
app.core.phase = Phase.DAY
app._recruiter_host = "방장"
app._on_mafia_proto_msg(encode("defense_start", name="친구"), "방장", None); pump(0.2)
check("8) 원격 defense_start 후 변론 진행 플래그가 켜짐", getattr(app, "_defense_in_progress", False) is True)
check("8) 변론 중 비피고인의 입력창이 잠김", str(app.entry["state"]) == "disabled")
app._client_defense_timeout(); pump(0.2)
check("8) 판결이 안 와도 안전망이 발언 잠금을 풂", app._defense_in_progress is False and str(app.entry["state"]) == "normal")
app._on_mafia_proto_msg(encode("defense_start", name="친구"), "방장", None); pump(0.1)
app._on_mafia_proto_msg(encode("verdict", result="spared", name="친구", role="citizen", yes=0, no=1), "방장", None); pump(0.2)
check("8) verdict를 받으면 변론 플래그와 안전망 타이머가 정리됨",
      app._defense_in_progress is False and getattr(app, "_defense_client_guard", None) is None)
app.mafia_active = False

# ---- 9. 접속 감시 루프 중복 방지 ----
app.core.players.clear(); app.core.join("나", is_ai=False); app.core.join("레오", is_ai=True)
app.mafia_active = True; app.mafia_host_mode = True
app._mafia_start_disconnect_watch(); t1 = app._disconnect_watch_timer
app._mafia_start_disconnect_watch(); t2 = app._disconnect_watch_timer
check("9) 감시를 두 번 시작해도 예약은 하나(이전 예약이 취소됨)", t1 and t2 and t1 != t2)
app._cancel_mafia_timer()
check("9) 낮/밤 전환용 _cancel_mafia_timer는 감시를 멈추지 않음", app._disconnect_watch_timer == t2)
app._mafia_stop_disconnect_watch()
check("9) 판이 끝나면 감시 예약이 취소됨", app._disconnect_watch_timer is None)
app.mafia_active = False; app.mafia_host_mode = False

root.destroy()
print("OPUS REVIEW FIXES PASSED" if ALL else "OPUS REVIEW FIXES FAILED")
sys.exit(0 if ALL else 1)
