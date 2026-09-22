# -*- coding: utf-8 -*-
"""mafia_core.py — 마피아 게임 순수 로직 계층 (상태머신·역할배분·투표 집계·승패 판정).

GUI/네트워크/AI 의존성 없음 — 단위 테스트 가능. 사회자(HostGameMaster)가 이 모듈의
공개 API만 호출해 게임을 진행한다.
"""
import random
import threading
import time

from mafia_config import ROLE_TABLE, MIN_PLAYERS_CORE


class Phase:
    LOBBY = "lobby"
    DAY = "day"           # 낮: 토론 + 지목투표
    VOTE = "vote"         # 개표·처형 연출 중
    NIGHT = "night"       # 밤: 마피아/의사 행동 수집
    END = "end"


def alloc_roles(total):
    """시작 인원 → 역할 배분. 정해진 규칙(ROLE_TABLE)에 따라 무작위 배분."""
    if isinstance(total, (list, tuple, set)):
        total = len(total)
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = 4
    valid_keys = [k for k in ROLE_TABLE if k <= total]
    max_k = max(valid_keys) if valid_keys else min(ROLE_TABLE.keys())
    tbl = ROLE_TABLE.get(total) or ROLE_TABLE.get(max_k) or {"mafia": 1, "doctor": 0, "police": 0}
    roles = []
    roles += ["mafia"] * tbl["mafia"]
    roles += ["doctor"] * tbl.get("doctor", 0)
    roles += ["police"] * tbl.get("police", 0)
    roles += ["citizen"] * max(0, total - len(roles))
    random.shuffle(roles)
    return roles


