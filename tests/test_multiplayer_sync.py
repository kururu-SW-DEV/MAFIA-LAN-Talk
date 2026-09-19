# -*- coding: utf-8 -*-
"""test_multiplayer_sync.py — "복수 인간(2명 이상) + AI" 플레이 회귀 테스트.

전수 검토 보고서(2026-09-19)가 지적한 항목들을, 실제 호스트(A, 완전한 App+GUI)와
실제 원격 참가자(B)가 진짜 localhost UDP로 [MAFIA1] 패킷을 주고받으며 검증한다.

B는 완전한 App(아바타 원형 이미지 등 GUI 전체)을 새로 만드는 대신, 같은
프로세스 안에서 두 번째 App을 만들면 위젯 캐시 일부가 Tcl 인터프리터 경계를
넘어가며 깨지는 문제가 있어(app.py/canvas_utils.py의 아바타 원형 이미지
모듈 전역 캐시가 첫 App의 Tk 인터프리터에 묶인 PhotoImage를 두 번째 App의
다른 인터프리터에 재사용하려다 "image ... doesn't exist"로 죽음 — 실측),
MafiaUIMixin/DialogsMixin만 가진 경량 스텁으로 대신한다. 실제 네트워크
송수신·프로토콜 디스패치(_on_mafia_proto_msg)·core 반영은 전부 진짜 코드
그대로 태운다 — 가짜인 건 아바타 등 B의 나머지 채팅 UI뿐이다.

검증 항목:
  ① 게임 시작 후 사람 발언이 서로에게 실제로 전달된다(user_say).
  ② 원격 참가자(B)의 core.players가 "start" 수신 후 실제로 채워진다.
  ③ B가 낮 투표 팝업에서 고른 표가 A(호스트)의 core에 실제로 반영된다(vote_cast).
  ④ B가 밤 행동(마피아 살해)을 고르면 A의 core에 실제로 반영된다(night_action).
  ⑤ B가 찬반(처형여부) 투표를 하면 A의 core에 실제로 반영된다(defense_vote_cast).
  ⑥ B의 접속이 끊기면 A가 자동으로 사망 처리하고, 그 사실이 방송된다(death).
"""
import argparse
import importlib.util
import os
import queue
import random
import re
import shutil
import socket
import sys
import time

if __name__ != "__main__":
    sys.exit(0)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(BASE)
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

spec = importlib.util.spec_from_file_location("lan_messenger", os.path.join(APP_ROOT, "lan_messenger.py"))
lm = importlib.util.module_from_spec(spec)

_real_bind = socket.socket.bind


def _spy(self, addr):
    if isinstance(addr, tuple) and len(addr) == 2 and addr[0] in ("", "0.0.0.0"):
        _real_bind(self, ("127.0.0.1", addr[1]))
    else:
        _real_bind(self, addr)


socket.socket.bind = _spy
spec.loader.exec_module(lm)

import tkinter as tk
import mafia_ai
from mafia_core import GameCore, Phase
from mafia_ui import MafiaUIMixin
from dialogs import DialogsMixin
from app import App as RealApp


def fake_llm_call(messages, max_tokens=350, timeout=45):
    sys_c = messages[0]["content"] if messages else ""
    user_c = messages[-1]["content"] if messages else ""
    m = re.search(r"참가자 '([^']+)'", sys_c)
    name = m.group(1) if m else "AI"
    if "준비완료" in user_c:
        return f"{name} 준비완료"
    if "투표" in user_c and "생존 후보" in user_c:
        cm = re.search(r"생존 후보(?:\(당신 제외\))?:\s*([^\n]+)", user_c)
        pool = [c.strip() for c in (cm.group(1).split(",") if cm else []) if c.strip()]
        pick = random.choice(pool) if pool else name
        return f"투표 {pick}"
    return "음 좀 더 지켜볼게요 ㅋㅋ"


mafia_ai._llm_call = fake_llm_call

