# -*- coding: utf-8 -*-
"""test_report_fixes.py — 외부 보고서 검증 후 수정한 항목(인격 파라미터, 빈 API 키 로그)의 회귀 테스트.

GUI/네트워크 없이 순수 로직만 확인한다.
"""
import io
import os
import sys
import urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import mafia_ai
import mafia_config
from mafia_ai import AIDirector

ok_all = True


def check(label, cond):
    global ok_all
    r = bool(cond)
    print(("OK  " if r else "FAIL"), label)
    if not r:
        ok_all = False


# ---- 20종 인격의 발언 빈도/공격성이 AI에게 실제로 전달된다 ----
by_name = {p["name"]: p for p in mafia_config.ALL_PERSONAS}
check("인격 풀에 PERSONAS20의 freq/att가 그대로 담김",
      all(by_name[x["name"]].get("freq") == x["freq"] and by_name[x["name"]].get("att") == x["att"]
          for x in mafia_config.PERSONAS20))

d = AIDirector()
d.players = []
# spawn_all은 부트스트랩(LLM 호출)까지 하므로 PlayerAgent 생성 부분만 같은 방식으로 재현
pers = [by_name["그림자"], by_name["돌직구"]]
from mafia_ai import PlayerAgent
agents = [PlayerAgent(p["name"], p["persona"], p["color"],
                      freq=p.get("freq", 50), att=p.get("att", 50)) for p in pers]
check("조용한 '그림자'는 발언 빈도가 낮고 '돌직구'는 높다", agents[0].freq < 50 < agents[1].freq)
check("'그림자'는 공격성이 낮고 '돌직구'는 상대적으로 높다", agents[0].att < agents[1].att)

# ---- 빈 API 키일 때 에러 본문 로그가 왜곡되지 않는다 ----
captured = {}
mafia_ai.applog.log = lambda tag, exc=None, detail="": captured.update(detail=detail)
mafia_ai.get_llm_api_key = lambda: ""
mafia_config.RUNTIME_OVERRIDES.update({"base_url": "http://127.0.0.1:9", "model": "m"})


def _boom(req, timeout=0):
    raise urllib.error.HTTPError("http://x", 500, "err", {}, io.BytesIO("server said boom".encode("utf-8")))


mafia_ai.urllib.request.urlopen = _boom
res = mafia_ai._llm_call([{"role": "user", "content": "hi"}])
check("LLM 호출 실패는 None 반환", res is None)
check("빈 API 키여도 에러 본문이 글자 사이에 별표 없이 그대로 기록됨",
      "body=server said boom" in captured.get("detail", "") and "***" not in captured.get("detail", ""))

# 키가 있으면 본문에 섞인 키는 가려진다
mafia_ai.get_llm_api_key = lambda: "SECRETKEY"


def _boom2(req, timeout=0):
    raise urllib.error.HTTPError("http://x", 401, "err", {}, io.BytesIO("bad key SECRETKEY here".encode("utf-8")))


mafia_ai.urllib.request.urlopen = _boom2
mafia_ai._llm_call([{"role": "user", "content": "hi"}])
check("API 키가 있으면 에러 본문 속 키는 *** 로 가려짐",
      "SECRETKEY" not in captured.get("detail", "") and "bad key *** here" in captured.get("detail", ""))

# ---- 응답이 토큰 한도로 잘렸을 때(finish_reason=length) 재요청·문장 단위 정리 ----
import json


class _Resp:
    def __init__(self, content, finish):
        self._b = json.dumps({"choices": [{"message": {"content": content},
                                           "finish_reason": finish}]}).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


sent = []


def _install(script):
    """script: 호출 순서대로 (content, finish) 또는 HTTPError 코드(int)."""
    it = iter(script)
    sent.clear()

    def _fake(req, timeout=0):
        body = json.loads(req.data.decode("utf-8"))
        sent.append(body)
        item = next(it)
        if isinstance(item, int):
            raise urllib.error.HTTPError(req.full_url, item, "err", {}, io.BytesIO(b"{}"))
        return _Resp(*item)
    mafia_ai.urllib.request.urlopen = _fake


mafia_config.RUNTIME_OVERRIDES.update({"base_url": "http://127.0.0.1:9", "model": "m"})
mafia_ai.get_llm_api_key = lambda: "k"
msgs = [{"role": "user", "content": "hi"}]

_install([("정상 답변이에요~", "stop")])
check("정상 응답은 한 번만 호출하고 그대로 반환", mafia_ai._llm_call(msgs, max_tokens=100) == "정상 답변이에요~" and len(sent) == 1)

_install([("그래서 내가 말하려던 건 사실", "length"), ("그래서 내가 말하려던 건 사실 너 수상하다는 거야ㅋㅋ", "stop")])
res = mafia_ai._llm_call(msgs, max_tokens=100)
check("잘린 응답(length)은 토큰 한도를 4배로 올려 재요청해 온전한 문장을 받음",
      res == "그래서 내가 말하려던 건 사실 너 수상하다는 거야ㅋㅋ" and len(sent) == 2
      and sent[0]["max_tokens"] == 100 and sent[1]["max_tokens"] == 400)

_install([("첫째 문장이에요. 둘째 문장은 말하다가 잘", "length"),
          ("첫째 문장이에요. 둘째 문장은 말하다가 또 잘", "length")])
check("재요청도 잘리면 마지막 완결 문장까지만 남김",
      mafia_ai._llm_call(msgs, max_tokens=100) == "첫째 문장이에요.")

_install([("", "length"), ("생각 끝나고 나온 답이에요.", "stop")])
check("생각에 토큰을 다 써서 빈 응답이 오면 재요청으로 답을 받음",
      mafia_ai._llm_call(msgs, max_tokens=100) == "생각 끝나고 나온 답이에요.")

# ---- Gemini 주소일 때만 reasoning_effort를 붙이고, 모델이 거절(400)하면 빼고 재시도 ----
mafia_config.RUNTIME_OVERRIDES["base_url"] = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
_install([("ok 답변", "stop")])
mafia_ai._llm_call(msgs, max_tokens=100)
check("Gemini 주소에는 reasoning_effort=low를 붙임", sent[0].get("reasoning_effort") == "low")

_install([400, ("생각 옵션 없이 성공", "stop")])
res = mafia_ai._llm_call(msgs, max_tokens=100)
check("Gemini 모델이 reasoning_effort를 거절(400)하면 빼고 다시 시도해 성공",
      res == "생각 옵션 없이 성공" and "reasoning_effort" in sent[0] and "reasoning_effort" not in sent[1])

mafia_config.RUNTIME_OVERRIDES["base_url"] = "http://127.0.0.1:9"
_install([("ok", "stop")])
mafia_ai._llm_call(msgs, max_tokens=100)
check("다른 서버(vLLM 등)에는 reasoning_effort를 보내지 않음", "reasoning_effort" not in sent[0])

# ---- 비밀방 목표 파싱: 부정 표현이 붙은 이름은 목표로 확정하지 않는다 ----
from mafia_ui import MafiaUIMixin


class _PlanStub(MafiaUIMixin):
    def __init__(self):
        self.cands = ["김철수", "이영희", "박민수"]
        self.bc = []
        self._mafia_kill_plan = None

    def _mafia_kill_candidates(self):
        return self.cands

    def _mafia_secret_broadcast(self, who, text):
        self.bc.append((who, text))


