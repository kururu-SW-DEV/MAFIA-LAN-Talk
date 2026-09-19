# -*- coding: utf-8 -*-
"""mafia_net.py — 마피아 P2P 동기화 레이어.

구조:
  - 호스트(사회자 역할을 맡은 클라이언트, [게임 시작]을 누른 사람)가 권한을 가진다.
  - 게임 이벤트(역할배정결과 제외 개인정보 / 발언 / 투표 / 개표 / 밤낮 전환 / 처형 / 승패)를
    랜톡 UDP DM 프로토콜 텍스트에 실어 모든 접속 피어에게 전송한다.
  - 클라이언트는 engine "msg" 이벤트로 수신한 텍스트를 _on_gmsg 경로에서 파싱해
    자기 게임방 로컬 렌더에 반영한다. 클라이언트는 명령을 수행만 하고 재전송하지 않는다.

텍스트 프로토콜(단일 라인):
  [MAFIA1] <json>
  json 필수키: t(이벤트타입) 호스트 전용: host=sombre name.
"""
MAFIA_PROTO = "[MAFIA1]"

EVENT_ROLE = "role"          # (private DM — role 전달용, 수신자만 자기 행 처리)
EVENT_HOST_SAY = "hsay"      # 사회자 공개 발언
EVENT_HOST_DM = "hdm"        # 사회자 → 특정인 개인 쪽지(역할 통보 등)
EVENT_AI_SAY = "asay"        # AI 참가자 발언 (호스트가 LLM 호출 후 중계)
EVENT_SYSTEM = "sys"         # 게임 진행 시스템 안내(개표 결과, 밤낮 등)
EVENT_JOIN = "join"
EVENT_START = "start"
EVENT_VOTE = "vote"
EVENT_NIGHT = "night"
EVENT_DAY = "day"
EVENT_DEATH = "death"
EVENT_END = "end"

# v1.36: 참가자 모집(로비/Ready) 프로토콜
EVENT_RECRUIT_START = "recruit_start"    # 방장의 모집 개시
EVENT_RECRUIT_JOIN = "recruit_join"      # 참가자의 신청
EVENT_RECRUIT_LEAVE = "recruit_leave"    # 참가자의 취소
EVENT_RECRUIT_UPDATE = "recruit_update"  # 명단 동기화
EVENT_RECRUIT_CANCEL = "recruit_cancel"  # 방장의 모집 취소

# v1.37: 게임 시작 전 로비 인간 단체 채팅
EVENT_LOBBY_CHAT = "lobby_chat"

# v1.39: 재판정 최후변론 및 판결 연출 프로토콜
EVENT_DEFENSE_START = "defense_start"
EVENT_VERDICT = "verdict"

# v1.61: 복수 인간 플레이어 동기화 — 게임 시작 후 발언/투표/밤행동/찬반은
# 호스트 권위형(host-authoritative)으로 처리한다. 클라이언트는 자기 선택을
# 호스트에게 개인 쪽지(private DM)로 보내기만 하고, 호스트가 core에 반영한
# 뒤 결과를 (이미 있던 vote/tally/verdict/sys 등으로) 다시 뿌린다.
EVENT_USER_SAY = "user_say"            # 게임 중 사람 발언 — 전원에게 메쉬 브로드캐스트
EVENT_VOTE_CAST = "vote_cast"          # 클라이언트 → 호스트: 낮 투표(지목/기권)
EVENT_NIGHT_ACTION = "night_action"    # 클라이언트 → 호스트: 밤 행동(살해/치료/조사)
EVENT_DEFENSE_VOTE_CAST = "defense_vote_cast"  # 클라이언트 → 호스트: 찬반(처형여부)

import json


def encode(ev_type, **kw):
    kw["t"] = ev_type
    try:
        body = json.dumps(kw, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    return MAFIA_PROTO + body


def decode(text):
    if not text or not text.startswith(MAFIA_PROTO):
        return None
    raw = text[len(MAFIA_PROTO):]
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict) or "t" not in obj:
        return None
    return obj
