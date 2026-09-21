# -*- coding: utf-8 -*-
"""mafia_ai.py — MAFIA의 AI 두뇌 계층.

구성:
  1) LLM 직접 호출 레이어(사회자 + AI 플레이어 공용)
     — 사내 vLLM(OpenAI 호환)을 urllib로 직접 호출. 응답 2~4초.
  2) PlayerAgent      — AI 플레이어 1명. 자기 대화 기록(self.memory) 유지,
     say(prompt)마다 시스템 프롬프트 + 최근 대화를 서버에 보내 '같은 AI'가
     이어서 말하도록 함. socles: freq(발언 빈도)·att(공격성) 파라미터.
  3) AIDirector       — AI 명단 관리, 병렬 발화, observe_all(모든 발언 관찰).

v1.06: spawn_all(names=...) — 인격 템플릿 'name'이 아니라 core에 실제 등록된
AI 참가자 명단으로 PlayerAgent.name을 설정 (사회자·AI가 같은 명단 지칭).
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import random as _rndm

import applog
from mafia_config import (LLM_BASE_URL, LLM_MODEL, HERMES_REPLY_LANG,
                          PLAYER_CONTEXT_TURNS, get_llm_api_key)

# ============================================================
# 1) LLM 직접 호출
# ============================================================
_llm_cache = {}                      # 사회자 개회사 등 단발 텍스트 캐시
_llm_path_cache = {}                 # base_url -> 실제로 응답한 chat/completions 경로
_LLM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MAFIA-LAN-Talk/1.61"
_cache_lock = threading.Lock()


def _trim_to_sentence(text):
    """토큰 한도로 끝이 잘린 응답을 마지막으로 완결된 문장(또는 ㅋㅋ/~ 등 끝맺음)까지만 남긴다.
    끝맺음이 너무 앞이거나 없으면 원문 그대로 둔다."""
    if not text:
        return text
    for i in range(len(text) - 1, -1, -1):
        if text[i] in ".!?…~ㅋㅎㅠ":
            return text[:i + 1].strip() if i >= 8 else text
    return text


def _llm_call(messages, max_tokens=350, timeout=45):
    """OpenAI 호환 호출. 응답이 토큰 한도(finish_reason=length)로 잘렸으면 한도를 4배로 올려
    한 번 더 요청하고, 그래도 잘리면 마지막 완결 문장까지만 돌려준다. 실패 시 None."""
    meta = {}
    text = _llm_call_once(messages, max_tokens, timeout, meta)
    if meta.get("finish") == "length":
        try:
            applog.log("mafia_llm_truncated", detail=f"max_tokens={max_tokens} len={len(text or '')} → 재요청")
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        meta2 = {}
        text2 = _llm_call_once(messages, max_tokens * 4, timeout, meta2)
        if text2:
            text, meta = text2, meta2
        if meta.get("finish") == "length":
            text = _trim_to_sentence(text)
    return text


def _llm_call_once(messages, max_tokens, timeout, meta, _reasoning=True):
    """OpenAI 호환 /v1/chat/completions 호출 1회. 실패 시 None. meta['finish']에 finish_reason."""
    import mafia_config
    key = get_llm_api_key()
    ov = dict(mafia_config.RUNTIME_OVERRIDES)
    if not ov and getattr(mafia_config, "_OVERRIDES_FILE", None):
        mafia_config.load_overrides()
        ov = dict(mafia_config.RUNTIME_OVERRIDES)
    payload = {
        "model": (ov.get("model") or LLM_MODEL),
        "messages": messages,
        "max_tokens": max_tokens,
    }
    b_url = (ov.get("base_url") or LLM_BASE_URL).rstrip("/")
    # Gemini(생각 기능이 기본 켜진 모델)는 생각에 쓴 토큰도 max_tokens에 포함돼 답이 잘리거나
    # 비므로, 생각을 낮춰 달라고 요청한다(모르는 모델이 400으로 거절하면 아래에서 빼고 재시도).
    gemini = "generativelanguage.googleapis.com" in b_url
    if gemini and _reasoning:
        payload["reasoning_effort"] = "low"
    body = json.dumps(payload).encode("utf-8")
    if b_url.endswith("/v1"):
        b_url = b_url[:-3]
    if not b_url:
        # 서버 URL 기본값이 없다 — 게임 설정 또는 MAFIA_LLM_BASE_URL로 지정해야 한다.
        try:
            applog.log("mafia_llm_call", exc=None, detail="LLM 서버 URL이 설정되지 않음")
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        return None
    # v1.61 — 호출 경로/헤더를 서버마다 다른 관례에 맞춘다.
    #  · User-Agent: Cloudflare 등 앞단이 파이썬 기본 UA("Python-urllib")를 403(코드 1010)으로
    #    막는 서비스(클라우드 게이트웨이)가 있어 브라우저형 UA를 항상 붙인다.
    #  · 경로: vLLM/OpenAI는 <주소>/v1/chat/completions, 일부 게이트웨이는
    #    <주소>/chat/completions(주소 자체가 이미 /v1/... 를 포함) — 404면 다음 후보를 시도하고,
    #    통하는 경로는 기억해 다음 호출부터 바로 쓴다.
    if b_url.endswith("/chat/completions"):
        cands = [b_url]
    else:
        cands = [b_url + "/v1/chat/completions", b_url + "/chat/completions"]
    known = _llm_path_cache.get(b_url)
    if known in cands:
        cands.remove(known)
        cands.insert(0, known)
    last_err = None
    for url in cands:
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": _LLM_USER_AGENT,
                     "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            _llm_path_cache[b_url] = url
            choice = d["choices"][0]
            meta["finish"] = choice.get("finish_reason")
            msg = choice["message"]
            return (msg.get("content") or "").strip()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                continue          # 경로가 다른 서버일 수 있음 — 다음 후보
            if e.code == 400 and gemini and _reasoning:
                # 이 모델이 reasoning_effort를 지원하지 않는 경우 — 빼고 다시 한 번
                return _llm_call_once(messages, max_tokens, timeout, meta, _reasoning=False)
            break
        except Exception as e:
            last_err = e
            break
    # v1.56 — 예전엔 네트워크 타임아웃과 인증 오류/잘못된 모델명 같은 설정
    # 실수를 구분할 방법이 전혀 없었다(전부 조용히 None). applog에만 남겨서
    # 원인 파악은 가능하게 하되, 기존처럼 앱은 절대 죽지 않는다.
    try:
        detail = f"base_url={b_url} model={ov.get('model') or LLM_MODEL}"
        if isinstance(last_err, urllib.error.HTTPError):
            try:
                err_body = last_err.read().decode("utf-8", "replace")[:200]
                detail += " body=" + (err_body.replace(key, "***") if key else err_body)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
        applog.log("mafia_llm_call", exc=last_err, detail=detail)
    except Exception as _swallow_e:
        applog.swallowed(_swallow_e)
    return None


def host_llm_cached(system, user, max_tokens=350):
    """캐시 포함 LLM 호출(사회자 스타일 단발 호출). 최근 대화 전용 캐시."""
    cache_key = hash((system, user, max_tokens))
    now = time.time()
    with _cache_lock:
        hit = _llm_cache.get(cache_key)
        if hit and now - hit["ts"] < 12 * 3600:
            return hit["text"]
    text = _llm_call([{"role": "system", "content": system},
                      {"role": "user", "content": user}], max_tokens=max_tokens)
    if text:
        with _cache_lock:
            _llm_cache[cache_key] = {"ts": now, "text": text}
    return text


def sanitize_player_names(text, valid_names, replace_fallback=""):
    """사회자 LLM 응답 정제 — 실제 참가자 명단에 없는 이름 언급 정정/제거.

    1) 조사/호칭이 결합된 사람이름 후보(2~4글자)를 뽑아 valid에 정확/유사 매칭되면 치환.
    2) 유사한 실명이 없으면 그 언급을 제거(사회자가 없는 사람 지칭 방지).
    3) 결과가 빈 문장이 되면 replace_fallback 문장으로 대체(UI가 이어받음)."""
    if not text or not valid_names:
        return text
    import difflib

    def repl(m):
        raw = m.group(0)
        cand = raw.strip()
        if cand in valid_names:
            return raw
        close = difflib.get_close_matches(cand, valid_names, n=1, cutoff=0.55)
        if close:
            return raw.replace(cand, close[0])
        return ""

    # 사람이름 후보 — 'OOO님' 결합형만(문장 훼손 최소화) + 별도로 조사 결합형은
    # 후보가 'valid와 0.7 이상 유사'할 때만 치환, 아니면 건드리지 않음.
    pattern = r"[가-힣]{2,4}님"
    out = re.sub(pattern, repl, text)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.!?])", r"\1", out).strip(" ,.!? ")
    return (out or replace_fallback).strip()


def split_chat_tags(text):
    """문서 1-3: LLM이 <strategy>~<chat>~로 출력하면 <chat>만 취함."""
    if not text or "<" not in text:
        return (text or "").strip()
    m = re.search(r"<chat>\s*(.*?)\s*</chat>", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    if re.search(r"<strategy>", text, re.I):
        return ""
    return (text or "").strip()


def clean_llm_dialect(text):
    """LLM 오염 제거 — thinking·마크다운·role 접두어·캐릭터 불일치. 잘림 최소화."""
    if not text:
        return ""
    text = re.sub(r"(?is)^\s*(thinking\s*:|thought\s*:)[^\n]{0,80}\n", "", text)
    text = re.sub(r"(?is)^\s*\d+\.\s*\*\*[^*]+\*\*\s*:?\s*$", "", text, flags=re.M)
    text = re.sub(r"^\s*\*\s*$", "", text, flags=re.M)
    text = re.sub(r"^\s*[가-힣A-Za-z0-9_]{1,8}\s*:", "", text, count=1)
    text = re.sub(r"(?s)<think.*?</think>\s*", "", text)
    text = re.sub(r"(?im)^\s*(유저|사람|assistant|user|system|AI|사회자)\s*:", "", text)
    # 공백 정리
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    # 끝에 쓸데없이 붙는 '소스 코드' 같은 것 제거
    text = re.sub(r"```[\s\S]*```", "", text)
    return (text or "").strip()


# 공개 채팅에 나가면 안 되는 비밀 정보 표현(마피아 비밀 대화·동료 언급). 프롬프트 지시만으로는
# 모델이 어길 수 있어 출력 쪽에서도 문장 단위로 걸러낸다.
_LEAK_PATTERNS = (
    r"비밀\s*대화", r"마피아\s*(?:끼리|방|채팅|동료|팀|친구)", r"(?:동료|같은|우리|저희)\s*마피아",
    r"마피아\s*(?:인|라고)\s*(?:저|제가|나는|난)", r"(?:저|제가|나는|난)\s*마피아",
    r"내\s*역할|제\s*역할|나의\s*역할", r"(?:살해|죽일)\s*(?:대상|계획|타깃)",
)


def strip_secret_leaks(text, role=None):
    """문장 단위로 비밀 누설 표현이 든 문장을 제거한다. 마피아 AI에만 적용(시민 AI가
    '저 마피아 아니에요'라고 말하는 것은 정상이라 건드리지 않는다). 다 지워지면 빈 문자열."""
    if not text or role != "mafia":
        return text
    parts = re.split(r"(?<=[.!?~ㅋㅠ…])\s+|[\r\n]+", text)
    kept = [p for p in parts if p.strip() and not any(re.search(pt, p) for pt in _LEAK_PATTERNS)]
    return " ".join(kept).strip()


# ============================================================
# 2) AI 플레이어 개체
# ============================================================
class PlayerAgent:
    """AI 플레이어 1명 = 대화 기록을 유지하는 개체."""

    def __init__(self, name, persona, color, freq=50, att=50):
        self.name = name
        self.persona = persona
        self.color = color
        self.booted = False
        self.alive = True
        self.role = None
        self.busy = False
        self.last_say_ms = 0
        self.freq = max(5, min(95, int(freq)))   # 발언 참여 확률
        self.att = max(5, min(95, int(att)))     # 공격성
        self.memory = []
        self.lock = threading.Lock()
        self.intel = {}            # 경찰 AI가 밤 조사로 확인한 사실: 이름 -> "mafia" | "citizen" (기억 창과 무관하게 유지)
        self.core_ref = None       # 생존·일차 확인용(호스트가 assign_roles에서 넣는다)
        self.claims_fn = None      # 공개적으로 "나 경찰이다"라고 밝힌 생존자 목록을 돌려주는 함수(마피아·의사 AI가 쓴다)
        self.doctor_log = []       # 의사 AI의 밤 기록: [(밤 번호, 보호 대상, 결과 문장)] — 기억 창과 무관하게 유지

    # ---------- 경찰 AI의 조사 정보 ----------
    def add_intel(self, target, result):
        """밤 조사 결과를 영구 기록한다. 예전에는 채팅 기억(최근 12개만 참조)에 한 줄 적립할 뿐이라 몇 마디 뒤엔
        잊었고, "절대 노출 금지"만 있어 조사 결과를 활용하는 행동(지목·투표·커밍아웃)이 전혀 없었다."""
        if self.role == "police" and target and result in ("mafia", "citizen"):
            self.intel[target] = result

    def _alive(self, name):
        core = self.core_ref
        if core is None:
            return True
        return bool((core.players.get(name) or {}).get("alive", True))

    def known_mafia_alive(self):
        """조사로 확인한 마피아 중 아직 살아 있는 사람(자기 자신 제외)."""
        if self.role != "police":
            return []
        return [n for n, r in self.intel.items() if r == "mafia" and n != self.name and self._alive(n)]

    def public_police_claims(self):
        """채팅에서 스스로 경찰이라고 밝혔고 아직 살아 있는 사람(나 제외). 누구나 볼 수 있는 공개 정보다."""
        if self.claims_fn is None:
            return []
        try:
            return [n for n in self.claims_fn() if n != self.name]
        except Exception:
            return []

    def police_claimants(self):
        """(마피아 AI) 경찰을 자처한 생존자 — 표적 후보(동료 마피아는 호스트의 claims_fn이 이미 뺀다)."""
        return self.public_police_claims() if self.role == "mafia" else []

    # ---------- 의사 AI의 밤 기록 ----------
    def add_doctor_result(self, night_no, target, outcome):
        """의사 AI가 어젯밤 누구를 보호했고 어떻게 됐는지 영구 기록한다(최근 5건 유지)."""
        if self.role == "doctor" and target:
            self.doctor_log.append((night_no, target, outcome))
            del self.doctor_log[:-5]

    def _doctor_intel_prompt(self):
        if self.role != "doctor":
            return ""
        claims = self.public_police_claims()
        if not self.doctor_log and not claims:
            return ""
        lines = ["[의사의 비밀 정보 — 내가 아는 사실]"]
        for n, t, out in self.doctor_log:
            lines.append(f"  · {n}일차 밤: {t}님을 보호했고 {out}")
        if claims:
            lines.append(f"  · {', '.join(claims)}이(가) 채팅에서 '나는 경찰'이라고 밝혔습니다. 진짜 경찰이면 마피아가 가장 먼저 노립니다.")
        lines.append("  - 마피아에게 노려졌다가 내가 살린 사람이나 경찰을 자처한 사람은 시민 쪽 핵심일 수 있으니 낮에도 감싸 주고(투표로 몰지 말고), 밤엔 계속 지켜 주세요.")
        lines.append("  - 내가 의사라는 사실과 누구를 보호했는지는 채팅에 절대 쓰지 마세요(밝히면 마피아의 표적이 됩니다). 감싸는 이유는 '왠지 믿음이 간다' 식으로 자연스럽게.")
        return chr(10).join(lines) + chr(10)

    def _mafia_intel_prompt(self):
        cl = self.police_claimants()
        if not cl:
            return ""
        return ("[마피아의 비밀 정보]" + chr(10) +
                f"  · {', '.join(cl)}이(가) 채팅에서 '나는 경찰'이라고 밝혔습니다. 진짜 경찰일 가능성이 높아 마피아에게 가장 위험한 사람입니다." + chr(10) +
                "  - 그 사람을 시민들 앞에서 믿을 수 없다고 몰아가세요(예: '갑자기 경찰이라니 타이밍이 너무 수상한데?', '증거도 없이 커밍아웃하면 다 믿어야 해?')." + chr(10) +
                "  - 단, 내가 마피아라는 것·마피아 동료·밤 계획은 절대 말하지 말고 시민인 척 자연스럽게 의심하세요. 밤 살해 대상과 투표는 그 사람을 우선합니다." + chr(10))

    def _intel_prompt(self):
        if self.role != "police" or not self.intel:
            return ""
        mafs = self.known_mafia_alive()
        cits = [n for n, r in self.intel.items() if r == "citizen" and self._alive(n)]
        if not mafs and not cits:
            return ""
        day = getattr(self.core_ref, "day_no", 1) if self.core_ref is not None else 1
        lines = ["[경찰의 비밀 정보 — 내가 밤 조사로 직접 확인한 사실]"]
        if mafs:
            lines.append(f"  · 마피아로 확인됨(생존): {', '.join(mafs)}")
        if cits:
            lines.append(f"  · 마피아가 아님으로 확인됨(생존): {', '.join(cits)}")
        if mafs:
            lines.append("  - 마피아로 확인된 사람을 토론에서 수상하다고 계속 짚고 투표도 그 사람에게 하세요. 확신을 갖되 말투는 가볍게.")
            lines.append(f"  - 지금은 {day}일차입니다. 첫날엔 '왠지 저 사람이 걸린다' 정도로 은근히 몰고, 2일차 이후이거나 누가 나를 의심하거나"
                         " 토론이 다른 사람에게 쏠릴 때는 \"나 경찰이야, ○○ 조사했더니 마피아였어\"라고 **커밍아웃해도 됩니다**"
                         "(이때만 내 역할과 조사 결과를 말해도 됨. 조사 결과를 지어내거나 조사 안 한 사람을 말하지 말 것).")
        if cits:
            lines.append("  - 마피아가 아님으로 확인된 사람이 몰리면 근거를 대며 변호하세요(이유는 '왠지 믿음이 간다' 식으로 가볍게, 커밍아웃 시엔 조사했다고 말해도 됨).")
        return "\n".join(lines) + "\n"

    # ---------- 세션 부트스트랩(최초 1회, ~2초) ----------
    def start_bootstrap(self, host_name, players_desc):
        sys_prompt = (
            f"/no_thinking\n"
            f"당신은 마피아 게임 참가자 '{self.name}'입니다. 성격은: {self.persona}.\n"
            f"게임 사회자는 '{host_name}'입니다. 다른 참가자: {players_desc}.\n"
            f"역할은 사회자가 나중에 개별적으로 알려줍니다. 대사는 {HERMES_REPLY_LANG}")
        ok = self._turn(sys_prompt,
                        f"[게임 개요] 위 지시를 알겠다면 '{self.name} 준비완료'라고만 짧게 답하세요.",
                        store=True)
        self.booted = bool(ok)
        return self.booted

    # ---------- 한 턴 발언(~2초) ----------
    def say(self, prompt, secret=False):
        """발언 지시를 주고 대사 문자열 반환. 실패 시 None."""
        if not self.booted or self.busy:
            return None
        with self.lock:
            if self.busy:
                return None
            self.busy = True
        try:
            t0 = time.time()
            sys_prompt = (
                "/no_thinking\n"
                f"당신은 마피아 게임 참가자 '{self.name}'입니다. 성격은: {self.persona}.\n"
                + ((f"역할: {self.role or '미정'} — 역할에 맞게 행동하세요.\n" + self._intel_prompt())
                   if self._intel_prompt() else
                   f"역할: {self.role or '미정'} — 역할명은 절대 말하지 말고 역할에 맞게 행동하세요.\n" + self._mafia_intel_prompt() + self._doctor_intel_prompt()) +
                "[중요] 당신은 채팅에 있는 다른 플레이어들과 **대화**하고 있습니다.\n"
                "  - 들어온 프롬프트에 특정 발언이 있으면 그 말에 **직접 대답**하세요.\n"
                "  - 사람 이름을 불러 대화하세요. 질문이 오면 답하세요.\n"
                "  - 혼잣말, 게임 규칙 설명, 발표문 금지.\n"
                "  - 절대 규칙: 자기 자신을 투표/살해/구조 대상으로 지목 금지(자투 금지).\n"
                "  - 앞 사람 의견을 그대로 반복(앵무새)하지 말고 제3의 인물을 환기하거나\n"
                "    다른 관점을 덧대거나 변호하라.\n"
                "  - 외국어·한자·번역투 금지 — 실제 한국 단체 카톡방 구어체만.\n"
                "  - 의심을 바꿀 때는 이유를 붙여라(급격한 태세전환 금지).\n"
                "  - 비밀 정보(내 역할/전략)는 절대 채팅에 쓰지 마라(단, [경찰의 비밀 정보]에서 허용한 커밍아웃은 예외).\n"
                "  - [말투] 친구들끼리 노는 편안한 분위기로. 따지거나 추궁하는 어투, 논리·근거를\n"
                "    나열하는 발표식 말투, 상대 발언을 조목조목 반박하는 말투는 피하고, 의심은\n"
                "    '왠지 좀 그래 보여~' 같은 가벼운 느낌으로 말하라. 리액션·농담·공감을 섞어라.\n"
                "  - 반박당하거나 의심받아도 날 세우지 말고 웃으며 받아 넘겨라.\n"
                "  - 한 사람에게만 집중해서 공격·의심하지 마라(다구리 금지). 이미 누가 몰리고 있으면\n"
                "    같이 몰지 말고 다른 시선·변호·질문을 섞어라. 사람 참가자도 다른 참가자와 똑같이 대하라.\n"
                f"  - 대사는 {HERMES_REPLY_LANG} 2문장 이내. 감정에 따라 ㅋㅋ/ㅠㅠ를 갈려서 써라.")
            ok, text = self._turn(sys_prompt, prompt, store=True)
            if not ok or not text:
                return None
            text = split_chat_tags(text)
            text = clean_llm_dialect(text)
            if re.match(r"(?i)^\s*(thinking\s*:|thought\s*:|1\.)", text):
                return None
            if not secret:
                text = strip_secret_leaks(text, self.role)
                if not text:
                    return None
            self.last_say_ms = int((time.time() - t0) * 1000)
            return text
        finally:
            self.busy = False

    def observe(self, speaker, text):
        """다른 사람(유저/AI/사회자) 발언을 내 기억에 적립."""
        self.memory.append({"role": "user",
                            "content": f"[발언] {speaker}: {text}"})
        if len(self.memory) > 40:
            self.memory = self.memory[-40:]

    def _turn(self, sys_prompt, user_prompt, store=False):
        """1회 호출. messages = system + memory(-N) + user. store=True면 기록 저장."""
        msgs = [{"role": "system", "content": sys_prompt}]
        msgs += self.memory[-PLAYER_CONTEXT_TURNS:]
        msgs.append({"role": "user", "content": user_prompt})
        text = _llm_call(msgs, max_tokens=512)
        text = clean_llm_dialect(text)
        if store:
            self.memory.append({"role": "user", "content": user_prompt})
            if text:
                self.memory.append({"role": "assistant", "content": text})
            if len(self.memory) > 40:
                self.memory = self.memory[-40:]
        return (True, text) if text else (False, None)


# 이전 이름 호환
SubAgentPlayer = PlayerAgent


# ============================================================
# 3) AI 매니저 — 병렬 스폰/대사 큐
# ============================================================
class AIDirector:
    """AI 플레이어 목록을 관리하고, 턴 지시를 병렬 스레드로 던진다."""

    def __init__(self):
        self.players = []
        self.on_utt = None
        self.pending_talk = []

    def spawn_all(self, personas, host_name, players_desc, names=None):
        """v1.06: names가 주어지면(core AI 실명) 그 이름을 사용 — 사회자·AI가
        같은 명단을 지칭. personas의 'name'은 인격 톤 템플릿일 뿐."""
        if not names:
            names = [p["name"] for p in personas]
        self.players = [PlayerAgent(names[i] if i < len(names) else p["name"],
                                    p["persona"], p["color"],
                                    freq=p.get("freq", 50), att=p.get("att", 50))
                        for i, p in enumerate(personas)]
        ok_list = []
        for pl in self.players:
            ok = pl.start_bootstrap(host_name, players_desc)
            ok_list.append((pl.name, ok))
        self.flush_pending()
        return ok_list

    def assign_roles(self, roles, core=None, claims_fn=None):
        for pl in self.players:
            pl.role = roles.get(pl.name, "citizen")
            pl.intel = {}                      # 새 판 — 지난 판의 조사 정보를 버린다
            pl.doctor_log = []
            if core is not None:
                pl.core_ref = core
            if claims_fn is not None:
                pl.claims_fn = claims_fn

    def observe_all(self, speaker, text):
        """모든 살아있는 AI의 기억에 한 발언을 적립."""
        for pl in self.players:
            if pl.alive and pl.booted:
                pl.observe(speaker, text)

    def say_async(self, prompt_factory, honor_freq=True):
        """모든 살아있는 AI에 비동기 발언 지시. freq 확률로만 끼어들고 아니면 침묵."""
        import random as _r
        for pl in self.players:
            if not pl.alive:
                continue
            if honor_freq and _r.random() * 100 > getattr(pl, "freq", 50):
                continue
            if not pl.booted:
                self.pending_talk.append((pl, prompt_factory))
                continue
            if pl.busy:
                continue
            th = threading.Thread(
                target=self._say_worker, args=(pl, prompt_factory), daemon=True)
            th.start()

    def say_one_async(self, pl, prompt_factory):
        """특정 AI 1명에게만 비동기 발언 지시 (연쇄 폭발 방지)."""
        if not pl.alive or not pl.booted or pl.busy:
            return
        threading.Thread(
            target=self._say_worker, args=(pl, prompt_factory), daemon=True).start()

    def flush_pending(self):
        pend, self.pending_talk = self.pending_talk, []
        for pl, factory in pend:
            if pl.alive and pl.booted and not pl.busy:
                th = threading.Thread(
                    target=self._say_worker, args=(pl, factory), daemon=True)
                th.start()

    def _say_worker(self, pl, prompt_factory):
        prompt = prompt_factory(pl)
        text = pl.say(prompt)
        if text and self.on_utt:
            self.on_utt(pl.name, pl.color, text)

    def stop_all(self):
        for pl in self.players:
            pl.alive = False