class GameCore:
    """게임 상태의 유일한 진실 공급원(source of truth). 스레드 안전."""

    def __init__(self, game_id):
        self.game_id = game_id
        self.lock = threading.RLock()
        self._reset_state()

    def _reset_state(self):
        """한 판의 모든 상태를 초기값으로 되돌린다. __init__과 lobby_reset이 함께 쓴다 —
        필드를 여기 한 곳에만 두어야 새 필드를 추가하고 초기화에서 빠뜨려 판 사이에 상태가
        새는 일(예: 이전 판의 경찰 조사 결과가 다음 판에 남던 버그)이 안 생긴다."""
        self.phase = Phase.LOBBY
        self.players = {}          # name -> {"role": str, "alive": bool, "is_ai": bool,
                                   #         "color": str, "addr": (ip,port)|None}
        self.day_no = 0
        self.night_target = None        # 밤 마피아 타깃
        self.night_saved = None         # 밤 의사 타깃
        self.night_dead = []            # 밤 사망자(중간체크용)
        self.votes = {}                 # 투표자 -> 피투표자
        # --- 문서 기획 보강(2026-09-17): 기존 동작 유지, 없는 것만 추가 ---
        self.police_invest = {}         # 경찰 조사 이력: target -> "mafia"|"citizen"
        self.police_report = None       # 직전 밤 경찰 조사 결과 (target, result)
        self.last_protect = None        # 의사 직전 밤 보호 대상(연속 보호 금지 판정)
        self.night_targets = {}         # 밤 다수 마피아 개별 지목: mafia_name -> target
        self.abstains = set()           # 기권(투표 타임아웃)자
        self.voted_history = []         # 직전 낮 투표 내역(심리전 근거)
        self.defendant = None           # 최후 변론 피고인
        self.defense_yes = {}           # 최후 변론 찬반 투표 기록
        self.winner = None              # "citizen"/"mafia"/None
        self.log = []                   # 이벤트 로그(사회자 정리용)

    # ---------- 로비 ----------
    def lobby_reset(self):
        """로비로 되돌린다 — 새 판을 시작하기 전에 이전 판의 상태를 전부 지운다."""
        with self.lock:
            self._reset_state()

    def join(self, name, is_ai, color=None, addr=None):
        with self.lock:
            if self.phase != Phase.LOBBY or name in self.players:
                return False
            self.players[name] = {"role": None, "alive": True, "is_ai": is_ai,
                                   "color": color or "#94a3b8", "addr": addr}
            return True

    def start_game(self):
        """역할 배정 → 낮 1일차 개시. 성공 시 True."""
        with self.lock:
            if self.phase != Phase.LOBBY or len(self.players) < MIN_PLAYERS_CORE:
                return False, []
            names = list(self.players)
            assigned = dict(zip(names, alloc_roles(len(names))))
            for n, r in assigned.items():
                self.players[n]["role"] = r
            self.phase = Phase.DAY
            self.day_no = 1
            self.votes.clear()
        return True, assigned

    # ---------- 낮 ----------
    def cast_vote(self, voter, target):
        with self.lock:
            if self.phase != Phase.DAY:
                return False
            if not self.players.get(voter, {}).get("alive"):
                return False
            if target not in self.players or not self.players[target]["alive"]:
                return False
            # 자투 금지 — 문서 3-1.4 (트롤링 방지) & 실제 참가자 명단과의 일치 강제
            if target == voter:
                return False
            self.votes[voter] = target
            return True

    def all_voted(self):
        with self.lock:
            alive = [n for n, p in self.players.items() if p["alive"]]
            return all(n in self.votes or n in self.abstains for n in alive)

    # ---------- 보강: 투표 상태 머신 (동률 재투표 → 최후변론 → 찬반투표) ----------
    def tally_votes_full(self):
        """문서 기획 개표: (mode, data).
        mode: "single"(단독최다 → 최후변론), "revote"(동률 재투표), "none"(무득표/기권뿐)
        data: {"tally": {}, "tied": [names], "top": name}
        v1.56 — 구 단일개표 tally_votes()/execute()는 도달 불가능한 죽은 코드라
        제거(VOTE_FULL_MACHINE이 상수 True로 고정돼 있어 실제로 꺼진 적이 없었음)."""
        with self.lock:
            cnt = {}
            for t in self.votes.values():
                # 사망자·빈 표·명단에 없는 이름은 집계하지 않는다(재투표 중 접속이 끊겨 죽은 사람이 최다득표가 되는 것 방지)
                if not t or not self.players.get(t, {}).get("alive"):
                    continue
                cnt[t] = cnt.get(t, 0) + 1
            max_v = max(cnt.values()) if cnt else 0
            if not cnt or max_v == 0:
                return "none", {"tally": {}, "tied": [], "top": None}
            top = sorted([n for n, v in cnt.items() if v == max_v])
            tally = dict(cnt)
            if len(top) == 1:
                self.voted_history.append(dict(tally))
                return "single", {"tally": tally, "tied": [], "top": top[0]}
            return "revote", {"tally": tally, "tied": top, "top": None}

    def execute_defense(self, name=None):
        """최후 변론 후 찬반 투표. self.defense_yes/{name: True/False/None} 은 UI가 사전 기입
        (None은 기권 — v1.88부터 찬반 어느 쪽으로도 세지 않는다. 예전에는 시한을 넘긴
        미투표자를 전부 명시적 반대(False)로 채워 넣었는데, 그러면 표 하나가 패킷 유실
        때문에 도착하지 않은 것과 '진짜로 반대를 눌렀다'가 결과에서 똑같이 취급돼, 인원이
        적은 판에서는 패킷 한 통이 처형/부결을 뒤집을 수 있었다).
        피고인 본인 투표권 없음. 찬성 > 반대 시 처형."""
        with self.lock:
            target = getattr(self, "defendant", None) or name
            defense_votes = getattr(self, "defense_yes", {})
            yes = sum(1 for voter, v in defense_votes.items() if v is True and voter != target)
            no = sum(1 for voter, v in defense_votes.items() if v is False and voter != target)
            executed = yes > no
            result = "executed" if executed else "acquitted"
            if executed and target and self.players.get(target, {}).get("alive"):
                self.players[target]["alive"] = False
                self.log.append({"phase": "day", "day": self.day_no,
                                  "kind": "execute", "who": target})
            self.defendant = None
            self.defense_yes = {}
            return result, yes, no

    def set_defendant(self, name):
        with self.lock:
            self.defendant = name
            self.defense_yes = {}

    def cast_defense_vote(self, voter, yes):
        with self.lock:
            # v1.13 — 사망자 투표 차단(문서 '사망자 투표권 없음')
            if not self.players.get(voter, {}).get("alive"):
                return False
            if getattr(self, "defendant", None) and voter != self.defendant:
                self.defense_yes[voter] = bool(yes)
                return True
            return False

    def defense_all_voted(self):
        with self.lock:
            voters = [n for n, p in self.players.items()
                      if p["alive"] and n != self.defendant]
            return all(n in self.defense_yes for n in voters)

    # ---------- 밤 ----------
    def set_night_target(self, target):
        """마피아 밤 살해 지목 — 자기 자신은 대상에서 제외."""
        with self.lock:
            if target:
                mafia_names = [n for n, p in self.players.items()
                               if p["role"] == "mafia" and p["alive"]]
                if target in mafia_names:
                    return False        # 마피아 동료/자기 자신 지목 불가
            self.night_target = target
            return True

    def set_night_save(self, target):
        with self.lock:
            self.night_saved = target

    # ---------- 보강: 경찰 조사 / 의사 연속보호금지 / 첫날밤 킬금지 / 기권 ----------
    def police_investigate(self, target):
        """경찰 밤 조사. 결과 'mafia'/'citizen'. 이력 저장 + 본인 통보용 반환.
        스파이(추후 추가 시)도 'citizen' 판정으로 유지할 것."""
        with self.lock:
            me = next((n for n, p in self.players.items()
                       if p["role"] == "police" and p["alive"]), None)
            if not me or self.phase != Phase.NIGHT:
                return None
            if not target or target == me or not self.players.get(target, {}).get("alive"):
                return None
            role = self.players.get(target, {}).get("role")
            if target in self.police_invest:
                result = self.police_invest[target]          # 중복 조사 방지
            else:
                result = "mafia" if role == "mafia" else "citizen"
                self.police_invest[target] = result
            self.police_report = (target, result)
            return result

    def doctor_protect(self, target):
        """의사 밤 보호 — 연속 2회 동일인(자신 포함) 보호 금지. 성공 여부 반환."""
        with self.lock:
            me = next((n for n, p in self.players.items()
                       if p["role"] == "doctor" and p["alive"]), None)
            if not me or self.phase != Phase.NIGHT:
                return False
            if not target or target not in self.players or not self.players[target]["alive"]:
                return False
            if self.last_protect == target:
                return False  # 연속 보호 금지
            self.set_night_save(target)
            return True

    def mafia_night_vote(self, mafia_name, target):
        """밤 마피아 다수 지목 — 개별 수집(합의 실패 판정용). 자기 자신 금지."""
        with self.lock:
            if self.players.get(mafia_name, {}).get("role") != "mafia":
                return False
            if not target or target == mafia_name or not self.players.get(target, {}).get("alive"):
                return False
            if self.players[target]["role"] == "mafia":
                return False            # 마피아 동료 지목 불가 (set_night_target과 같은 기준)
            # 다시 고르면 맨 뒤로 보낸다 — night_targets의 순서가 '각자의 마지막 선택이 빨랐던 순서'가
            # 되어, 인간 마피아가 갈릴 때 "가장 먼저 최종 선택을 끝낸 사람"의 선택이 팀 결정이 된다.
            # (dict는 기존 키를 덮어써도 원래 자리를 지켜서, A가 1초에 고르고 20초에 바꿔도 A가 계속 앞이었다.)
            self.night_targets.pop(mafia_name, None)
            self.night_targets[mafia_name] = target
            return True

    def night_kill_agree(self):
        """다수 마피아 합의 실패/동률 판정. 합의 대상 또는 None."""
        with self.lock:
            t = list(self.night_targets.values())
            if not t:
                return None
            if len(set(t)) == 1:
                return t[0]
            return None  # 갈림 — 킬 무효

    def cast_abstain(self, voter):
        """기권(투표 타임아웃 or 팝업 기권). DAY·VOTE 모두 허용."""
        with self.lock:
            if self.phase in (Phase.DAY, Phase.VOTE) and \
                    self.players.get(voter, {}).get("alive"):
                self.abstains.add(voter)
                return True
            return False

    def enter_night(self):
        with self.lock:
            if self.phase not in (Phase.DAY, Phase.VOTE):
                return False
            self.phase = Phase.NIGHT
            self.night_target = None
            self.night_saved = None
            self.night_targets.clear()  # 비정상 전이(재시작 등)로 resolve_night()를
                                         # 안 거치고 다시 밤에 진입해도 이전 밤의
                                         # 다수 마피아 개별 지목이 남아있지 않도록
            return True

    def resolve_night(self):
        """밤 해결 — 마피아 타깃 vs 의사 세이브 판정. 결과 (died, victim).

        보강(문서 기획): 다수 마피아 합의 실패 시 킬 무효 / 첫날 밤 킬 금지 권고 /
        경찰 조사 결과 이력 누적. 기존 반환 시그니처 유지."""
        with self.lock:
            if self.phase != Phase.NIGHT:
                return False, None
            self.day_no += 1
            self.votes.clear()
            self.abstains.clear()
            victim = None
            # 다수 마피아: night_targets가 채워져 있으면 합의 판정 우선(기존 단일 지목과 병행 호환)
            if self.night_targets:
                agreed = self.night_kill_agree()
                if agreed:
                    self.night_target = agreed
                else:
                    self.night_target = None  # 합의 실패 시 킬 무효
            # 첫날 밤부터 정상 살해 허용(첫날 밤 킬 금지 규칙 없음)
            if (self.night_target
                    and self.players.get(self.night_target, {}).get("alive")):
                if self.night_saved and self.night_target == self.night_saved:
                    pass  # 의사가 살렸다
                else:
                    self.players[self.night_target]["alive"] = False
                    victim = self.night_target
            self.log.append({"phase": "night", "day": self.day_no,
                              "kind": "victim", "who": victim})
            self.last_protect = self.night_saved          # 연속 보호 금지용
            self.night_targets.clear()
            self.phase = Phase.DAY
            return (victim is not None), victim

    # ---------- 승리 판정 ----------
    def check_winner(self):
        with self.lock:
            if self.phase in (Phase.LOBBY, Phase.END):
                return self.winner
            alive_mafia = sum(1 for p in self.players.values()
                              if p["alive"] and p["role"] == "mafia")
            alive_cit = sum(1 for p in self.players.values()
                            if p["alive"] and p["role"] != "mafia")
            if alive_mafia == 0:
                self.phase = Phase.END
                self.winner = "citizen"
            elif alive_mafia >= alive_cit:
                self.phase = Phase.END
                self.winner = "mafia"
            return self.winner

    # ---------- 보강: 유령 채팅방 / 돌연사 / 직업 공개 ----------
    def ghost_room_members(self):
        with self.lock:
            return [n for n, p in self.players.items() if not p["alive"]]

    def sudden_death(self, name):
        """게임 도중 이탈 → 돌연사 처리. 성공 시 (True, role)"""
        with self.lock:
            if self.players.get(name, {}).get("alive"):
                self.players[name]["alive"] = False
                role = self.players[name]["role"]
                self.log.append({"phase": "day", "day": self.day_no,
                                  "kind": "sudden_death", "who": name})
                return True, role
            return False, None

    def reveal_role(self, name):
        """사망(처형/돌연사/밤사망)자의 직업 즉시 공개용."""
        with self.lock:
            p = self.players.get(name)
            if p and not p["alive"]:
                return p["role"]
            return None

    def human_ai_role_names(self):
        """승/패 표시용 — name / role / alive / is_ai 목록."""
        with self.lock:
            return [{"name": n, "role": p["role"], "alive": p["alive"],
                      "is_ai": p["is_ai"], "color": p["color"]}
                     for n, p in self.players.items()]

    def mafias(self):
        with self.lock:
            return [n for n, p in self.players.items()
                    if p["role"] == "mafia" and p["alive"]]

    def alive_players(self):
        with self.lock:
            return [n for n, p in self.players.items() if p["alive"]]