tmp = os.path.join(BASE, "tmp_multiplayer_sync")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(os.path.join(tmp, "A"), exist_ok=True)
os.makedirs(os.path.join(tmp, "B"), exist_ok=True)
open(os.path.join(tmp, "A", "firewall_notice_done"), "w", encoding="utf-8").close()
open(os.path.join(tmp, "B", "firewall_notice_done"), "w", encoding="utf-8").close()

A_PORT, B_PORT = 60031, 60032

rootA = tk.Tk(); rootA.withdraw()
# v1.61 — 두 번째 tk.Tk()를 따로 만들면 ImageTk.PhotoImage가 master를 안 줬을 때
# tkinter._default_root(마지막에 만든 Tk가 됨)에 잘못 묶여, A쪽 위젯에 쓰는
# 이미지가 B의 인터프리터에 만들어지는 등 뒤섞여 "image ... doesn't exist"로
# 깨진다(실측). Toplevel(rootA)로 만들면 인터프리터가 하나뿐이라 이 문제 자체가
# 없다 — B는 완전한 App이 아니라 경량 스텁이라 이렇게 해도 실제 검증 내용은
# 그대로다.
rootB = tk.Toplevel(rootA); rootB.withdraw()

argsA = argparse.Namespace(name="김재무A", port=A_PORT, datadir=os.path.join(tmp, "A"))
appA = lm.App(rootA, argsA, [f"127.0.0.1:{B_PORT}"])


class ClientStub(DialogsMixin, MafiaUIMixin):
    """B(원격 참가자) 대역 — 실제 네트워크/프로토콜 처리 코드는 App과 동일하게
    타되, 아바타 등 무거운 채팅 UI만 없는 경량 스텁."""

    def _on_msg(self, ev):
        return RealApp._on_msg(self, ev)


b_q = queue.SimpleQueue()
B_engine = lm.Engine("이팀장B", port=B_PORT, datadir=os.path.join(tmp, "B"),
                     on_event=b_q.put, instance_id="B" * 10)
B_engine.set_static_targets({("127.0.0.1", A_PORT)})

stubB = ClientStub()
stubB.root = rootB
stubB.engine = B_engine
stubB.core = GameCore("mafia-room")
# 실제 클라이언트도 mafia_ready()에서 AIDirector()를 만들어 두지만(host만
# spawn_all/assign_roles를 호출해 실제 플레이어를 채운다) 클라이언트 쪽은
# 항상 비어 있다 — 실제 상태를 그대로 재현(None으로 두면 _trigger_ai_reactions
# 류가 클라이언트 자기 자신에게서도 호출될 때 AttributeError로 죽어서 실제
# 앱과 다른 동작이 됨).
stubB.ai = mafia_ai.AIDirector()
stubB.mafia_active = False
stubB.mafia_history = []
stubB.current = None
stubB._my_mafia_role = None
stubB._recruiting = False
stubB._recruited_humans = []
stubB._my_joined = False
stubB._mafia_overlay = None
stubB.mafia_phase_lbl = tk.Label(rootB)  # "recruit_start" 핸들러가 무조건 건드림


def _drain_b():
    while True:
        try:
            ev = b_q.get_nowait()
        except queue.Empty:
            break
        if ev.get("ev") == "msg":
            try:
                stubB._on_msg(ev)
            except Exception as e:
                print("stubB._on_msg 예외:", repr(e))


ok_all = True


def check(label, cond):
    global ok_all
    result = bool(cond)
    print(("OK  " if result else "FAIL"), label)
    if not result:
        ok_all = False
    return result


