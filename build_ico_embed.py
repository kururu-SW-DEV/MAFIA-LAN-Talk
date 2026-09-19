# -*- coding: utf-8 -*-
"""build_ico_embed.py — netutils.py의 ICO_EMBED 자리에 app.ico base64를 채워 넣는다.

사용: python build_ico_embed.py   (build_exe.bat에서 빌드 직전에 자동 호출)
Pillow/vendored PIL과 무관 — 순수 표준 라이브러리만 사용.
"""
import base64
import os

HERE = os.path.dirname(os.path.abspath(__file__))
NET = os.path.join(HERE, "netutils.py")
ICO = os.path.join(HERE, "app.ico")


def main():
    ico_b = open(ICO, "rb").read()
    b64 = base64.b64encode(ico_b).decode("ascii")
    lines = ['    "' + b64[i:i + 100] + '"' for i in range(0, len(b64), 100)]
    block = "ICO_EMBED = (\n" + "\n".join(lines) + "\n)"

    src = open(NET, encoding="utf-8").read()
    if "ICO_EMBED = (" in src:
        # 이미 채워져 있으면 교체 (app.ico 크기 변경 반영)
        start = src.index("ICO_EMBED = (")
        # 다음 빈 줄 뒤까지가 블록 — 단순 정규로 처리
        import re
        src, n = re.subn(r"ICO_EMBED = \(.*?\)", block, src, count=1, flags=re.S)
        assert n == 1, "ICO_EMBED block replace failed"
    else:
        # build_new fail-safe: 자리표시자 교체
        marker = "ICO_EMBED = None"
        assert marker in src, "ICO_EMBED placeholder missing in netutils.py"
        src = src.replace(marker, block, 1)
    open(NET, "w", encoding="utf-8").write(src)
    print(f"ICO_EMBED embedded: {len(b64)} chars (app.ico {len(ico_b)} bytes)")


if __name__ == "__main__":
    main()