def _plan_after(text, start=None):
    st = _PlanStub()
    st._mafia_kill_plan = start
    st._mafia_plan_from_human(text)
    return st


for txt in ("김철수는 안 돼", "김철수는 건드리지 말자", "김철수는 의사가 살릴 것 같으니 피하자",
            "김철수 말고 다른 사람", "김철수는 제외하자"):
    check(f"부정 발언 '{txt}'은 목표로 확정되지 않음", _plan_after(txt)._mafia_kill_plan is None)

st = _plan_after("김철수는 안 돼, 이영희로 하자")
check("'김철수는 안 돼, 이영희로 하자' → 이영희가 확정 목표",
      st._mafia_kill_plan == {"target": "이영희", "by": "human", "confirmed": True})
st = _plan_after("이영희 말고 김철수 노리자")
check("'이영희 말고 김철수 노리자' → 김철수가 확정 목표", (st._mafia_kill_plan or {}).get("target") == "김철수")
st = _plan_after("박민수 노리자")
check("부정 없는 '박민수 노리자'는 그대로 확정 목표",
      st._mafia_kill_plan == {"target": "박민수", "by": "human", "confirmed": True})

cur = {"target": "김철수", "by": "human", "confirmed": True}
st = _plan_after("아 김철수는 안 돼", start=cur)
check("이미 정한 목표를 '안 돼'라고 하면 목표가 취소되고 비밀방에 알림",
      st._mafia_kill_plan is None and any("취소" in t for _w, t in st.bc))
st = _plan_after("이영희는 안 돼", start=cur)
check("다른 사람을 빼자는 말은 기존 목표를 건드리지 않음", st._mafia_kill_plan == cur)

ai_prop = {"target": "김철수", "by": "ai", "confirmed": False}
check("AI 제안에 '좋아'라고 하면 확정",
      _plan_after("좋아", start=ai_prop)._mafia_kill_plan == {"target": "김철수", "by": "human", "confirmed": True})
check("'좋아 근데 별로야'처럼 반대 표현이 섞이면 확정하지 않음",
      _plan_after("좋아 근데 별로야", start=ai_prop)._mafia_kill_plan == ai_prop)

# ---- 이름 겹침('민우'/'정민우')과 앞쪽 부정어('안 돼 김철수') ----
_ns = MafiaUIMixin._named_split
_c2 = ["민우", "정민우", "김철수", "이영희"]
check("'정민우는 안 돼'는 정민우도 민우도 목표로 잡지 않음(부분 문자열 충돌 없음)",
      _ns("정민우는 안 돼", _c2) == (None, {"정민우"}))
check("'정민우 노리자'는 정민우만 긍정(민우가 따로 잡히지 않음)", _ns("정민우 노리자", _c2) == ("정민우", set()))
check("'민우 노리자'는 민우", _ns("민우 노리자", _c2) == ("민우", set()))
check("'안 돼 김철수'처럼 부정어가 앞에 오면 부정", _ns("안 돼 김철수", _c2) == (None, {"김철수"}))
check("'별로야 김철수는'도 부정", _ns("별로야 김철수는", _c2) == (None, {"김철수"}))
check("'아니 김철수 노리자'는 서술이 있으니 긍정", _ns("아니 김철수 노리자", _c2) == ("김철수", set()))
check("'아니 이영희 로 하자'도 긍정", _ns("아니 이영희 로 하자", _c2) == ("이영희", set()))
check("'안 돼 김철수 이영희로 하자' → 이영희 긍정", _ns("안 돼 김철수 이영희로 하자", _c2)[0] == "이영희")
check("후보에 없는 말이나 빈 문장은 (None, 빈 집합)", _ns("", _c2) == (None, set()) and _ns("오늘 밤 조용하네", _c2) == (None, set()))

check("'별로야 김철수는 진짜'처럼 앞쪽 부정어 뒤에 부가어가 붙어도 부정", _ns("별로야 김철수는 진짜", _c2) == (None, {"김철수"}))
check("'안 돼 김철수는 패스'도 부정", _ns("안 돼 김철수는 패스", _c2) == (None, {"김철수"}))
check("'아니 김철수 수상하잖아'처럼 모호한 '아니'는 부정으로 보지 않음", _ns("아니 김철수 수상하잖아", _c2) == ("김철수", set()))
check("'별로야 김철수 말고 이영희 노리자' → 이영희", _ns("별로야 김철수 말고 이영희 노리자", _c2) == ("이영희", {"김철수"}))
check("'싫어 이영희 노리자'는 서술이 있어 긍정", _ns("싫어 이영희 노리자", _c2) == ("이영희", set()))

# ============================================================
# ① 새 판을 시작하면 이전 판의 상태가 전부 지워진다 (lobby_reset)
# ============================================================
from types import SimpleNamespace
from mafia_core import GameCore, Phase
from mafia_net import encode


def _dirty_core():
    c = GameCore("t")
    c.join("경찰A", False)
    c.join("미나", True)
    c.police_invest["미나"] = "mafia"
    c.police_report = ("미나", "mafia")
    c.last_protect = "경찰A"
    c.abstains.add("경찰A")
    c.voted_history.append({"미나": 2})
    c.defendant = "미나"
    c.defense_yes["경찰A"] = True
    c.night_targets["미나"] = "경찰A"
    c.votes["경찰A"] = "미나"
    c.night_target, c.night_saved = "경찰A", "미나"
    c.night_dead.append("미나")
    c.day_no, c.winner = 3, "mafia"
    c.log.append({"x": 1})
    c.phase = Phase.NIGHT
    return c


_c = _dirty_core()
_c.lobby_reset()
_fresh = GameCore("t")
_leaks = [k for k in vars(_fresh) if k != "lock" and getattr(_c, k) != getattr(_fresh, k)]
check("lobby_reset이 이전 판 상태를 전부 초기화(새 코어와 완전히 같음)", _leaks == [])

_c = _dirty_core()
_c.lobby_reset()
for _n in ("경찰A", "미나"):
    _c.join(_n, _n == "미나")
_c.players["경찰A"]["role"], _c.players["미나"]["role"] = "police", "citizen"
_c.phase = Phase.NIGHT
check("2판째: 이전 판에서 '마피아'였던 이름을 조사해도 새 판의 실제 역할(시민)로 판정",
      _c.police_investigate("미나") == "citizen")

_c = _dirty_core()
_c.lobby_reset()
for _n in ("경찰A", "미나"):
    _c.join(_n, False)
check("2판째: 이전 판의 기권·의사 직전 보호가 남아 있지 않음",
      _c.abstains == set() and _c.last_protect is None and _c.night_targets == {})

# ============================================================
# ② [MAFIA1] 송신자 검증
# ============================================================


class _GateStub(MafiaUIMixin):
    def __init__(self, me="이팀장B", host="김재무A", active=True, is_host=False):
        self.engine = SimpleNamespace(name=me)
        self._recruiter_host = host
        self.mafia_active = active
        self._is_host = is_host
        self.peers = {}

    def _mafia_is_host(self):
        return self._is_host

    def _mafia_peer_of(self, name):
        return self.peers.get(name)

    def _mafia_peer_raw(self, name):
        return self.peers.get(name)