def pump(cond, timeout=20.0):
    """v1.61 — 단순 root.update() 반복이 아니라 root.mainloop()+quit()로 돌린다.
    호스트(A)는 AI 부트스트랩을 백그라운드 스레드에서 진행하며 거기서
    root.after(...)로 메인스레드에 콜백을 등록하는데, Tk는 mainloop에 한 번도
    안 들어간 인터프리터에 대해 이 크로스스레드 after 등록을 거부한다
    ("main thread is not in main loop") — 실측. mainloop 안에서 매 tick마다
    B쪽(rootB)도 같이 펌프한다."""
    state = {"start": time.time(), "done": False}

    def _poll():
        try:
            rootB.update()
        except tk.TclError:
            pass
        _drain_b()
        try:
            if cond():
                state["done"] = True
                rootA.quit()
                return
        except tk.TclError:
            rootA.quit()
            return
        if time.time() - state["start"] > timeout:
            rootA.quit()
            return
        rootA.after(15, _poll)

    rootA.after(0, _poll)
    rootA.mainloop()
    return state["done"]


A_sys = []
_a_orig_sys = appA.add_mafia_system


def _spyA(t):
    A_sys.append(t); return _a_orig_sys(t)


appA.add_mafia_system = _spyA

try:
    check("A가 B를 발견", pump(lambda: len(appA.engine.peers) >= 1, timeout=10))
    check("B가 A를 발견", pump(lambda: len(stubB.engine.peers) >= 1, timeout=10))

    appA._select(("mgame",))

    # ---- 1단계: A가 모집 시작 → B가 실제 네트워크로 수신하고 참가 신청 ----
    appA.mafia_start_clicked()
    check("B가 모집 개시를 실제로 수신함", pump(lambda: stubB._recruiting is True))

    stubB.mafia_toggle_join_clicked()
    check("A가 B의 참가 신청을 실제로 수신함", pump(lambda: "이팀장B" in appA._recruited_humans))
    check("B 자신도 명단 갱신을 수신함", pump(lambda: "이팀장B" in stubB._recruited_humans))

    # ---- 2단계: 모집 마감 + 게임 시작(인간 2 + AI 3) ----
    appA.mafia_ai_count = 3
    appA.mafia_start_clicked()
    check("A: 게임 활성화", pump(lambda: appA.mafia_active))
    check("A: 낮 1일차 진입", pump(lambda: appA.core.phase == Phase.DAY))

    # ===================== ② 원격 참가자 명단/역할 동기화 =====================
    check("② B의 core.players가 start 수신 후 실제로 채워짐",
          pump(lambda: "이팀장B" in stubB.core.players and len(stubB.core.players) >= 2, timeout=15))
    check("② B의 core.phase도 DAY로 동기화됨", pump(lambda: stubB.core.phase == Phase.DAY))
    check("② B 자신도 core.players 안에서 is_ai=False로 정확히 표시됨",
          not stubB.core.players.get("이팀장B", {}).get("is_ai", True))
    check("② B가 자기 자신의 역할을 배정받음(비밀 유지, 본인만)",
          pump(lambda: stubB.core.players.get("이팀장B", {}).get("role") is not None, timeout=10))

    ai_names = [n for n, p in appA.core.players.items() if p.get("is_ai")]
    check("AI 3명 명단 확보", len(ai_names) == 3)

    pump(lambda: False, timeout=0.3)
    try:
        appA._mafia_overlay_close()
    except Exception:
        pass
    try:
        stubB._mafia_overlay_close()
    except Exception:
        pass

    # ===================== ① 게임 시작 후 사람 발언 상호 전달 =====================
    appA._mafia_handle_user_text("안녕하세요 A입니다")
    check("① B가 A의 낮 발언을 실제로 수신함",
          pump(lambda: any("안녕하세요 A입니다" in r.get("text", "") for r in stubB.mafia_history), timeout=10))

    stubB._mafia_handle_user_text("반갑습니다 B입니다")
    check("① A가 B의 낮 발언을 실제로 수신함",
          pump(lambda: any("반갑습니다 B입니다" in r.get("text", "") for r in appA.mafia_history), timeout=10))

    # ===================== ③ 낮 투표 동기화(클라이언트 → 호스트) =====================
    appA._tally_and_reveal = lambda *a, **kw: None
    appA._silent_tally_if_pending = lambda *a, **kw: None
    appA._schedule_tally = lambda *a, **kw: None

    vote_target = ai_names[0]
    stubB._popup_vote(vote_target)
    check("③ A(호스트)의 core.votes에 B의 실제 선택이 반영됨",
          pump(lambda: appA.core.votes.get("이팀장B") == vote_target, timeout=10))
    check("③ B 자신의 로컬 화면에도 투표 완료로 표시됨",
          stubB.core.votes.get("이팀장B") == vote_target)

    # ===================== 투표 개시 동기화(vote_open) =====================
    appA.open_the_vote()
    check("vote_open: 원격(B) 화면에도 투표 팝업이 실제로 열림",
          pump(lambda: getattr(stubB, "_vote_lbl", None) is not None, timeout=10))
    stubB._cancel_vote_popup()
    try:
        appA._cancel_vote_popup()
    except Exception:
        pass

    # ===================== 동률 재투표 동기화(revote_open) =====================
    appA._tally_full = lambda *a, **kw: None   # 실제 2차 개표(최후변론 진행 등)는 이 테스트 범위 밖
    tied = [ai_names[0], ai_names[1]]
    appA._open_revote_popup(tied)
    check("revote_open: 원격(B) 화면에도 재투표 팝업이 실제로 열림",
          pump(lambda: stubB._mafia_overlay is not None and stubB._revote_tied == tied, timeout=10))
    stubB._cast_revote(tied[0])
    check("revote: 원격(B)의 재투표 선택이 호스트(A)에 실제로 반영됨",
          pump(lambda: appA.core.votes.get("이팀장B") == tied[0], timeout=10))
    try:
        appA._mafia_overlay_close()
    except Exception:
        pass
    dl = getattr(appA, "_revote_deadline", None)
    if dl:
        appA.root.after_cancel(dl)
        appA._revote_deadline = None
    appA._revote_tied = None
    appA.core.votes.clear()
    appA.core.abstains.clear()

    # ===================== ④ 밤 행동 동기화(클라이언트 → 호스트) =====================
    # 실제 무작위 역할 배정을 기다리지 않고 시나리오 검증을 위해 마피아로
    # 강제 지정한다(호스트의 core를 직접 바꾸는 것은 테스트 셋업 전용 단축이지,
    # 실제 플레이 경로가 아니다).
    with appA.core.lock:
        appA.core.players["이팀장B"]["role"] = "mafia"
    # 역할은 무작위 배정이라 ai_names[1]이 마피아 동료일 수 있다(그러면 호스트가
    # 정당하게 거절) — 검증 대상은 '전달·반영'이므로 확실히 비마피아인 대상을 고른다.
    victim = next(n for n, p in appA.core.players.items()
                  if n != "이팀장B" and p.get("role") != "mafia")
    with appA.core.lock:
        appA.core.phase = Phase.NIGHT
    stubB.core.players["이팀장B"]["role"] = "mafia"
    stubB.core.phase = Phase.NIGHT
    stubB._my_mafia_role = "mafia"

    stubB._apply_night_pick(victim, "mafia")
    check("④ A(호스트)의 core.night_target에 B가 보낸 밤 행동이 실제로 반영됨",
          pump(lambda: appA.core.night_target == victim, timeout=10))

    # ===================== 마피아 공모 조율(호스트) =====================
    a_nm = "김재무A"
    with appA.core.lock:
        for n, p_ in appA.core.players.items():
            p_["role"] = "citizen"
        appA.core.players["이팀장B"]["role"] = "mafia"
        appA.core.players[ai_names[0]]["role"] = "mafia"
        appA.core.phase = Phase.NIGHT
        appA.core.night_targets.clear()
        appA.core.night_targets[ai_names[0]] = ai_names[2]      # AI는 먼저, 다른 대상
        appA.core.night_targets["이팀장B"] = ai_names[1]        # 사람 선택이 팀 결정
    appA._reconcile_mafia_night()
    check("공모: 사람 마피아 선택으로 AI 마피아가 통일되어 합의(살해 인정)됨",
          appA.core.night_kill_agree() == ai_names[1])
    with appA.core.lock:
        appA.core.players["이팀장B"]["role"] = "citizen"
        appA.core.players[ai_names[1]]["role"] = "mafia"
        appA.core.night_targets.clear()
        appA.core.night_targets[ai_names[0]] = a_nm
        appA.core.night_targets[ai_names[1]] = ai_names[2]
    appA._reconcile_mafia_night()
    check("공모: AI 마피아끼리도 한 명으로 통일되어 합의됨",
          appA.core.night_kill_agree() in (a_nm, ai_names[2]))
    with appA.core.lock:   # 이후 시나리오 원복
        appA.core.players[ai_names[1]]["role"] = "citizen"
        appA.core.night_targets.clear()

    # ===================== 마피아 전용 비밀방(자동 생성) =====================
    with appA.core.lock:
        appA.core.players["이팀장B"]["role"] = "mafia"
        appA.core.players[a_nm]["role"] = "mafia"
        appA.core.phase = Phase.NIGHT
    stubB.core.players["이팀장B"]["role"] = "mafia"
    stubB.core.players[a_nm]["role"] = "mafia"
    stubB.core.phase = Phase.NIGHT
    appA._maybe_open_mafia_room()
    stubB._maybe_open_mafia_room()
    check("비밀방: 사람 마피아 둘 다 밤에 비밀방이 자동으로 생성됨",
          appA._mafia_room is not None and stubB._mafia_room is not None)
    stubB._mafia_room_ent.insert(0, "비밀작전 하나")
    stubB._mafia_room_send()
    check("비밀방: 동료(A)의 비밀방으로 전달됨",
          pump(lambda: ("이팀장B", "비밀작전 하나") in getattr(appA, "_mafia_room_history", []), timeout=10))
    check("비밀방: 공개 게임방 채팅에는 노출되지 않음",
          not any("비밀작전 하나" in r.get("text", "") for r in appA.mafia_history))
    with appA.core.lock:
        appA.core.players[a_nm]["role"] = "citizen"
    stubB._mafia_room_ent.insert(0, "비밀작전 둘")
    stubB._mafia_room_send()
    pump(lambda: False, timeout=1.0)
    check("비밀방: 마피아가 아니면(시민) 내용을 받지 않음",
          ("이팀장B", "비밀작전 둘") not in getattr(appA, "_mafia_room_history", []))
    stubB._mafia_room_close(keep_reopen=True)
    check("비밀방: ✕로 닫으면 다시 열 수 있는 작은 버튼이 남음",
          stubB._mafia_room is None and stubB._mafia_room_mini is not None)
    stubB._mafia_room_open()
    check("비밀방: 재오픈하면 작은 버튼은 사라지고 방이 다시 열림",
          stubB._mafia_room is not None and stubB._mafia_room_mini is None)
    with appA.core.lock:
        appA.core.players[a_nm]["alive"] = False
    stubB.core.players[a_nm]["alive"] = False
    check("비밀방: 동료가 전멸(사망)하면 비밀방이 필요 없다고 판단함",
          not stubB._mafia_room_wanted() and stubB._mafia_team_names("이팀장B") == [])
    with appA.core.lock:
        appA.core.players[a_nm]["alive"] = True
    stubB.core.players[a_nm]["alive"] = True
    appA._mafia_room_close(); stubB._mafia_room_close()
    check("비밀방: 닫기 후 정리됨", appA._mafia_room is None and stubB._mafia_room is None)
    with appA.core.lock:
        appA.core.players["이팀장B"]["role"] = "mafia"
        appA.core.phase = Phase.DAY

    # ===================== ⑤ 찬반(처형여부) 투표 동기화 =====================
    defendant = ai_names[2]
    with appA.core.lock:
        appA.core.phase = Phase.DAY
        appA.core.defendant = defendant
        appA.core.defense_yes = {}
    stubB.core.phase = Phase.DAY
    stubB.core.defendant = defendant

    stubB._cast_defense(defendant, True)
    check("⑤ A(호스트)의 core.defense_yes에 B의 실제 찬반 표가 반영됨",
          pump(lambda: appA.core.defense_yes.get("이팀장B") is True, timeout=10))

    # ===================== 동료 마피아 통보(마피아에게만) =====================
    mate = ai_names[0]
    appA._mafia_send_private("이팀장B", "hdm", target="이팀장B", text="🃏 테스트 '마피아'", role="mafia", mates=[mate])
    check("동료: B의 core에 동료 마피아 역할이 반영됨",
          pump(lambda: stubB.core.players.get(mate, {}).get("role") == "mafia", timeout=10))
    check("동료: B의 core.mafias()에 동료가 포함되어 밤 후보에서 제외 가능",
          mate in stubB.core.mafias())
    pump(lambda: False, timeout=0.5)
    try:
        stubB._mafia_overlay_close()
    except Exception:
        pass
    civ = next(n for n, p in appA.core.players.items() if p.get("role") not in ("mafia",) and n not in ("이팀장B",) and n != mate)
    # 마피아가 아닌 사람에게 가는 쪽지에는 mates가 없어야 한다(호스트 코드 경로 확인)
    import inspect, mafia_ui
    src = inspect.getsource(mafia_ui.MafiaUIMixin._launch_game_with_recruits)
    check("동료: 호스트가 마피아 직업에게만 mates를 붙이는 경로임", 'if prole == "mafia"' in src)

    # ===================== 찬반 개시 / 처형 판결 동기화 =====================
    appA._mafia_broadcast("defense_vote_open", name=defendant)
    check("defense_vote_open: 원격(B) 화면에 찬반 팝업이 열림",
          pump(lambda: stubB._mafia_overlay is not None, timeout=10))
    stubB._mafia_overlay_close()
    appA._mafia_broadcast("verdict", result="executed", name=defendant, role="citizen", yes=2, no=1)
    check("verdict: 처형된 사람이 원격 core에서도 사망 처리됨",
          pump(lambda: stubB.core.players[defendant]["alive"] is False, timeout=10))
    pump(lambda: False, timeout=0.3)

    # ===================== 게임 종료(end) → 원격 상태 로비 복귀 =====================
    appA._mafia_broadcast("end", winner="citizen",
                          roles={n: p.get("role") for n, p in appA.core.players.items()})
    check("end: 원격(B)의 mafia_active가 꺼지고 core가 로비로 복귀함",
          pump(lambda: stubB.mafia_active is False and stubB.core.phase == Phase.LOBBY, timeout=10))

    # ===================== ⑥ 접속 끊김 → 자동 사망 처리 =====================
    with appA.core.lock:
        appA.core.defendant = None
    appA._mafia_disconnected = set()

    try:
        stubB.engine.stop()
    except Exception:
        pass

    check("⑥ A가 B의 접속 끊김을 감지해 사망 처리함",
          pump(lambda: appA.core.players.get("이팀장B", {}).get("alive") is False, timeout=20))
    check("⑥ 사망 처리 사실이 시스템 메시지로 안내됨",
          any("이팀장B" in t and "사망 처리" in t for t in A_sys))
    leak = [t for t in A_sys if "이팀장B" in t and "끊긴 것 같습니다" in t
            and any(r in t for r in ("마피아", "의사", "경찰", "시민"))]
    check("⑥ 끊김/사망 안내가 역할 정보를 노출하지 않음", len(leak) == 0)

finally:
    try:
        appA._cancel_mafia_timer()
    except Exception:
        pass
    try:
        stubB.engine.stop()
    except Exception:
        pass
    try:
        appA.engine.stop()
    except Exception:
        pass
    try:
        rootB.destroy()
    except Exception:
        pass
    try:
        rootA.destroy()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)

print("MULTIPLAYER SYNC", "PASSED" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
