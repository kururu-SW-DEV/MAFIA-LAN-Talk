# -*- coding: utf-8 -*-
"""test_v167_fixes.py — 4차 독립 평가 지적 반영(v1.67) 회귀 테스트. GUI/네트워크 없이 로직만 확인한다."""
import json
import os
import sys
import tempfile
from types import SimpleNamespace

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import mafia_ai
import mafia_config
from mafia_core import GameCore, Phase

ok_all = True


def check(label, cond):
    global ok_all
    r = bool(cond)
    print(("OK  " if r else "FAIL"), label)
    if not r:
        ok_all = False


def _core(names=("A", "B", "C", "D", "E"), mafia=("A",)):
    c = GameCore("t")
    for n in names:
        c.join(n, False)
    for n in names:
        c.players[n]["role"] = "mafia" if n in mafia else "citizen"
    return c


# ---- 4) 사망자·빈 표는 개표에서 제외 ----
c = _core()
c.phase = Phase.DAY
c.votes = {"A": "D", "B": "D", "C": "E"}
c.players["D"]["alive"] = False
mode, data = c.tally_votes_full()
check("재투표 중 죽은 사람에게 간 표는 개표에서 빠지고 산 사람이 최다득표가 됨",
      mode == "single" and data["top"] == "E")
c2 = _core()
c2.votes = {"A": "", "B": ""}
check("빈 문자열 표는 집계하지 않음", c2.tally_votes_full()[0] == "none")
c3 = _core()
c3.votes = {"A": "D", "B": "D"}
c3.players["D"]["alive"] = False
check("표가 전부 사망자에게 갔으면 '무득표' 처리(처형 없음)", c3.tally_votes_full()[0] == "none")

# ---- 8) 마피아 동료 지목 금지: core 단독으로도 ----
c4 = _core(mafia=("A", "B"))
check("mafia_night_vote가 마피아 동료 지목을 거부", c4.mafia_night_vote("A", "B") is False)
check("mafia_night_vote가 시민 지목은 허용", c4.mafia_night_vote("A", "C") is True)

# ---- 6) 마피아 AI의 공개 발언에서 비밀 누설 문장 제거 ----
s = mafia_ai.strip_secret_leaks
check("마피아 AI: '우리 마피아 팀' 문장은 지우고 나머지는 남김",
      s("미나가 좀 수상해요. 우리 마피아 팀은 괜찮아요.", "mafia") == "미나가 좀 수상해요.")
check("마피아 AI: 비밀 대화 언급 문장 제거", "비밀" not in s("아까 비밀 대화에서 말했잖아. 일단 투표하자.", "mafia"))
check("마피아 AI: 전부 누설이면 빈 문자열(발언 취소)", s("저 마피아예요.", "mafia") == "")
check("시민 AI의 '저 마피아 아니에요'는 그대로", s("저 마피아 아니에요.", "citizen") == "저 마피아 아니에요.")
check("평범한 문장은 마피아 AI라도 그대로", s("오늘은 좀 조용하네요~", "mafia") == "오늘은 좀 조용하네요~")

# ---- 1) API 키는 평문으로 저장하지 않음 ----
_tmp = tempfile.mkdtemp()
_old_file, _old_rt = mafia_config._OVERRIDES_FILE, dict(mafia_config.RUNTIME_OVERRIDES)
try:
    mafia_config._OVERRIDES_FILE = os.path.join(_tmp, "mafia_llm.json")
    mafia_config.RUNTIME_OVERRIDES.clear()
    mafia_config.RUNTIME_OVERRIDES.update({"base_url": "http://x", "model": "m", "api_key": "sk-SECRET-123"})
    check("설정 저장 성공", mafia_config.save_overrides() is True)
    raw = open(mafia_config._OVERRIDES_FILE, encoding="utf-8").read()
    if sys.platform == "win32":
        check("저장 파일에 API 키 평문이 없음", "sk-SECRET-123" not in raw and "api_key_dpapi" in raw)
        mafia_config.RUNTIME_OVERRIDES.clear()
        mafia_config.load_overrides()
        check("다시 읽으면 키가 복원됨", mafia_config.RUNTIME_OVERRIDES.get("api_key") == "sk-SECRET-123")
    # 예전 평문 파일도 읽을 수 있어야 함(호환)
    with open(mafia_config._OVERRIDES_FILE, "w", encoding="utf-8") as f:
        json.dump({"base_url": "http://x", "model": "m", "api_key": "legacy-key"}, f)
    mafia_config.RUNTIME_OVERRIDES.clear()
    mafia_config.load_overrides()
    check("예전 평문 설정 파일도 그대로 읽힘", mafia_config.RUNTIME_OVERRIDES.get("api_key") == "legacy-key")