_cl = _GateStub()                       # 원격 참가자(B) 입장
for _t in ("hsay", "asay", "sys", "hdm", "start", "night", "day", "death", "end", "tally", "vote",
           "vote_open", "revote_open", "defense_vote_open", "defense_start", "verdict",
           "recruit_update", "recruit_cancel", "ghost_say"):
    if not (_cl._proto_authorized(_t, {}, "김재무A") and not _cl._proto_authorized(_t, {}, "해커")):
        check(f"방장 전용 이벤트 '{_t}': 방장은 통과·다른 사람은 차단", False)
        break
else:
    check("방장 전용 이벤트 19종: 방장이 보내면 통과, 다른 사람이 보내면 차단", True)
check("가짜 '게임 종료/역할 통보'를 방장이 아닌 사람이 보내면 차단",
      not _cl._proto_authorized("end", {"winner": "mafia"}, "해커")
      and not _cl._proto_authorized("hdm", {"target": "이팀장B", "role": "citizen"}, "해커"))
check("방장(호스트)에게는 방장 전용 이벤트가 오면 누가 보냈든 차단",
      not _GateStub(me="김재무A", host="김재무A", is_host=True)._proto_authorized("end", {}, "해커"))
check("방장을 아직 모르는 참가자는 방장 전용 이벤트(모집 시작 제외)를 받지 않음",
      not _GateStub(host=None)._proto_authorized("start", {}, "김재무A"))

for _t, _f in (("vote_cast", "voter"), ("defense_vote_cast", "voter"), ("night_action", "actor"),
               ("user_say", "name"), ("lobby_chat", "sender"), ("mafia_to_ai", "name"),
               ("ghost_to_ai", "name"), ("recruit_join", "name"), ("recruit_leave", "name")):
    _host = _GateStub(me="김재무A", is_host=True)
    if not (_host._proto_authorized(_t, {_f: "이팀장B"}, "이팀장B")
            and not _host._proto_authorized(_t, {_f: "이팀장B"}, "해커")
            and not _host._proto_authorized(_t, {}, "이팀장B")):
        check(f"본인 이름 이벤트 '{_t}': 본인이 보내면 통과, 남의 이름으로 보내면 차단", False)
        break
else:
    check("본인 이름 이벤트 9종: 본인이 보내면 통과, 남의 이름으로 보내면 차단", True)

_ga = _GateStub()
check("마피아 비밀 대화: 방장이 중계하는 AI 발언은 통과, 남이 AI 이름을 사칭하면 차단",
      _ga._proto_authorized("mafia_say", {"name": "미나"}, "김재무A")
      and not _ga._proto_authorized("mafia_say", {"name": "미나"}, "해커"))
check("마피아 비밀 대화: 사람 동료의 말은 방장 중계로만 받는다(v1.100 — 직접 온 것은 차단)",
      _ga._proto_authorized("mafia_say", {"name": "동료"}, "김재무A")
      and not _ga._proto_authorized("mafia_say", {"name": "동료"}, "동료"))

check("모집 시작: 본인을 방장으로 알리는 사람만 통과(남을 방장으로 지목하면 차단)",
      _GateStub(host=None, active=False)._proto_authorized("recruit_start", {"host": "김재무A"}, "김재무A")
      and not _GateStub(host=None, active=False)._proto_authorized("recruit_start", {"host": "해커"}, "김재무A"))
check("진행 중인 판의 방장은 다른 사람이 모집 시작으로 바꿀 수 없음",
      not _GateStub(host="김재무A", active=True)._proto_authorized("recruit_start", {"host": "해커"}, "해커")
      and _GateStub(host="김재무A", active=True)._proto_authorized("recruit_start", {"host": "김재무A"}, "김재무A"))

_al = _GateStub()
_al.peers["김재무A"] = ("10.0.0.1", 50707)
check("별칭 때문에 표시 이름이 달라도 같은 접속 상대(peer)면 방장으로 인정",
      _al._proto_authorized("end", {}, "재무팀장(별칭)", ("10.0.0.1", 50707))
      and not _al._proto_authorized("end", {}, "재무팀장(별칭)", ("10.0.0.9", 50707)))

# 게이트가 실제 수신 경로에서 폐기하는지(투표 이벤트로 확인)
# ============================================================
# ③ 투표 대상은 패킷에 실리지 않는다(익명)
# ============================================================


class _VoteStub(_GateStub):
    def __init__(self):
        super().__init__()
        self.core = GameCore("t")
        for _n in ("김재무A", "이팀장B", "미나"):
            self.core.join(_n, _n == "미나")
        self.core.phase = Phase.DAY
        self.sent = []

    def _refresh_vote_progress_label(self):
        pass

    def _mafia_broadcast(self, ev_type, **kw):
        self.sent.append((ev_type, kw))


_vs = _VoteStub()
_vs._broadcast_vote_done("미나", "이팀장B")
_vs._broadcast_vote_done("미나", None)
check("호스트가 뿌리는 vote 이벤트에 투표 대상(target)이 없음",
      all("target" not in kw for _t, kw in _vs.sent) and _vs.sent[0][1] == {"voter": "미나", "abstain": False})
check("기권은 abstain=True로만 알림", _vs.sent[1][1] == {"voter": "미나", "abstain": True})

_vs = _VoteStub()
_vs._on_mafia_proto_msg(encode("vote", voter="미나", abstain=False), "김재무A")
check("클라이언트는 '누가 냈는지'만 표시(대상 없이)", _vs.core.votes.get("미나") == "")
_vs._on_mafia_proto_msg(encode("vote", voter="김재무A", abstain=True), "김재무A")
check("기권은 기권으로 처리", "김재무A" in _vs.core.abstains)
_vs = _VoteStub()
_vs._on_mafia_proto_msg(encode("vote", voter="미나", target="이팀장B"), "김재무A")
check("옛 호스트가 target을 실어 보내도 그 값은 저장하지 않음(누가 냈는지만)", _vs.core.votes.get("미나") == "")
_vs = _VoteStub()
_vs._on_mafia_proto_msg(encode("vote", voter="미나", target=None), "김재무A")
check("옛 호스트의 기권(target=None)도 기권으로 인식", "미나" in _vs.core.abstains)
_vs = _VoteStub()
_vs.core.votes["이팀장B"] = "미나"
_vs._on_mafia_proto_msg(encode("vote", voter="이팀장B", abstain=False), "김재무A")
check("호스트가 되돌려 보낸 내 투표 알림이 내가 기록한 실제 표를 덮어쓰지 않음", _vs.core.votes["이팀장B"] == "미나")
_vs = _VoteStub()
_vs._on_mafia_proto_msg(encode("vote", voter="미나", abstain=False), "해커")
check("방장이 아닌 사람이 보낸 vote 이벤트는 수신 경로에서 폐기", "미나" not in _vs.core.votes)

# ============================================================
# ④ 밤 AI 행동은 LLM이 판단한다(경찰·의사·마피아)
# ============================================================


class _FakeRoot:
    def __init__(self):
        self.scheduled = []

    def after(self, ms, fn=None, *a):
        self.scheduled.append((ms, fn))
        return len(self.scheduled)


