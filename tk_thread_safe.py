# -*- coding: utf-8 -*-
"""tk_thread_safe.py — 다른 스레드에서 실행되는 Tk 객체 소멸자(__del__)가 UI를 교착시키지 않게 한다.

증상(실측, hang_trace.log): 화면이 12초 넘게 멈춤(응답 없음). 두 번 모두 같은 모양이었다.
  · 메인 스레드: 밤 시작 → 메시지 전송 → `Thread.start()`가 새 스레드의 시작 신호(`_started`)를 기다림
  · 새 스레드: `_started.set()`을 부르는 중 파이썬 순환 가비지 수집(GC)이 돌았고, 여기서 정리되는
    `ImageTk.PhotoImage.__del__` → `tk.call("image", "delete", ...)`를 **메인이 아닌 스레드**에서 실행
Tk는 다른 스레드의 호출을 메인 스레드 이벤트 루프가 처리해 주길 기다린다. 그런데 메인 스레드는 그 새 스레드를
기다리는 중이라 서로 영원히 기다린다(교착). PhotoImage·tkinter.Image·Variable 소멸자가 모두 같은 위험이다.

해결: 이 소멸자들이 메인 스레드가 아닌 곳에서 불리면 그 자리에서 Tk를 건드리지 않고 목록에 넣어 두었다가,
메인 스레드의 주기 작업이 대신 실행한다(객체는 그때까지 살려 둔다).
"""
import collections
import threading
import tkinter
import tkinter.font

_pending = collections.deque()
_installed = False
_MAIN = threading.main_thread()


def _wrap(cls):
    orig = cls.__dict__.get("__del__")
    if orig is None or getattr(orig, "_tk_thread_safe", False):
        return

    def safe_del(self, _orig=orig):
        if threading.current_thread() is _MAIN:
            return _orig(self)
        _pending.append((_orig, self))          # 메인 스레드에서 나중에 정리 — 여기서는 Tk를 부르지 않는다

    safe_del._tk_thread_safe = True
    cls.__del__ = safe_del


def drain():
    """대기 중인 소멸 작업을 실행한다(메인 스레드에서만 호출)."""
    n = 0
    while _pending and n < 500:
        fn, obj = _pending.popleft()
        n += 1
        try:
            fn(obj)
        except Exception:
            pass                                # 창이 이미 닫힌 경우 등 — 정리 실패는 무시
    return n


def install(root, interval_ms=300):
    """앱 시작 시 한 번 호출한다. 소멸자를 안전한 버전으로 바꾸고 메인 스레드에서 주기적으로 정리한다."""
    global _installed
    if not _installed:
        _installed = True
        for cls in (tkinter.Image, tkinter.Variable, tkinter.font.Font):
            _wrap(cls)
        try:
            from PIL import ImageTk
            for cls in (ImageTk.PhotoImage, ImageTk.BitmapImage):
                _wrap(cls)
        except Exception:
            pass

    def _tick():
        drain()
        try:
            root.after(interval_ms, _tick)
        except tkinter.TclError:
            pass                                # 창이 닫힘 — 더 예약하지 않는다

    root.after(interval_ms, _tick)
