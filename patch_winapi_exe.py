# -*- coding: utf-8 -*-
"""patch_winapi_exe.py — winapi.py의 4개 app.ico 접근 지점을 exe(frozen) 대응으로 교체.

사용: python patch_winapi_exe.py   (build_exe.bat에서 ico_embed 다음에 자동 호출)
멱등(idempotent): 이미 패치돼 있으면 아무것도 바꾸지 않는다.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(HERE, "winapi.py")


def ensure_imports(src):
    # tempfile import
    if "import tempfile" not in src:
        src = src.replace("import os\n", "import os\nimport tempfile\n", 1)
    # app_dir import
    if "from netutils import app_dir" not in src:
        src = src.replace(
            'from constants import C_SIDEBAR, C_TEXT\n',
            'from constants import C_SIDEBAR, C_TEXT\nfrom netutils import app_dir\n', 1)
    return src


ICO_FALLBACK = '''            try:
                from netutils import ICO_EMBED as _ico_bytes
                if _ico_bytes and not os.path.exists(ico_path):
                    _fd, _tp = tempfile.mkstemp(suffix=".ico", prefix="lantalk_")
                    with os.fdopen(_fd, "wb") as _f:
                        _f.write(_ico_bytes)
                    ico_path = _tp
            except Exception:
                pass
'''

PAT_REGISTRY = (
    'base_dir = os.path.dirname(os.path.abspath(__file__))\n'
    '            ico_path = os.path.join(base_dir, "app.ico")\n'
    '            key_path'
)
REPL_REGISTRY = (
    'base_dir = app_dir()\n'
    '            ico_path = os.path.join(base_dir, "app.ico")\n'
    '{FB}            key_path'
)
PAT_NOTIFIER = (
    'base_dir = os.path.dirname(os.path.abspath(__file__))\n'
    '            ico_path = os.path.join(base_dir, "app.ico")\n'
    '            h_icon = None'
)
REPL_NOTIFIER = (
    'base_dir = app_dir()\n'
    '            ico_path = os.path.join(base_dir, "app.ico")\n'
    '{FB}            h_icon = None'
)
PAT_LNK = (
    'base_dir = os.path.dirname(os.path.abspath(__file__))\n'
    '                bat_path'
)
REPL_LNK = (
    'base_dir = app_dir()\n'
    '                bat_path'
)
PAT_WINDOWICON = (
    'base_dir = os.path.dirname(os.path.abspath(__file__))\n'
    '            ico_path = os.path.join(base_dir, "app.ico")\n'
    '            if os.path.isfile(ico_path):'
)
REPL_WINDOWICON = (
    'base_dir = app_dir()\n'
    '            ico_path = os.path.join(base_dir, "app.ico")\n'
    '            if os.path.isfile(ico_path):'
)


def main():
    src = open(P, encoding="utf-8").read()
    src = ensure_imports(src)

    # (a) AUMID 레지스트리 — ico fallback 추가(멱등 선행체크: ICO_EMBED 사용부 없을 때만)
    if PAT_REGISTRY in src:
        fb = ICO_FALLBACK.replace("\n", "\n" + "        " * 0).replace("            try", "            try", 1)
        src = src.replace(PAT_REGISTRY,
                          REPL_REGISTRY.format(FB=ICO_FALLBACK), 1)
    # (b) Notifier 트레이 h_icon
    if PAT_NOTIFIER in src:
        src = src.replace(PAT_NOTIFIER,
                          REPL_NOTIFIER.format(FB=ICO_FALLBACK), 1)
    # (c) 바로가기 생성
    if PAT_LNK in src:
        src = src.replace(PAT_LNK, REPL_LNK, 1)
    # (d) 창 아이콘
    if PAT_WINDOWICON in src:
        src = src.replace(PAT_WINDOWICON, REPL_WINDOWICON, 1)

    open(P, "w", encoding="utf-8").write(src)

    # 검증 출력
    ok = all(k in src for k in (
        "from netutils import app_dir",
        "import tempfile",
        "ICO_EMBED as _ico_bytes",
    )) and "os.path.dirname(os.path.abspath(__file__))" not in src
    print("winapi.py exe 패치:", "OK" if ok else "REVIEW (남은 __file__ 접근 있음)")
    if not ok:
        for i, line in enumerate(src.splitlines(), 1):
            if "os.path.dirname(os.path.abspath(__file__))" in line:
                print(f"  line {i}: {line.strip()}")


if __name__ == "__main__":
    main()
