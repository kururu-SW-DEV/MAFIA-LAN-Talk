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

print("REPORT FIXES PASSED" if ok_all else "REPORT FIXES FAILED")
sys.exit(0 if ok_all else 1)