finally:
    mafia_config._OVERRIDES_FILE = _old_file
    mafia_config.RUNTIME_OVERRIDES.clear()
    mafia_config.RUNTIME_OVERRIDES.update(_old_rt)

# ---- 2) 참가자 토큰: 투표·밤 행동은 호스트가 개인 쪽지로 준 토큰이 있어야 받음 ----
from mafia_ui import MafiaUIMixin


class _TokStub(MafiaUIMixin):
    def __init__(self, is_host=True):
        self.engine = SimpleNamespace(name="호스트")
        self._recruiter_host = "호스트"
        self.mafia_active = True
        self._is_host = is_host
        self.peers = {}
        self.sent = []

    def _mafia_is_host(self):
        return self._is_host

    def _mafia_peer_of(self, name):
        return self.peers.get(name)

    def _mafia_peer_raw(self, name):
        return self.peers.get(name)

    def _mafia_send_private(self, to, t, **kw):
        self.sent.append((to, t, kw))

    def add_mafia_host_dm(self, *a, **k):
        pass


h = _TokStub()
h._mafia_tokens = {"철수": "abcd1234"}
for t, f in (("vote_cast", "voter"), ("defense_vote_cast", "voter"), ("night_action", "actor")):
    check(f"{t}: 토큰이 맞으면 통과", h._proto_authorized(t, {f: "철수", "tok": "abcd1234"}, "철수"))
    check(f"{t}: 토큰이 없으면 차단(이름·포트만 흉내 낸 위조)", not h._proto_authorized(t, {f: "철수"}, "철수"))
    check(f"{t}: 토큰이 틀리면 차단", not h._proto_authorized(t, {f: "철수", "tok": "zzzz"}, "철수"))
check("토큰이 발급되지 않은 이름은 예전처럼 통과(구버전 호환)",
      h._proto_authorized("vote_cast", {"voter": "영희"}, "영희"))
check("채팅(user_say)은 토큰을 요구하지 않음", h._proto_authorized("user_say", {"name": "철수"}, "철수"))

cl = _TokStub(is_host=False)
cl.engine = SimpleNamespace(name="철수")
cl._my_tok = "abcd1234"
cl._mafia_send_to_host("vote_cast", voter="철수", target="영희")
cl._mafia_send_to_host("user_say", name="철수", text="hi")
check("참가자는 투표·밤 행동 전송에 토큰을 실음",
      cl.sent[0][2].get("tok") == "abcd1234" and "tok" not in cl.sent[1][2])

# ---- 7) '@이름'은 투표가 아니라 멘션 ----
_src = open(os.path.join(ROOT, "mafia_ui_ai.py"), encoding="utf-8").read()
check("낮 채팅의 투표 정규식에 '@이름' 대안이 없음", 're.match(r"^@([^\\s]+)\\s*$"' not in _src)

# ---- 5) 원격 밤 행동은 호스트 접수 회신 전까지 패널을 닫지 않음 ----
_night = open(os.path.join(ROOT, "mafia_ui_night.py"), encoding="utf-8").read()
_i = _night.index("def _apply_night_pick")
_j = _night.index("def _night_action_apply")
check("원격 참가자의 _apply_night_pick이 전송 직후 패널을 닫지 않음",
      "self._mafia_overlay_close()" not in _night[_night.index("else:", _i):_j].split("def _close_night_panel_on_ack")[0])


class _AckStub(MafiaUIMixin):
    def __init__(self):
        self._mafia_overlay = object()
        self.closed = 0

    def _mafia_overlay_close(self):
        self.closed += 1
        self._mafia_overlay = None


a = _AckStub()
a._close_night_panel_on_ack("⚠ (의사) X은(는) 어제 밤에 이미 보호했습니다")
check("호스트가 거절(⚠)하면 패널이 유지돼 다른 대상을 다시 고를 수 있음", a.closed == 0)
a._close_night_panel_on_ack("💉 (의사 신청) X 구조 지시 접수")
check("호스트가 접수하면 패널을 닫음", a.closed == 1)

print("V167 FIXES PASSED" if ok_all else "V167 FIXES FAILED")
sys.exit(0 if ok_all else 1)
