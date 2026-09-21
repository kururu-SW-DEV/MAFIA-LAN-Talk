# -*- coding: utf-8 -*-
"""test_remote_human_full_round.py — 원격 사람 참가자(B)가 한 판을 '실제 흐름'으로 끝까지 하는지.

test_multiplayer_sync는 개표·재투표를 스텁으로 막고 상태를 손으로 세팅해 부분만 확인한다. 여기서는
호스트(A)의 실제 개표 → 최후 변론 → 찬반 투표 → 판결 → 밤(경찰/의사/마피아 행동) 흐름을 그대로 태우고,
그때 원격 참가자 B의 화면에 무엇이 뜨는지, B의 선택이 A에 반영되는지를 확인한다.
(네트워크 하니스는 test_multiplayer_sync.py의 앞부분을 그대로 재사용한다.)
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_src = open(os.path.join(_HERE, "test_multiplayer_sync.py"), encoding="utf-8").read()
_head = _src[:_src.index('try:\n    check("A가 B를 발견"')]
_head = (_head.replace("A_PORT, B_PORT = 60031, 60032", "A_PORT, B_PORT = 60033, 60034")
         .replace("tmp_multiplayer_sync", "tmp_remote_full_round"))
exec(compile(_head, os.path.join(_HERE, "test_multiplayer_sync.py"), "exec"), globals())

from mafia_net import encode


def b_sees(fragment):
    return any(fragment in (r.get("text") or "") for r in stubB.mafia_history)


try:
    check("A가 B를 발견", pump(lambda: len(appA.engine.peers) >= 1, timeout=10))
    check("B가 A를 발견", pump(lambda: len(stubB.engine.peers) >= 1, timeout=10))
    appA._select(("mgame",))
    a_nm, b_nm = "김재무A", "이팀장B"

    # ---- 모집 → 참가 → 시작 ----
    appA.mafia_start_clicked()
    check("B가 모집 개시를 수신", pump(lambda: stubB._recruiting is True))
    stubB.mafia_toggle_join_clicked()
    check("A가 B의 참가 신청을 수신", pump(lambda: b_nm in appA._recruited_humans))
    appA.mafia_ai_count = 3
    appA.mafia_start_clicked()
    check("게임 시작 + B의 명단 동기화", pump(lambda: appA.mafia_active and b_nm in stubB.core.players
                                        and len(stubB.core.players) >= 5, timeout=20))
    check("B가 직업을 통보받음", pump(lambda: stubB._my_mafia_role is not None, timeout=10))
    check("B가 호스트가 준 비밀 토큰을 받음(이게 없으면 B의 투표·밤 행동이 호스트에서 버려진다)",
          bool(getattr(stubB, "_my_tok", None)))
    for o in (appA, stubB):
        try:
            o._mafia_overlay_close()
        except Exception:
            pass
    ai_names = [n for n, p in appA.core.players.items() if p.get("is_ai")]
    # 역할을 고정한다(무작위면 처형 한 번으로 판이 끝나 뒤 단계를 못 본다): 처형 대상 ai0=시민, ai1=마피아
    for nm, rl in ((a_nm, "citizen"), (b_nm, "citizen"), (ai_names[0], "citizen"),
                   (ai_names[1], "mafia"), (ai_names[2], "citizen")):
        appA.core.players[nm]["role"] = rl
    stubB.core.players[b_nm]["role"] = "citizen"; stubB._my_mafia_role = "citizen"

    # ============ 낮 투표: 실제 개표까지 (스텁 없음) ============
    for pl in appA.ai.players:
        pl.alive, pl.booted = True, True
    appA.core.phase = Phase.DAY
    appA.open_the_vote()
    check("B 화면에 투표 팝업이 뜸", pump(lambda: getattr(stubB, "_vote_lbl", None) is not None, timeout=10))
    target = ai_names[0]
    stubB._popup_vote(target)
    check("B의 표가 호스트 core에 반영됨(토큰 검증 통과)",
          pump(lambda: appA.core.votes.get(b_nm) == target, timeout=10))
    check("호스트 화면에 'B님 투표 접수' 안내가 뜸", any(b_nm in t and "투표 접수" in t for t in A_sys))
    check("B 화면에 '방장이 내 투표를 접수했습니다' 확인이 돌아옴(표가 실제로 집계됐다는 뜻)",
          pump(lambda: b_sees("방장이 내 투표를 접수했습니다"), timeout=10))
    check("접수 확인이 오면 대기 중이던 재전송/경고는 해제됨", "vote_cast" not in getattr(stubB, "_ack_pending", {}))
    appA._popup_vote(target)                                  # 호스트 사용자도 같은 대상에게
    check("원격 B 화면에도 AI들의 투표 진행이 표시됨(예전엔 호스트 화면에만 떴다)",
          pump(lambda: any("(AI)님 투표 완료" in (r.get("text") or "") for r in stubB.mafia_history), timeout=30))
    check("원격 B 화면에 호스트 사용자(A)의 투표 진행도 표시됨",
          pump(lambda: b_sees(f"{a_nm}님 투표 완료"), timeout=10))
    check("전원(사람2+AI3) 투표 완료 → 실제 개표가 실행돼 최후 변론으로 넘어감",
          pump(lambda: appA.core.defendant == target or any("최후 변론" in t for t in A_sys), timeout=40))
    check("B 화면에도 변론 시작이 전달됨(defendant 동기화)",
          pump(lambda: stubB.core.defendant == target, timeout=10))
    check("B의 변론 진행 표시가 켜지고 입력이 잠김(피고인이 아니므로)",
          getattr(stubB, "_defense_in_progress", False) is True and str(getattr(stubB.__dict__.get("entry", None), "cget", lambda k: "n/a")("state")) in ("disabled", "n/a"))

    # ============ 찬반(처형여부) 투표: 변론 60초를 건너뛰고 실제 찬반 단계로 ============
    appA._start_defense_votes(target)
    check("B 화면에 찬반(처형여부) 팝업이 뜸", pump(lambda: getattr(stubB, "_defense_vote_lbl", None) is not None
                                              or getattr(stubB, "_mafia_overlay", None) is not None, timeout=10))
    stubB._cast_defense(target, True)
    check("B의 찬반 표가 호스트에 반영됨", pump(lambda: appA.core.defense_yes.get(b_nm) is True, timeout=10))
    check("B 화면에 '방장이 내 찬반 표를 접수했습니다' 확인이 돌아옴",
          pump(lambda: b_sees("방장이 내 찬반 표를 접수했습니다"), timeout=10))
    check("호스트 화면에 'B님 찬반 표 접수' 안내가 뜸", any(b_nm in t and "찬반" in t for t in A_sys))
    check("원격 B 화면에도 AI들의 찬반 표 진행이 표시됨",
          pump(lambda: any("찬반 표 접수" in (r.get("text") or "") and "(AI)님" in (r.get("text") or "")
                           for r in stubB.mafia_history), timeout=20))
    appA._cast_defense(target, True) if hasattr(appA, "_cast_defense") else None
    check("찬반이 끝나 판결이 나고 B 화면에 판결이 전달됨",
          pump(lambda: stubB.core.defendant is None and (b_sees("처형") or b_sees("판결") or
                                                         any(k in " ".join(x.get("text", "") for x in stubB.mafia_history)
                                                             for k in ("처형", "무죄", "부결", "가결"))), timeout=40))

    # ============ 밤: 원격 사람의 경찰/의사/마피아 행동 ============
    check("밤이 시작되고 B에게 전달됨", pump(lambda: appA.core.phase == Phase.NIGHT and stubB.core.phase == Phase.NIGHT, timeout=40))
    others = [n for n in ai_names if appA.core.players[n].get("alive", True)]
    ai_target = ai_names[2]                       # 살아 있는 시민 AI(마피아 동료가 아니라 마피아 B도 지목 가능)
    results = {}
    for role in ("police", "doctor", "mafia"):
        appA.core.players[b_nm]["role"] = role
        stubB.core.players[b_nm]["role"] = role
        stubB._my_mafia_role = role
        appA.core.players[b_nm]["alive"] = True
        appA.core.phase = Phase.NIGHT
        appA.core.police_invest.clear(); appA.core.police_report = None
        appA.core.night_saved = None; appA.core.night_targets.clear()
        appA.core.last_saved = None if hasattr(appA.core, "last_saved") else None
        before = len(stubB.mafia_history)
        # B의 밤 행동 패널이 뜨는지(원격 참가자 화면)
        try:
            stubB._mafia_overlay_close()
        except Exception:
            pass
        stubB._show_night_panel()
        panel_open = getattr(stubB, "_mafia_overlay", None) is not None
        results[role] = panel_open
        try:
            stubB._mafia_overlay_close()
        except Exception:
            pass
        stubB._mafia_send_to_host("night_action", actor=b_nm, role=role, target=ai_target)
        if role == "police":
            ok = pump(lambda: len(appA.core.police_invest) == 1, timeout=10)
            check("경찰 B: 조사 대상이 호스트에 접수됨", ok)
            check("경찰 B: 조사 결과가 B에게만 쪽지로 돌아옴",
                  pump(lambda: any("조사" in (r.get("text") or "") for r in stubB.mafia_history[before:]), timeout=10))
        elif role == "doctor":
            check("의사 B: 치료 대상이 호스트에 접수됨", pump(lambda: appA.core.night_saved == ai_target, timeout=10))
        else:
            check("마피아 B: 살해 대상이 호스트에 접수됨",
                  pump(lambda: appA.core.night_targets.get(b_nm) == ai_target
                       if isinstance(appA.core.night_targets, dict) else ai_target in appA.core.night_targets, timeout=10))
    for role, opened in results.items():
        check(f"{role} B: 원격 화면에 밤 행동 패널이 열림", opened)

    # ============ 호스트가 표를 버리는 경우(버전 불일치·인증 실패 등): 투표자에게 경고가 떠야 한다 ============
    stubB._ACK_WAIT_MS = 300
    _orig_recv = appA._host_receive_vote_cast
    appA._host_receive_vote_cast = lambda *a, **k: None         # 호스트가 아무 응답 없이 표를 버림
    before_n = len(stubB.mafia_history)
    stubB._mafia_send_to_host("vote_cast", voter=b_nm, target=ai_names[2])
    check("호스트가 표를 접수하지 않으면 투표자(B) 화면에 경고가 뜸(성공한 줄 착각 방지)",
          pump(lambda: any("접수했다는 확인이 없습니다" in (r.get("text") or "") for r in stubB.mafia_history[before_n:]), timeout=10))
    appA._host_receive_vote_cast = _orig_recv

finally:
    try:
        appA._cancel_mafia_timer()
    except Exception:
        pass
    for fn in (lambda: stubB.engine.stop(), lambda: appA.engine.stop(),
               lambda: appA._notifier.close(), lambda: rootB.destroy(), lambda: rootA.destroy()):
        try:
            fn()
        except Exception:
            pass
    shutil.rmtree(tmp, ignore_errors=True)

print("REMOTE HUMAN FULL ROUND", "PASSED" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
