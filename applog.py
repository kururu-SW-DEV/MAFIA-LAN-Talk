# -*- coding: utf-8 -*-
"""
applog.py — 조용히 삼켜지던 예외를 파일로 남기는 최소 로깅 유틸.

배경: 기존 코드는 보안 프로그램 간섭으로 앱이 죽지 않도록 네트워크/파일 IO 대부분을
`except Exception: pass`로 감쌌다. 그 대가로 "전송이 왜 실패했는지" 현장에서 전혀
알 수 없었다. 이 모듈은 앱을 죽이는 대신, 실패를 조용히 로그 파일에만 남긴다
(그래서 기존의 "절대 안 죽는다"는 설계 원칙은 그대로 유지된다).

기본은 항상 조용히 파일에 남기고(사용자에게 노출 안 됨), 파일이 무한히 커지지
않도록 500KB를 넘으면 스스로 잘라낸다.
"""
import datetime
import os
import threading

_lock = threading.Lock()
_log_path = None
_MAX_BYTES = 500 * 1024


def init(datadir):
    """Engine이 datadir을 알게 되는 시점에 1회 호출 — 이후 log()가 이 파일에 기록된다."""
    global _log_path
    try:
        os.makedirs(datadir, exist_ok=True)
    except OSError:
        pass
    _log_path = os.path.join(datadir, "debug.log")


def log(tag, exc=None, detail=""):
    """실패 지점 하나를 한 줄로 남긴다. exc가 있으면 예외 타입/메시지도 함께."""
    if _log_path is None:
        return
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts = [ts, tag]
        if detail:
            parts.append(detail)
        if exc is not None:
            parts.append(f"{type(exc).__name__}: {exc}")
        line = " | ".join(parts) + "\n"
        with _lock:
            try:
                if os.path.exists(_log_path) and os.path.getsize(_log_path) > _MAX_BYTES:
                    # 전체 삭제 대신 최근 절반만 남긴다 — 로테이션이 걸리는 순간이야말로
                    # 방금 쌓인 오류들이 가장 중요한 시점이라, 통째로 지우면 그걸 잃는다.
                    with open(_log_path, "rb") as f:
                        f.seek(-(_MAX_BYTES // 2), os.SEEK_END)
                        tail = f.read()
                    nl = tail.find(b"\n")
                    if nl != -1:
                        tail = tail[nl + 1:]
                    with open(_log_path, "wb") as f:
                        f.write(tail)
            except OSError:
                pass
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass  # 로깅 자체가 앱을 죽이는 일은 절대 없어야 한다


_swallowed_seen = set()


def swallowed(exc):
    """`except Exception: pass`로 조용히 삼키던 예외를 기록한다(동작은 그대로, 흔적만 남긴다).
    예외를 삼킨 위치(파일:줄·함수)를 자동으로 붙이고, 같은 위치·같은 예외 종류는 한 번만 남겨
    화면 갱신 같은 반복 경로에서도 로그가 넘치지 않게 한다. 기록 실패는 무시한다."""
    try:
        import sys
        fr = sys._getframe(1)
        site = (fr.f_code.co_filename, fr.f_lineno, type(exc).__name__)
        with _lock:
            if site in _swallowed_seen:
                return
            _swallowed_seen.add(site)
        log("swallowed", exc=exc,
            detail=f"{os.path.basename(site[0])}:{site[1]} {fr.f_code.co_name}")
    except Exception:
        pass


def start_hang_watchdog(root, path, stall_sec=10.0):
    """화면이 멈추면(메인 스레드가 stall_sec초 넘게 응답 없음) 모든 스레드의 호출 스택을 path에 남긴다.
    '응답 없음' 강제 종료는 파이썬 예외가 없어 debug.log에 아무것도 안 남기 때문에, 어디서 멈췄는지
    알 방법이 이것뿐이다. 프로세스가 네이티브 오류로 죽을 때의 스택도 같은 파일에 남긴다."""
    import faulthandler
    import threading
    import time
    try:
        f = open(path, "a", buffering=1, encoding="utf-8")
        faulthandler.enable(file=f, all_threads=True)
    except Exception as exc:
        swallowed(exc)
        return None
    beat = {"t": time.time(), "dumped": False}

    def _tick():
        beat["t"] = time.time()
        beat["dumped"] = False
        try:
            root.after(1000, _tick)
        except Exception as exc:
            swallowed(exc)

    def _watch():
        while True:
            time.sleep(2.0)
            if time.time() - beat["t"] > stall_sec and not beat["dumped"]:
                beat["dumped"] = True
                try:
                    f.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} 화면 멈춤 "
                            f"({time.time() - beat['t']:.0f}초 무응답) — 전체 스레드 스택 =====\n")
                    faulthandler.dump_traceback(file=f, all_threads=True)
                    log("ui_hang", detail=f"{time.time() - beat['t']:.0f}s → {path}")
                except Exception as exc:
                    swallowed(exc)

    try:
        root.after(1000, _tick)
        threading.Thread(target=_watch, daemon=True, name="hang-watchdog").start()
    except Exception as exc:
        swallowed(exc)
    return f
