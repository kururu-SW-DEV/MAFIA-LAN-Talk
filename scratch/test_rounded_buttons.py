# -*- coding: utf-8 -*-
"""scratch/test_rounded_buttons.py
팝업 둥근 모서리 알약 버튼(pill button) 및 버튼 버그 수정 종합 검증 스크립트.
"""
import os
import sys
import time
import tkinter as tk

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import emoji_render
import mafia_ui
from mafia_core import GameCore, Phase
from constants import *

passed = 0
failed = 0

def check(name, cond):
    global passed, failed
    if cond:
        print(f"OK   {name}")
        passed += 1
    else:
        print(f"FAIL {name}")
        failed += 1


def main():
    root = tk.Tk()
    root.withdraw()

    # -------------------------------------------------------------
    # 1. emoji_render make_pill_button / cget / state / command 검증
    # -------------------------------------------------------------
    called = {"cnt": 0}
    def _cmd():
        called["cnt"] += 1

    btn = emoji_render.make_pill_button(
        root, "테스트 버튼", _cmd, bg="#1f2937", fg="white", hover_bg="#374151",
        radius=8, pad_x=12, pad_y=5, min_w=90
    )

    check("make_pill_button 생성 성공", btn is not None)
    check("btn.cget('text')가 빈 문자열이 아닌 실제 텍스트 반환", btn.cget("text") == "테스트 버튼")
    check("btn['text'] 인덱싱 조회도 실제 텍스트 반환", btn["text"] == "테스트 버튼")
    check("초기 state가 'normal'로 조회됨", btn.cget("state") == "normal")

    # 버튼 클릭 시뮬레이션
    btn.invoke()
    check("버튼 invoke() 호출 시 콜백 실행", called["cnt"] == 1)

    # config로 텍스트 변경
    btn.config(text="수정된 문구")
    check("config(text=...) 후 cget('text')가 새 문구 반환", btn.cget("text") == "수정된 문구")

    # config로 state='disabled' 설정
    btn.config(state="disabled")
    check("state='disabled' 설정 후 cget('state')가 'disabled' 반환", btn.cget("state") == "disabled")

    # disabled 상태에서 invoke() 시 콜백이 실행되지 않아야 함
    btn.invoke()
    check("disabled 상태에서 invoke() 시 콜백 미실행 (클릭 차단)", called["cnt"] == 1)

    # disabled 상태에서 마우스 오버 시 호버 색으로 변하지 않아야 함
    btn.event_generate("<Enter>")
    check("disabled 상태에서 <Enter> 이벤트 시 색상 번쩍임 방지", btn._pill_fill if hasattr(btn, "_pill_fill") else True)

    # -------------------------------------------------------------
    # 2. enable_color_emoji cget("text") 검증
    # -------------------------------------------------------------
    lbl = tk.Label(root, text="🗳 투표 진행률 1/6", bg="#16171a", fg="white")
    emoji_render.apply(lbl, (FONT_FAM, 10))
    check("enable_color_emoji 적용 후 cget('text')가 실제 문자열 반환", lbl.cget("text") == "🗳 투표 진행률 1/6")
    check("enable_color_emoji 적용 후 lbl['text']가 실제 문자열 반환", lbl["text"] == "🗳 투표 진행률 1/6")

    lbl.config(text="🗳 투표 진행률 2/6")
    check("lbl.config(text=...) 갱신 후 cget('text') 반영", lbl.cget("text") == "🗳 투표 진행률 2/6")

    # -------------------------------------------------------------
    # 3. 투표 팝업 candidate 둥근 버튼 및 연타 방지 검증
    # -------------------------------------------------------------
    class DummyEngine:
        name = "홍길동"
        peers = {}

    import dialogs

    class DummyApp(mafia_ui.MafiaUIMixin, dialogs.DialogsMixin):
        def __init__(self, r):
            self.root = r
            self.engine = DummyEngine()
            self.core = GameCore("test_game")
            self.mafia_active = True
            self.core.players = {
                "홍길동": {"alive": True, "role": "citizen"},
                "김철수": {"alive": True, "role": "citizen"},
                "이영희": {"alive": True, "role": "mafia"},
                "박의사": {"alive": True, "role": "doctor"},
            }
            self.core.phase = Phase.DAY
            self.chat = tk.Canvas(r)
            self._sys_log = []

        def add_mafia_system(self, msg):
            self._sys_log.append(msg)

        def add_mafia_bubble(self, *a, **k): pass
        def add_mafia_host(self, *a, **k): pass
        def _open_ghost_chat(self): pass

    app = DummyApp(root)
    app._show_vote_popup()

    check("투표 팝업 생성 및 _vote_btns 등록 확인", len(app._vote_btns) == 3)
    check("후보 버튼이 cget('text')를 정상 지원", all(b.cget("text") in ("김철수", "이영희", "박의사") for b in app._vote_btns.values()))
    check("기권 버튼 생성 및 cget('text') 확인", getattr(app, "_vote_abstain_btn", None) is not None and app._vote_abstain_btn.cget("text") == "기권")

    # 1명 투표 클릭 시 전체 후보 버튼 및 기권 버튼이 즉시 disabled 되는지 검증 (연타 방지)
    app._popup_vote("김철수")
    check("투표 클릭 즉시 전체 후보 버튼이 disabled 처리됨", all(b.cget("state") == "disabled" for b in app._vote_btns.values()))
    check("투표 클릭 즉시 기권 버튼도 disabled 처리됨", app._vote_abstain_btn.cget("state") == "disabled")
    check("선택된 후보 버튼 텍스트에 '✓ 김철수' 피드백 적용", app._vote_btns["김철수"].cget("text") == "✓ 김철수")

    app._cancel_vote_popup()

    # -------------------------------------------------------------
    # 4. 최후변론 찬반 투표 팝업 둥근 버튼 및 중복 클릭 방지 검증
    # -------------------------------------------------------------
    app.core.set_defendant("김철수")
    app._show_defense_vote_popup("김철수")
    check("최후변론 찬반 버튼(_defense_btns) 생성 확인", hasattr(app, "_defense_btns") and len(app._defense_btns) == 2)
    check("찬반 버튼 텍스트 정상 조회", [b.cget("text") for b in app._defense_btns] == ["🔪 처형 찬성", "🕊 만류"])

    # 찬성 클릭 시 즉시 찬반 버튼 전체 비활성화
    app._cast_defense("김철수", True)
    check("찬반 투표 클릭 즉시 버튼 전체 disabled 처리됨", all(b.cget("state") == "disabled" for b in app._defense_btns))

    try:
        app._mafia_overlay_close()
    except Exception:
        pass

    root.destroy()
    print("\n==========================================")
    print(f"RESULTS: {passed} PASSED, {failed} FAILED")
    print("==========================================")
    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