class _FakePl:
    def __init__(self, name, role, replies=()):
        self.name, self.role, self.alive, self.booted = name, role, True, True
        self.memory, self.prompts, self.replies = [], [], list(replies)

    def say(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else None


class _NightStub(MafiaUIMixin):
    def __init__(self, replies=None, day_no=2):
        self.root = _FakeRoot()
        self.mafia_active = True
        self.core = GameCore("t")
        spec = [("사람", "citizen", False), ("마피아M", "mafia", True), ("경찰P", "police", True),
                ("의사D", "doctor", True), ("영희", "citizen", True), ("철수", "citizen", True)]
        for n, r, ai in spec:
            self.core.join(n, ai)
            self.core.players[n]["role"] = r
        self.core.phase, self.core.day_no = Phase.NIGHT, day_no
        replies = replies or {}
        self.pls = {n: _FakePl(n, r, replies.get(n, ())) for n, r, ai in spec if ai}
        self.ai = SimpleNamespace(players=list(self.pls.values()))
        self.sys = []

    def add_mafia_system(self, t, local=False):
        self.sys.append(t)

    def _night_ai_pick_async(self, pl, prompt, cands, cb):        # 스레드 없이 동기 실행
        cb(self._night_ai_parse(pl.say(prompt), cands))


import random as _rnd

# --- 경찰 ---
_s = _NightStub({"경찰P": ["선택 영희"]})
_s._night_ai_police(_s.pls["경찰P"], 2)
check("AI 경찰: LLM이 고른 사람을 실제로 조사(영희 = 마피아 아님)", _s.core.police_invest == {"영희": "citizen"})
check("AI 경찰 프롬프트에 후보와 자기 자신 제외가 반영됨",
      "영희" in _s.pls["경찰P"].prompts[0] and "경찰P" not in _s.pls["경찰P"].prompts[0].split("후보:")[1].split(".")[0])
for _ms, _fn in _s.root.scheduled:
    _fn()
check("AI 경찰: 늦은 대체(무작위) 타이머가 와도 조사는 한 번만", len(_s.core.police_invest) == 1)

for _bad, _why in (("모르겠어요", "후보에 없는 답"), (None, "LLM 실패"), ("선택 경찰P", "자기 자신 지목")):
    _s = _NightStub({"경찰P": [_bad, _bad, _bad]})
    _s._night_ai_police(_s.pls["경찰P"], 2)
    _tgt = list(_s.core.police_invest)
    check(f"AI 경찰: {_why}이면 무작위 후보로 대체해 정확히 1명 조사(자기 자신 제외)",
          len(_tgt) == 1 and _tgt[0] != "경찰P")

_s = _NightStub({"경찰P": ["선택 영희"]})
_s.core.police_invest["철수"] = "citizen"
_s._night_ai_police(_s.pls["경찰P"], 2)
check("AI 경찰: 이미 조사한 사람은 후보에서 제외", "철수" not in _s.pls["경찰P"].prompts[0].split("후보:")[1].split(".")[0])

_s = _NightStub({"경찰P": ["선택 영희"]})
_s.core.phase = Phase.DAY
_s._night_ai_police(_s.pls["경찰P"], 2)
check("밤이 끝난 뒤(낮) 도착한 LLM 결과는 적용하지 않음", _s.core.police_invest == {})
_s = _NightStub({"경찰P": ["선택 영희"]}, day_no=3)
_s._night_ai_police(_s.pls["경찰P"], 2)
check("다른 밤(day_no 불일치)의 LLM 결과는 적용하지 않음", _s.core.police_invest == {})

# --- 의사 ---
_s = _NightStub({"의사D": ["선택 철수"]})
_s._night_ai_doctor(_s.pls["의사D"], 2)
check("AI 의사: LLM이 고른 사람을 실제로 보호", _s.core.night_saved == "철수")
_s = _NightStub({"의사D": ["선택 의사D"]})
_s._night_ai_doctor(_s.pls["의사D"], 2)
check("AI 의사: 자기 자신도 보호할 수 있음", _s.core.night_saved == "의사D")
_s = _NightStub({"의사D": ["선택 영희"] * 3})
_s.core.last_protect = "영희"
_s._night_ai_doctor(_s.pls["의사D"], 2)
check("AI 의사: 어젯밤 보호한 사람(연속 금지)은 후보에서 빠지고 LLM이 골라도 보호하지 않음",
      _s.core.night_saved not in (None, "영희") and "영희" not in _s.pls["의사D"].prompts[0].split("후보:")[1].split(".")[0])
_s = _NightStub({"의사D": [None, None, None]})
_s._night_ai_doctor(_s.pls["의사D"], 2)
check("AI 의사: LLM 실패 시 무작위 후보를 보호(밤 행동이 비지 않음)", _s.core.night_saved is not None)

# --- 마피아 ---
_s = _NightStub({"마피아M": ["선택 철수"]})
_s._night_ai_mafia(2)
check("AI 마피아: LLM이 고른 사람을 AI 마피아의 살해 지목으로 기록(후보는 시민 쪽만)",
      _s.core.night_targets.get("마피아M") == "철수"
      and "마피아M" not in _s.pls["마피아M"].prompts[0].split("후보(시민 쪽 생존자):")[1].split(".")[0])
_s = _NightStub({"마피아M": ["선택 철수"]})
_s._mafia_kill_plan = {"target": "영희", "by": "human", "confirmed": True}
_s._night_ai_mafia(2)
check("비밀방에서 사람이 목표를 정했으면 AI가 LLM으로 다시 고르지 않음(호출도 안 함)",
      _s.pls["마피아M"].prompts == [] and _s.core.night_targets == {})
_s = _NightStub({"마피아M": ["선택 철수"]})
_s.core.players["사람"]["role"] = "mafia"
_s.core.night_targets["사람"] = "영희"
_s._night_ai_mafia(2)
check("사람 마피아가 밤 패널에서 이미 골랐으면 AI가 LLM으로 다시 고르지 않음",
      _s.pls["마피아M"].prompts == [] and "마피아M" not in _s.core.night_targets)
_s = _NightStub({"마피아M": ["선택 철수"]})
_s.core.phase = Phase.DAY
_s._night_ai_mafia(2)
check("낮이 되면 마피아 LLM 결과를 적용하지 않음", _s.core.night_targets == {})

check("LLM 답 파싱: '선택 이름' / 문장 속 이름 / 후보에 없는 이름",
      MafiaUIMixin._night_ai_parse("선택 영희", ["영희", "철수"]) == "영희"
      and MafiaUIMixin._night_ai_parse("음… 철수가 제일 수상해요", ["영희", "철수"]) == "철수"
      and MafiaUIMixin._night_ai_parse("아무도 모르겠어", ["영희", "철수"]) is None
      and MafiaUIMixin._night_ai_parse("", ["영희"]) is None)

# ============================================================
# 인격 풀 무작위 뽑기 / 최소 5인 / 삼킨 예외 기록
# ============================================================
_picks = [tuple(p["name"] for p in MafiaUIMixin._pick_ai_personas(4)) for _ in range(40)]
_one = MafiaUIMixin._pick_ai_personas(5)
check("AI 인격 뽑기: 요청한 수만큼, 중복 없이, 인격 풀에서 뽑음",
      len(_one) == 5 and len({p["name"] for p in _one}) == 5
      and all(p in mafia_config.ALL_PERSONAS for p in _one))
check("AI 인격 뽑기: 매판 같은 캐스팅이 아님(40번 뽑아 서로 다른 조합이 여러 개)", len(set(_picks)) > 5)
check("AI 인격 뽑기: 풀보다 많이 요청하면 풀 크기로 제한, 0이면 빈 목록",
      len(MafiaUIMixin._pick_ai_personas(999)) == len(mafia_config.ALL_PERSONAS)
      and MafiaUIMixin._pick_ai_personas(0) == [])
check("AI 인격 뽑기: 성격별 발언 빈도·공격성이 그대로 딸려 감(원본 딕셔너리 보존)",
      all(("freq" in p) for p in MafiaUIMixin._pick_ai_personas(len(mafia_config.ALL_PERSONAS))
          if p["name"] in {x["name"] for x in mafia_config.PERSONAS20}))

check("최소 인원 5명(4인 게임 폐지)", mafia_config.MIN_PLAYERS == 5 and mafia_config.MIN_PLAYERS_CORE == 5
      and min(mafia_config.ROLE_TABLE) == 5)
_g = GameCore("t")
for _i in range(4):
    _g.join("p%d" % _i, False)
check("4명이면 게임 시작이 거절됨", _g.start_game()[0] is False)
_g.join("p4", False)
_ok5, _assigned5 = _g.start_game()
check("5명이면 게임이 시작됨", _ok5 is True and len(_assigned5) == 5)
from mafia_core import alloc_roles
check("5~10명 모두 의사 1·경찰 1, 마피아는 5~6명 1 / 7~10명 2",
      all(sorted(alloc_roles(n)).count("doctor") == 1 and sorted(alloc_roles(n)).count("police") == 1
          and sorted(alloc_roles(n)).count("mafia") == (1 if n <= 6 else 2)
          and len(alloc_roles(n)) == n for n in range(5, 11)))

# ---- 삼킨 예외를 잃지 않는다 ----
import importlib
import tempfile
import applog
importlib.reload(applog)          # 위에서 로그 함수를 가짜로 바꿔 둔 것을 원상 복구
_logdir = tempfile.mkdtemp(prefix="applog_test_")
applog.init(_logdir)


def _boom_a():
    try:
        raise ValueError("첫 번째")
    except Exception as _e:
        applog.swallowed(_e)


def _boom_b():
    try:
        raise KeyError("두 번째")
    except Exception as _e:
        applog.swallowed(_e)


for _ in range(5):
    _boom_a()                     # 같은 위치·같은 예외는 한 번만 기록
_boom_b()
_lines = [l for l in open(os.path.join(_logdir, "debug.log"), encoding="utf-8").read().splitlines()
          if "| swallowed |" in l]
check("삼킨 예외 기록: 같은 위치를 5번 삼켜도 로그는 1줄(반복 경로에서 로그가 넘치지 않음)",
      sum("_boom_a" in l for l in _lines) == 1)
check("삼킨 예외 기록: 다른 위치는 따로 기록되고 파일:줄·함수·예외 종류가 남음",
      any("_boom_b" in l and "test_report_fixes.py:" in l and "KeyError" in l for l in _lines))
import shutil as _sh
_sh.rmtree(_logdir, ignore_errors=True)

import ast as _ast
_silent = {}
for _f in ("mafia_ui.py", "mafia_ai.py", "mafia_config.py", "mafia_core.py", "mafia_net.py"):
    _tree = _ast.parse(open(os.path.join(ROOT, _f), encoding="utf-8").read())
    _n = sum(1 for x in _ast.walk(_tree) if isinstance(x, _ast.ExceptHandler)
             and isinstance(x.type, _ast.Name) and x.type.id == "Exception"
             and len(x.body) == 1 and isinstance(x.body[0], _ast.Pass))
    if _n:
        _silent[_f] = _n
check("마피아 모듈에 예외를 조용히 삼키는 'except Exception: pass'가 없음(다시 생기면 실패)", _silent == {})

# ============================================================
# v1.65 — 밤 행동 역할 검증 / 접속 주소 묶음 / 협박 판정 / 멘션 / 선택 순서 / 최대 인원
# ============================================================
# ---- 밤 행동은 요청자의 '실제' 역할과 같은 역할만 받는다(시민이 role="police"를 적어 보내도 무시) ----
def _night_apply(actor, role, phase=Phase.NIGHT, mutate=None):
    s = _NightStub()
    s.core.phase = phase
    if mutate:
        mutate(s)
    notes = []
    ok = s._night_action_apply(actor, role, "영희", notes.append)
    return s, ok, notes


_s, _ok, _n = _night_apply("사람", "police")
check("시민이 role=police로 위조한 조사 요청은 거부(조사도 결과 회신도 없음)",
      _ok is False and _s.core.police_invest == {} and _s.core.police_report is None
      and any("할 수 없" in t for t in _n) and not any("마피아" in t for t in _n))
_s, _ok, _n = _night_apply("사람", "doctor")
check("시민이 role=doctor로 위조한 보호 요청은 거부(의사의 보호 대상을 덮어쓰지 못함)",
      _ok is False and _s.core.night_saved is None)
_s, _ok, _n = _night_apply("사람", "mafia")
check("시민이 role=mafia로 위조한 살해 지정은 거부", _ok is False and _s.core.night_target is None)
_s, _ok, _n = _night_apply("경찰P", "doctor")
check("경찰이 의사 행동을 요청하면 거부(자기 역할만 가능)", _ok is False and _s.core.night_saved is None)
_s, _ok, _n = _night_apply("경찰P", "police")
check("진짜 경찰의 조사는 정상 처리(결과 회신)",
      _ok is True and _s.core.police_invest == {"영희": "citizen"} and any("마피아가 아닙니다" in t for t in _n))
_s, _ok, _n = _night_apply("의사D", "doctor")
check("진짜 의사의 보호는 정상 처리", _ok is True and _s.core.night_saved == "영희")
_s, _ok, _n = _night_apply("마피아M", "mafia")
check("진짜 마피아의 살해 지정은 정상 처리", _ok is True and _s.core.night_target == "영희")
_s, _ok, _n = _night_apply("경찰P", "police", mutate=lambda s: s.core.players["경찰P"].update(alive=False))
check("죽은 사람의 밤 행동은 거부", _ok is False and _s.core.police_invest == {})
_s, _ok, _n = _night_apply("경찰P", "police", phase=Phase.DAY)
check("밤이 아닐 때의 밤 행동은 거부", _ok is False and _s.core.police_invest == {})
_s, _ok, _n = _night_apply("경찰P", "citizen")
check("알 수 없는 역할 값은 거부", _ok is False)


class _HostNightStub(_NightStub):
    def __init__(self):
        super().__init__()
        self.engine = SimpleNamespace(name="방장")
        self.dms = []

    def _mafia_is_host(self):
        return True

    def _mafia_send_private(self, who, ev_type, **kw):
        self.dms.append((who, ev_type, kw.get("text", "")))

    def _ghost_dm(self, text):
        self.dms.append(("방장", "ghost", text))


_hs = _HostNightStub()
_hs._host_receive_night_action("사람", "police", "영희")
check("호스트 수신 경로: 원격 시민의 위조 조사는 결과 없이 '할 수 없다'는 회신만 감",
      _hs.core.police_invest == {} and len(_hs.dms) == 1 and "할 수 없" in _hs.dms[0][2])

# ---- 먼저 '고른' 사람의 선택이 팀 결정 (예전엔 먼저 '입장한' 사람의 선택이 항상 이겼다) ----
_o = _HostNightStub()
_o.core.players["사람"]["role"] = "mafia"
_o.core.players["사람2"] = {"role": "mafia", "alive": True, "is_ai": False, "color": "#fff", "addr": None}
_o.core.players["마피아M"]["role"] = "citizen"
_o.core.night_targets["사람2"] = "영희"      # 사람2가 먼저 고르고
_o.core.night_targets["사람"] = "철수"       # 사람이 나중에 고름(마피아 명단 순서는 사람 → 사람2)
_o._mafia_kill_plan = None
_o._reconcile_mafia_night()
check("인간 마피아 둘이 갈리면 입장 순서가 아니라 먼저 고른 사람의 선택이 팀 결정", _o.core.night_target == "영희")
_o = _HostNightStub()
_o.core.players["사람"]["role"] = "mafia"
_o.core.players["사람2"] = {"role": "mafia", "alive": True, "is_ai": False, "color": "#fff", "addr": None}
_o.core.players["마피아M"]["role"] = "citizen"
_o.core.night_targets["사람"] = "철수"
_o.core.night_targets["사람2"] = "영희"
_o._mafia_kill_plan = None
_o._reconcile_mafia_night()
check("반대로 사람이 먼저 고르면 사람의 선택이 팀 결정", _o.core.night_target == "철수")

# ---- 이름을 '접속 주소(ip, port)'에 묶는다 ----
class _IdStub(_GateStub):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.notes = []          # 이 PC 화면에만 보이는 안내(add_mafia_host_dm)
        self.public = []         # 전원에게 브로드캐스트되는 시스템 메시지(add_mafia_system)

    def add_mafia_host_dm(self, text):
        self.notes.append(text)

    def add_mafia_system(self, text, local=False):
        self.public.append(text)


_KA, _KB, _KX = ("10.0.0.1", 50707), ("10.0.0.2", 50707), ("10.0.0.9", 50707)
_c = _IdStub(host=None, active=False)
check("모집 시작을 보낸 접속 주소에 방장 이름이 묶임",
      _c._proto_authorized("recruit_start", {"host": "김재무A"}, "김재무A", _KA))
_c._recruiter_host = "김재무A"
check("방장 이름으로 온 방장 전용 이벤트는 그 접속 주소에서 온 것만 통과",
      _c._proto_authorized("end", {}, "김재무A", _KA))
check("표시 이름을 방장으로 바꿔도 다른 접속 주소에서 온 방장 전용 이벤트는 거부(이름만 믿지 않음)",
      not _c._proto_authorized("end", {}, "김재무A", _KX)
      and not _c._proto_authorized("hdm", {"target": "이팀장B", "role": "citizen"}, "김재무A", _KX))
for _ in range(3):
    _c._proto_authorized("end", {}, "김재무A", _KX)
check("사칭으로 보이는 요청은 사용자에게 한 번만 알림", len([t for t in _c.notes if "김재무A" in t]) == 1)
_c.mafia_active = True
check("진행 중인 판에서는 다른 주소가 모집 시작으로 방장을 가로챌 수 없음",
      not _c._proto_authorized("recruit_start", {"host": "해커"}, "해커", _KX)
      and _c._proto_authorized("end", {}, "김재무A", _KA))
_c.mafia_active = False
check("새 모집이 시작되면 이전 판의 이름·주소 묶음을 버림(재시작으로 주소가 바뀐 방장도 다음 판에는 가능)",
      _c._proto_authorized("recruit_start", {"host": "김재무A"}, "김재무A", _KX)
      and _c._proto_authorized("end", {}, "김재무A", _KX))

_h = _IdStub(me="김재무A", host="김재무A", is_host=True)
check("호스트: 참가 신청을 보낸 접속 주소에 참가자 이름이 묶임",
      _h._proto_authorized("recruit_join", {"name": "이팀장B"}, "이팀장B", _KB))
check("호스트: 참가자 본인 주소에서 온 밤 행동·투표는 통과",
      _h._proto_authorized("night_action", {"actor": "이팀장B"}, "이팀장B", _KB)
      and _h._proto_authorized("vote_cast", {"voter": "이팀장B"}, "이팀장B", _KB))
check("호스트: 참가자 이름을 적어도 다른 주소에서 온 밤 행동·투표는 거부(참가자 사칭)",
      not _h._proto_authorized("night_action", {"actor": "이팀장B"}, "이팀장B", _KX)
      and not _h._proto_authorized("vote_cast", {"voter": "이팀장B"}, "이팀장B", _KX))
check("호스트: 참가자가 방장 이름을 적어 보낸 요청은 거부",
      not _h._proto_authorized("night_action", {"actor": "김재무A"}, "이팀장B", _KB))

# ---- 협박 판정: 마피아 게임의 정상 발언은 협박이 아니다 ----
class _ToneStub(MafiaUIMixin):
    def __init__(self, hist):
        self.engine = SimpleNamespace(name="나")
        self.mafia_history = hist


def _rec(text, who=None):
    return {"kind": "text", "text": text, "label": who or "나", "mine": who is None}


check("협박 키워드에 게임 용어('처형하', '죽인', '죽일')가 없음",
      not any(k in mafia_config.THREAT_KEYWORDS for k in ("처형하", "죽인", "죽일")))
check("'철수 처형하자'·'누가 죽인 걸까'는 협박이 아님(중립)",
      _ToneStub([_rec("철수 처형하자"), _rec("누가 죽인 걸까")])._barometer_last_user_tone()[0] == "neutral")
check("진짜 협박('협박')은 여전히 감지", _ToneStub([_rec("이거 협박이다")])._barometer_last_user_tone()[0] == "threat")
_t = _ToneStub([_rec("협박이다", "이팀장B"), _rec("안녕")])
check("협박 판정은 발화자별 — 원격 사람의 협박은 그 사람에게만 적용(호스트 사용자는 중립)",
      _t._barometer_last_user_tone("이팀장B")[0] == "threat" and _t._barometer_last_user_tone()[0] == "neutral")
_t = _ToneStub([_rec("협박이다", "이팀장B")] + [_rec("다른말%d" % i, "이팀장B") for i in range(5)])
check("한 사람의 최근 발화 5개 안에서만 판정(오래된 협박은 사라짐)", _t._barometer_last_user_tone("이팀장B")[0] == "neutral")

# ---- 이름 멘션: 이름 전체가 들어 있을 때만 지목 ----
class _MentionStub(MafiaUIMixin):
    def __init__(self, names):
        self.ai = SimpleNamespace(players=[_FakePl(n, "citizen") for n in names])


_m = _MentionStub(["반장", "미나", "제이", "루카"])


def _mentioned(text):
    hits = _m._detect_mention(text)
    return sorted(p.name for p in hits) if hits else []


check("첫 글자만 겹치는 일반 단어는 지목이 아님('반대합니다'·'미안해'·'제가 할게')",
      _mentioned("반대합니다") == [] and _mentioned("미안해") == [] and _mentioned("제가 할게") == [])
check("이름을 부르면 지목('반장 어떻게 생각해', '미나야 안녕', '@루카')",
      _mentioned("반장 어떻게 생각해") == ["반장"] and _mentioned("미나야 안녕") == ["미나"]
      and _mentioned("@루카 너는?") == ["루카"])
check("여러 AI 이름이 함께 나오면 모두 지목", _mentioned("반장이랑 미나는 어때") == ["미나", "반장"])

# ---- 사람이 최대 인원을 채우면 시작을 거절 ----
class _LaunchStub(MafiaUIMixin):
    def __init__(self, n_humans):
        self.engine = SimpleNamespace(name="방장")
        self._recruited_humans = ["방장"] + ["p%d" % i for i in range(n_humans - 1)]
        self.core = GameCore("t")
        self.msgs, self.btn = [], []
        self.mafia_start_btn = SimpleNamespace(configure=lambda **k: self.btn.append(k))

    def add_mafia_system(self, t, local=False):
        self.msgs.append(t)


_ls = _LaunchStub(10)
_ls._recruiting = True
_ls.mafia_cancel_recruit_btn = SimpleNamespace(pack_forget=lambda: _ls.btn.append({"forgot": "cancel"}))
_ls.mafia_join_btn = SimpleNamespace(pack_forget=lambda: _ls.btn.append({"forgot": "join"}))
_ls._launch_game_with_recruits()
check("사람 10명(최대 인원)이면 AI 자리가 없어 시작을 거절하고 안내",
      any("너무 많" in t for t in _ls.msgs) and _ls.core.players == {} and not getattr(_ls, "mafia_active", False))
check("거절해도 모집 상태·버튼은 그대로(예전엔 거절 전에 모집을 끝내 버려 전원이 재신청해야 했음)",
      _ls._recruiting is True and _ls.btn == [])

# ============================================================
# v1.66 — 송신 경로 신원 / 사칭 안내 도배 방지 / 인증 전 초기화 금지 / 모집 인원 상한 / 선택 순서
# ============================================================
import threading as _th
import time as _tm


class _PeerEngine:
    """접속 상대 목록을 흉내 낸다(peers {(ip, port): {"name", "last"}}), 보낸 패킷을 기록한다."""

    def __init__(self, name="방장", peers=None):
        self.name = name
        self.plock = _th.RLock()
        self.peers = peers or {}
        self.sent = []

    def get_alias(self, key):
        return ""

    def send_message(self, ip, port, pkt):
        self.sent.append(((ip, port), pkt))


class _SendStub(MafiaUIMixin):
    def __init__(self, peers, me="방장"):
        self.engine = _PeerEngine(me, peers)


_NOW = _tm.time()
_K_ATT, _K_REAL = ("10.0.0.66", 50707), ("10.0.0.2", 50707)
_dup = {_K_ATT: {"name": "이팀장B", "last": _NOW}, _K_REAL: {"name": "이팀장B", "last": _NOW}}   # 공격자가 먼저 접속

# ---- A. 비밀 패킷의 수신자도 이름이 아니라 묶인 접속 주소로 정한다 ----
_ss = _SendStub(dict(_dup))
_ss._mafia_send_private("이팀장B", "hdm", target="이팀장B", text="당신의 직업은 마피아입니다")
check("[참고] 묶음이 없으면 이름이 같은 '첫' 접속자에게 감(옛 동작 — 사칭자가 먼저 접속했다면 그쪽으로)",
      _ss.engine.sent[-1][0] == _K_ATT)
_ss._ident()["이팀장B"] = _K_REAL
_ss._mafia_send_private("이팀장B", "hdm", target="이팀장B", text="당신의 직업은 마피아입니다")
_ss._mafia_send_private("이팀장B", "mafia_say", name="미나", text="오늘은 누구 노릴까")
check("이름이 접속 주소에 묶여 있으면 같은 이름의 다른 접속자가 먼저 있어도 묶인 주소로만 보냄(직업·비밀 대화·조사 결과)",
      [k for k, _p in _ss.engine.sent[1:]] == [_K_REAL, _K_REAL] and _ss._mafia_peer_of("이팀장B") == _K_REAL)
check("묶음이 없는 이름은 기존처럼 이름으로 찾음(별칭 등 호환)",
      _SendStub({_K_REAL: {"name": "소피", "last": _NOW}})._mafia_peer_of("소피") == _K_REAL)
check("같은 이름을 쓰는 접속자가 둘 이상 접속 중이면 '모호'하다고 판단",
      _SendStub(dict(_dup))._mafia_name_ambiguous("이팀장B") is True)
check("한쪽이 오래전 소식이 끊긴 옛 항목이면 모호하지 않음(재접속한 정상 참가자를 막지 않음)",
      _SendStub({_K_ATT: {"name": "이팀장B", "last": _NOW - 100000},
                 _K_REAL: {"name": "이팀장B", "last": _NOW}})._mafia_name_ambiguous("이팀장B") is False)


class _JoinStub(MafiaUIMixin):
    """방장(호스트) 쪽 모집 수신부."""

    def __init__(self, peers, roster):
        self.engine = _PeerEngine("김재무A", peers)
        self._recruiter_host = "김재무A"
        self._recruiting = True
        self.mafia_active = False
        self._recruited_humans = list(roster)
        self.mafia_start_btn = SimpleNamespace(config=lambda **k: None)
        self.notes, self.public, self.sent, self.cast = [], [], [], []

    def _mafia_is_host(self):
        return True

    def add_mafia_host_dm(self, t):
        self.notes.append(t)

    def add_mafia_system(self, t, local=False):
        self.public.append(t)

    def _mafia_broadcast(self, ev_type, **kw):
        self.cast.append((ev_type, kw))

    def _mafia_send_private(self, who, ev_type, **kw):
        self.sent.append((who, ev_type, kw.get("text", "")))


_jn = _JoinStub(dict(_dup), ["김재무A"])
_jn._on_mafia_proto_msg(encode("recruit_join", name="이팀장B"), "이팀장B", _K_REAL)
check("같은 이름 접속자가 둘 이상이면 참가 신청을 받지 않고(어느 쪽에도 이름을 묶지 않고) 안내",
      _jn._recruited_humans == ["김재무A"] and _jn._ident().get("이팀장B") is None
      and any("둘 이상" in t for t in _jn.notes))
_jn = _JoinStub({_K_REAL: {"name": "이팀장B", "last": _NOW}}, ["김재무A"])
_jn._on_mafia_proto_msg(encode("recruit_join", name="이팀장B"), "이팀장B", _K_REAL)
check("접속자가 한 명뿐이면 정상 참가되고 이름이 그 접속 주소에 묶임",
      _jn._recruited_humans == ["김재무A", "이팀장B"] and _jn._ident().get("이팀장B") == _K_REAL)

# ---- B. 모집 인원 상한(사람 최대 MAX_PLAYERS-1명) — 참가 신청 시점에 막고 신청자에게 알린다 ----
_full = ["김재무A"] + ["p%d" % i for i in range(mafia_config.MAX_PLAYERS - 2)]      # 사람 9명
_jn = _JoinStub({_K_REAL: {"name": "새사람", "last": _NOW}}, _full)
_jn._on_mafia_proto_msg(encode("recruit_join", name="새사람"), "새사람", _K_REAL)
check("사람이 최대(9명)면 10번째 참가 신청은 받지 않음", len(_jn._recruited_humans) == 9 and "새사람" not in _jn._recruited_humans)
check("거절된 신청자에게 '가득 찼다'는 개인 쪽지를 보내고 명단을 다시 알려 신청자 화면을 되돌림",
      any(w == "새사람" and "가득" in t for w, _e, t in _jn.sent) and any(e == "recruit_update" for e, _k in _jn.cast))
_jn = _JoinStub({_K_REAL: {"name": "새사람", "last": _NOW}}, _full[:-1])
_jn._on_mafia_proto_msg(encode("recruit_join", name="새사람"), "새사람", _K_REAL)
check("8명일 때 신청은 받아서 9명이 됨", len(_jn._recruited_humans) == 9 and "새사람" in _jn._recruited_humans)

# ---- C. 사칭 안내는 로컬 전용·이름별 1회·모집당 최대 5회 ----
_c = _IdStub(host="김재무A", active=False)
_c._ident()["김재무A"] = _KA
for _port in range(50000, 50040):              # 포트는 보내는 쪽이 정하는 값 — 바꿔 가며 40번 위조
    _c._proto_authorized("end", {}, "김재무A", ("10.0.0.9", _port))
check("포트만 바꿔 위조를 40번 보내도 안내는 1번(도배 방지)", len([t for t in _c.notes if "김재무A" in t]) == 1)
check("사칭 안내는 이 PC 화면에만 표시하고 전원에게 브로드캐스트하지 않음", _c.public == [])
for _i in range(10):
    _c._ident()["참가자%d" % _i] = _KB
    _c._proto_authorized("vote_cast", {"voter": "참가자%d" % _i}, "참가자%d" % _i, _KX)
check("서로 다른 이름을 잔뜩 사칭해도 한 모집에 최대 5번만 안내", len(_c.notes) == 5)
_c._reset_ident()
check("새 모집이 시작되면 안내 기록도 초기화", _c._ident() == {} and len(_c._mafia_ident_noted) == 0)

# ---- D. 인증에 실패한 recruit_start는 기존 이름·주소 묶음을 건드리지 못한다 ----
_d = _IdStub(host=None, active=False)
_d._proto_authorized("recruit_start", {"host": "김재무A"}, "김재무A", _KA)
_d._recruiter_host = "김재무A"
_before = dict(_d._ident())
check("방장이 아닌 사람이 남의 이름을 방장으로 적은 모집 시작은 거부되고 묶음을 지우지 못함",
      _d._proto_authorized("recruit_start", {"host": "김재무A"}, "해커", _KX) is False
      and _d._ident() == _before and _d._proto_authorized("end", {}, "김재무A", _KA))
check("본인을 방장으로 알린 진짜 새 모집만 묶음을 초기화(이전 판 묶음은 버려짐)",
      _d._proto_authorized("recruit_start", {"host": "김재무A"}, "김재무A", _KA)
      and _d._ident().get("김재무A") == _KA and len(_d._ident()) == 1)

# ---- 5. '먼저 고른' = 각자의 마지막 선택이 빨랐던 순서 ----
_g = GameCore("t")
for _n, _ai in (("사람", False), ("사람2", False), ("영희", True), ("철수", True), ("의사D", True)):
    _g.join(_n, _ai)
_g.players["사람"]["role"] = _g.players["사람2"]["role"] = "mafia"
_g.phase = Phase.NIGHT
_g.mafia_night_vote("사람", "철수")
_g.mafia_night_vote("사람2", "영희")
_g.mafia_night_vote("사람", "의사D")           # 사람이 마음을 바꿔 다시 고름
check("다시 고른 사람은 순서가 뒤로 감(마지막 선택 시각 순)", list(_g.night_targets) == ["사람2", "사람"])
_o = _HostNightStub()
_o.core.players["사람"]["role"] = "mafia"
_o.core.players["사람2"] = {"role": "mafia", "alive": True, "is_ai": False, "color": "#fff", "addr": None}
_o.core.players["마피아M"]["role"] = "citizen"
_o.core.mafia_night_vote("사람", "철수")
_o.core.mafia_night_vote("사람2", "영희")
_o.core.mafia_night_vote("사람", "의사D")
_o._mafia_kill_plan = None
_o._reconcile_mafia_night()
check("A가 먼저 골랐다 바꾼 뒤 B가 골랐던 상황에서는 먼저 최종 선택을 끝낸 B의 선택이 팀 결정", _o.core.night_target == "영희")

# ---- 팝업 글자 크기: 버튼 글자를 라벨·제목과 같은 포인트 기준으로 통일 ----
import ast as _ast2
from mafia_ui_common import POPUP_BTN_PX, POPUP_SMALL_BTN_PX, pt_px
check("팝업 버튼 글자 크기는 10pt/9pt를 픽셀로 환산한 값(13px/12px)",
      pt_px(10) == 13 and pt_px(9) == 12 and POPUP_BTN_PX == 13 and POPUP_SMALL_BTN_PX == 12)
_literal = []
for _f in ("mafia_ui_night.py", "mafia_ui_vote.py", "mafia_ui_secret.py"):
    for _n in _ast2.walk(_ast2.parse(open(os.path.join(ROOT, _f), encoding="utf-8").read())):
        if isinstance(_n, _ast2.keyword) and _n.arg == "font_size" and isinstance(_n.value, _ast2.Constant):
            _literal.append(f"{_f}:{_n.value.value}")
check("밤·투표·비밀방 팝업의 알약 버튼이 글자 크기를 숫자로 직접 쓰지 않고 공용 상수를 씀(9px·10px 혼용 재발 방지)",
      _literal == [])

# ============================================================
# mafia_ui 분할 구조 무결성 — 믹스인 모듈로 나눈 뒤에도 깨지지 않게 지킨다
# ============================================================
import builtins as _bi
import dis as _dis
import inspect as _inspect
import types as _types

_UI_MODS = ["mafia_ui_view", "mafia_ui_net", "mafia_ui_secret", "mafia_ui_night",
            "mafia_ui_vote", "mafia_ui_ai", "mafia_ui"]


def _code_objs(code):
    yield code
    for c in code.co_consts:
        if isinstance(c, _types.CodeType):
            yield from _code_objs(c)


def _undefined_globals(modname):
    mod = importlib.import_module(modname)
    bad = set()
    for cname, cls in _inspect.getmembers(mod, _inspect.isclass):
        if cls.__module__ != modname:
            continue
        for attr, val in vars(cls).items():
            fn = val.__func__ if isinstance(val, (staticmethod, classmethod)) else val
            if not isinstance(fn, _types.FunctionType):
                continue
            for co in _code_objs(fn.__code__):
                for ins in _dis.get_instructions(co):
                    if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME") and ins.argval not in vars(mod) \
                            and not hasattr(_bi, ins.argval):
                        bad.add(f"{cname}.{attr}:{ins.argval}")
    return sorted(bad)


for _m in _UI_MODS:
    _bad = _undefined_globals(_m)
    check(f"{_m}: 메서드가 쓰는 전역 이름이 모두 정의돼 있음(분할 후 이름 누락 방지)", _bad == [])
    if _bad:
        print("     ", _bad[:5])

from mafia_ui import MafiaUIMixin as _Composed
_owners = {}
for _k in _Composed.__mro__:
    if _k is object:
        continue
    for _a in vars(_k):
        if not _a.startswith("__"):
            _owners.setdefault(_a, []).append(_k.__name__)
_dupes = {a: o for a, o in _owners.items() if len(o) > 1}
check("MafiaUIMixin: 같은 메서드가 두 믹스인에 중복 정의돼 있지 않음(MRO 순서로 덮어써지는 사고 방지)", _dupes == {})
check("MafiaUIMixin은 6개 기능 믹스인을 합친 구조", len(_Composed.__mro__) == 8)
_biggest = max(
    (open(os.path.join(ROOT, _f + ".py"), encoding="utf-8").read().count(chr(10)), _f)
    for _f in _UI_MODS + ["mafia_ui_common"])
check(f"mafia_ui 계열 파일이 한 덩어리로 다시 커지지 않음(최대 {_biggest[1]} {_biggest[0]}줄 < 1500줄)", _biggest[0] < 1500)

print("REPORT FIXES PASSED" if ok_all else "REPORT FIXES FAILED")
sys.exit(0 if ok_all else 1)
