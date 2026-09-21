# -*- coding: utf-8 -*-
"""내가 보낸 사진/파일은 오른쪽, 상대가 보낸 것은 왼쪽에 그려지는지."""
import argparse, importlib.util, os, shutil, socket, sys
if __name__ != "__main__":
    sys.exit(0)
sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(BASE); sys.path.insert(0, APP)
spec = importlib.util.spec_from_file_location("lan_messenger", os.path.join(APP, "lan_messenger.py"))
lm = importlib.util.module_from_spec(spec)
_rb = socket.socket.bind
socket.socket.bind = lambda s, a: _rb(s, ("127.0.0.1", a[1])) if isinstance(a, tuple) and a[0] in ("", "0.0.0.0") else _rb(s, a)
spec.loader.exec_module(lm)
import tkinter as tk
from PIL import Image

tmp = os.path.join(BASE, "tmp_align"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
png = os.path.join(tmp, "사진.png"); Image.new("RGB", (120, 80), (200, 60, 60)).save(png)
txt = os.path.join(tmp, "보고서.txt"); open(txt, "w", encoding="utf-8").write("x")
root = tk.Tk(); root.geometry("1000x700"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60051, datadir=tmp), [])
app._select(("mgame",)); root.update()
ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


def draw(mine, rec):
    """새로 그려진 항목들의 가로 범위(x1, x2)와 캔버스 폭."""
    before = set(app.chat.find_all())
    app._draw_file_item(mine, rec)
    new = [i for i in app.chat.find_all() if i not in before]
    boxes = [app.chat.bbox(i) for i in new if app.chat.bbox(i)]
    return min(b[0] for b in boxes), max(b[2] for b in boxes), app._chat_width()


for label, rec in (("사진", dict(is_image=True, path=png, fname="사진.png", size=1000, state="done")),
                   ("일반 파일", dict(path=txt, fname="보고서.txt", size=10, state="done"))):
    x1, x2, w = draw(True, rec)
    check(f"내가 보낸 {label}은 오른쪽에 붙음 (우측 끝 {x2:.0f} / 폭 {w})", x2 >= w - 30 and x1 > w / 2 - 80)
    x1, x2, w = draw(False, rec)
    check(f"상대가 보낸 {label}은 왼쪽에 붙음 (좌측 끝 {x1:.0f})", x1 < 80 and x2 < w - 30)
before = set(app.chat.find_all()); app._draw_bubble(True, "내가 쓴 메시지입니다")
b = [app.chat.bbox(i) for i in app.chat.find_all() if i not in before and app.chat.bbox(i)]
check("내가 쓴 텍스트 메시지는 오른쪽", max(x[2] for x in b) >= app._chat_width() - 30)
before = set(app.chat.find_all()); app._draw_sticker_item(True, dict(sticker_id=next(iter(__import__("stickers").STICKERS))))
b = [app.chat.bbox(i) for i in app.chat.find_all() if i not in before and app.chat.bbox(i)]
check("내가 보낸 스티커는 오른쪽", bool(b) and max(x[2] for x in b) >= app._chat_width() - 30)
root.destroy()
print("OWN FILE ALIGN PASSED" if ALL else "OWN FILE ALIGN FAILED")
sys.exit(0 if ALL else 1)
