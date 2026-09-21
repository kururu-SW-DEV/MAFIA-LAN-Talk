# -*- coding: utf-8 -*-
"""test_tk_thread_deadlock.py — 다른 스레드의 GC가 PhotoImage 소멸자를 불러도 UI가 교착되지 않는지.

hang_trace.log의 실제 멈춤을 그대로 재현한다: 메인 스레드는 이벤트 루프 안의 콜백에서 새 스레드를 기다리고(=Tk
호출을 처리하지 못함), 그 스레드가 가비지 수집으로 ImageTk.PhotoImage.__del__(Tk 호출)을 실행한다.
"""
import gc
import os
import sys
import threading
import time

if __name__ != "__main__":
    sys.exit(0)
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from PIL import Image, ImageTk

INSTALL = os.environ.get("TK_SAFE", "1") == "1"
ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


root = tk.Tk(); root.withdraw()
if INSTALL:
    import tk_thread_safe
    tk_thread_safe.install(root, interval_ms=100)

result = {}


def make_garbage():
    """순환 참조 안의 PhotoImage — 참조 카운트로는 안 지워지고 GC가 지운다(실제 앱의 이미지 캐시와 같은 모양)."""
    holder = {}
    holder["self"] = holder
    holder["img"] = ImageTk.PhotoImage(Image.new("RGB", (8, 8), (255, 0, 0)), master=root)
    holder["var"] = tk.StringVar(master=root, value="x")
    result["name"] = str(holder["img"])
    result["vname"] = str(holder["var"])


def in_mainloop():
    make_garbage()

    def worker():
        gc.collect()                       # 이 스레드에서 소멸자가 실행된다
        result["worker_done"] = True

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(4.0)                            # 메인 스레드가 이벤트 루프를 못 도는 상태로 기다린다(= Thread.start()의 _started.wait())
    result["joined"] = not t.is_alive()
    root.after(50, root.quit)


root.after(50, in_mainloop)
root.mainloop()

check("워커 스레드의 GC가 끝남(교착 없음)", result.get("joined") and result.get("worker_done"))
if INSTALL:
    for _ in range(30):                    # 메인 스레드 정리 작업이 이미지를 실제로 지운다
        root.update(); time.sleep(0.05)
    names = root.tk.call("image", "names")
    check("지연된 정리가 메인 스레드에서 이미지를 삭제함", result["name"] not in [str(n) for n in names])
    check("지연 목록이 비워짐", len(tk_thread_safe._pending) == 0)
root.destroy()
print("TK THREAD DEADLOCK", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
