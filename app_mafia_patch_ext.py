# -*- coding: utf-8 -*-
"""mafia_ui_patch.py — app.py에 게임방 통합 패치를 재적용(유실 복구용).

app.py가 랜톡 6.48 원본으로 되돌아가거나 새 버전으로 교체되면 이것을 실행하면 된다:
python app_mafia_patch.py   (이미 적용이면 건너뜀)
"""

# 아래 확장 패치(게임방 통합 — 트하는 12건 전체 수록):

# 나몬지 8건은 app_mafia_patch.py에서 이미 처리.
# 여기서는 6.48 유실시 필수 재적용 남은 8건(_select/ _send/ _update_row/그리고 나머지)을
# 한 곳에 정리해 둔다. app_mafia_patch.py가 실행된 뒤 실행하면 된다.
import os
import re

P = r"D:\DEV\MAFIA\app.py"


def apply():
    src = open(P, encoding="utf-8").read()
    changed = []

    # 1) _select에 mgame 분기 — _select_mafia_room 호출
    if "def _select(self" in src and "_select_mafia_room" not in src:
        m = re.search(r"    def _select\(self, key\):\n", src)
        assert m, "_select anchor"
        src = src.replace(m.group(0), m.group(0) +
            "        if key[0] == \"mgame\":\n"
            "            self._select_mafia_room(key)\n"
            "            return\n", 1)
        changed.append("_select mgame branch")

    # 2) _send 라우팅 — 현재 방이 게임방이면 _mafia_handle_user_text로 전환
    m2 = re.search(r"    def _send\(self.*?, event=None\):\n", src)
    if m2 and "_mafia_handle_user_text" not in src[m2.start():m2.start()+700]:
        # _send 함수 몸통의 시작 몇 줄 안에 첫 if 로 새 분기를 놓는다
        src = src.replace(m2.group(0), m2.group(0) +
            "        if getattr(self, \"current\", None) and self.current[0] == \"mgame\":\n"
            "            text = self.entry.get().strip()\n"
            "            if text:\n"
            "                self._mafia_handle_user_text(text)\n"
            "                self.entry.delete(0, \"end\")\n"
            "            return\n", 1)
        changed.append("_send mgame route")

    # 3) _refresh_list의 mgame inject
    if "_mafia_pump_list_inject" not in src:
        m3 = re.search(r"        items = \[\]\n", src)
        assert m3, "items anchor"
        src = src.replace(m3.group(0), m3.group(0) +
            "        if self._tab == \"chat\" and not (self.search_var.get() or \"\").strip():\n"
            "            self._mafia_pump_list_inject(items)\n", 1)
        changed.append("_refresh_list mgame inject")

    # 4) _update_row mgame
    if "def _update_row" in src:
        m4 = re.search(r"    def _update_row\(self, row_ref, it\):\n", src)
        if m4 and "GAME_ROOM_NAME" not in src[m4.start():m4.start() + 1200]:
            src = src.replace(m4.group(0), m4.group(0) +
                "        if it[\"kind\"] == \"mgame\":\n"
                "            name = GAME_ROOM_NAME\n"
                "            bg0 = C_ROWSEL if selected else C_SIDEBAR\n"
                "            if row_ref.get(\"_last_bg\") != bg0:\n"
                "                row_ref[\"_last_bg\"] = bg0\n"
                "                for w in (row_ref[\"frame\"], row_ref[\"av\"], row_ref[\"mid\"],\n"
                "                         row_ref[\"name\"], row_ref[\"prev\"], row_ref[\"right\"]):\n"
                "                    w.config(bg=bg0)\n"
                "            av_state = (\"mgame\", bg0)\n"
                "            if row_ref.get(\"_av_state\") != av_state:\n"
                "                row_ref[\"_av_state\"] = av_state\n"
                "                self._avatar(row_ref[\"av\"], \"🎭\", \"#b91c1c\")\n"
                "            row_ref[\"name\"].config(text=name[:17], fg=\"#fca5a5\", font=FONT_NAME)\n"
                "            prev_txt = \"AI 사회자와 함께하는 마피아 게임\"\n"
                "            if row_ref.get(\"_last_prev\") != prev_txt:\n"
                "                row_ref[\"_last_prev\"] = prev_txt\n"
                "                row_ref[\"prev\"].config(text=prev_txt)\n"
                "            return\n", 1)
            changed.append("_update_row mgame")

    # 5) _update_pin_banner mgame (게임방에선 notice 억제)
    if "_update_pin_banner" in src:
        m5 = re.search(r"    def _update_pin_banner\(self\):\n", src)
        if m5 and "mgame" not in src[m5.start():m5.start() + 600]:
            src = src.replace(m5.group(0), m5.group(0) +
                "        if getattr(self, \"current\", None) and self.current[0] == \"mgame\":\n"
                "            notice = None\n", 1)
            changed.append("_update_pin_banner mgame")

    open(P, "w", encoding="utf-8").write(src)
    print("EXTENDED PATCH:", changed)
    import py_compile
    py_compile.compile(P, doraise=True)
    print("COMPILE OK")


if __name__ == "__main__":
    apply()
