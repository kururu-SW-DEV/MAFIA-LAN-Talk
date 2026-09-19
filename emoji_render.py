# -*- coding: utf-8 -*-
"""컬러 이모지 렌더링 유틸.

Tkinter 위젯(Label/Button)은 텍스트를 항상 fg 색 하나로만 그리기 때문에,
Windows의 컬러 이모지 폰트(Segoe UI Emoji)를 넣어도 단색으로 뭉개져 나온다
(Tk 자체의 렌더링 한계 — widgets.py의 EmojiPicker 관련 주석 참고).

이 모듈은 문자열을 이모지/일반 텍스트 구간으로 나눠 PIL로 각각 알맞은
폰트로 그린 뒤(이모지는 embedded_color=True로 색을 살려서) 투명 배경
RGBA 이미지 한 장으로 합성한다. 위젯 배경색과 상관없이 투명하게 얹히므로
호버 시 bg가 바뀌어도 그대로 자연스럽게 보인다.
"""
import os
import re
import tkinter as tk

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002300-\U000023FF"  # \u231A\u23F0\u23F3 \uB4F1 \uAE30\uD0C0 \uAE30\uC220 \uAE30\uD638 \uBE14\uB85D(\uC2DC\uACC4\uB958 \uC774\uBAA8\uC9C0\uAC00 \uC5EC\uAE30 \uC788\uC74C)
    "\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF"
    "\u200D"
    "\uFE0F"
    "]+"
)

_EMOJI_FONT_PATH = "C:/Windows/Fonts/seguiemj.ttf"
FONT_PATH_REGULAR = "C:/Windows/Fonts/malgun.ttf"
FONT_PATH_BOLD = "C:/Windows/Fonts/malgunbd.ttf"
_FONT_CACHE = {}
_emoji_font_ok = None  # None=미확인, True/False=확인됨


def _load_font(path, size):
    key = (path, size)
    f = _FONT_CACHE.get(key)
    if f is None:
        from PIL import ImageFont
        try:
            f = ImageFont.truetype(path, size)
        except Exception:
            try:
                f = ImageFont.load_default()
            except Exception:
                f = None
        _FONT_CACHE[key] = f
    return f


def emoji_font_available():
    """이 환경에서 컬러 이모지 폰트를 쓸 수 있는지(대개 Windows 전용)."""
    global _emoji_font_ok
    if _emoji_font_ok is None:
        try:
            _load_font(_EMOJI_FONT_PATH, 24)
            _emoji_font_ok = os.path.exists(_EMOJI_FONT_PATH)
        except Exception:
            _emoji_font_ok = False
    return _emoji_font_ok


def _split_segments(text):
    """문자열을 (이모지 여부, 조각) 리스트로 나눈다."""
    segs = []
    pos = 0
    for m in _EMOJI_RE.finditer(text):
        if m.start() > pos:
            segs.append((False, text[pos:m.start()]))
        segs.append((True, m.group()))
        pos = m.end()
    if pos < len(text):
        segs.append((False, text[pos:]))
    return segs


def has_emoji(text):
    return bool(_EMOJI_RE.search(text or ""))


def render_mixed_text(text, text_font_path, text_size, fg="#ffffff", emoji_px=None):
    """이모지 포함 문자열을 투명 배경 RGBA PIL.Image로 래스터화한다.
    이모지 폰트가 없는 환경이면 None을 반환한다(호출부는 평범한 text=로 폴백)."""
    if not text or not emoji_font_available():
        return None
    from PIL import Image, ImageDraw

    emoji_px = emoji_px or max(text_size + 6, 16)
    segs = _split_segments(text)
    text_font = _load_font(text_font_path, text_size)
    emoji_font = _load_font(_EMOJI_FONT_PATH, emoji_px)

    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)

    parts = []
    total_w = 0
    max_h = 1
    for is_emoji, seg in segs:
        if not seg:
            continue
        font = emoji_font if is_emoji else text_font
        kw = {"embedded_color": True} if is_emoji else {}
        bbox = d.textbbox((0, 0), seg, font=font, **kw)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        parts.append((is_emoji, seg, w, bbox))
        total_w += w + (2 if is_emoji else 0)
        max_h = max(max_h, h)

    # 글리프 bbox에 딱 맞춰 자르면 버튼 안에서 여백 없이 빡빡해 보이므로,
    # 이미지 자체에 약간의 여유를 둔다(바깥쪽 padx/pady와는 별개로, 이모지와
    # 텍스트 사이 최소 여백을 보장).
    pad_x, pad_y = 3, 3
    img = Image.new("RGBA", (max(total_w, 1) + pad_x * 2, max_h + pad_y * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = pad_x
    for is_emoji, seg, w, bbox in parts:
        y = (img.height - (bbox[3] - bbox[1])) // 2 - bbox[1]
        if is_emoji:
            d.text((x - bbox[0], y), seg, font=emoji_font, embedded_color=True)
            x += w + 2
        else:
            d.text((x - bbox[0], y), seg, font=text_font, fill=fg)
            x += w
    return img


def to_photoimage(img):
    from PIL import ImageTk
    return ImageTk.PhotoImage(img)


def _plain_text_image(text, font_path, size, fg):
    """이모지 폰트가 없는 환경(비-Windows)용 최소 폴백 — 색은 못 살려도
    최소한 렌더링은 되게 한다."""
    from PIL import Image, ImageDraw
    font = _load_font(font_path, size)
    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    img = Image.new("RGBA", (max(w, 1), max(h, 1)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((-bbox[0], -bbox[1]), text, font=font, fill=fg)
    return img


def _rounded_mask(w, h, radius, scale=4):
    """모서리가 계단져 보이지 않도록 4배 크기로 그린 뒤 축소(안티에일리어싱)."""
    from PIL import Image, ImageDraw
    w, h = max(w, 1), max(h, 1)
    big = Image.new("L", (w * scale, h * scale), 0)
    r = max(min(radius, w // 2, h // 2) * scale, 1)
    ImageDraw.Draw(big).rounded_rectangle([0, 0, w * scale - 1, h * scale - 1], radius=r, fill=255)
    return big.resize((w, h), Image.LANCZOS)


def measure_content_height(text_font_path, text_size, sample_texts, emoji_px=None):
    """이모지 글리프는 폰트 포인트 크기와 무관하게 실제 비트맵 높이가 문자마다
    들쭉날쭉해서(예: 📢 vs ⚙ vs 🃏), 공식으로 어림잡지 않고 실제 쓰일 문구들을
    전부 한 번씩 그려 보고 그중 가장 큰 높이를 구한다. 같은 게임바의 버튼들에
    이 값을 min_content_h로 공통 적용하면 어떤 아이콘이 오든 키가 맞는다."""
    best = 0
    for t in sample_texts:
        img = render_mixed_text(t, text_font_path, text_size, fg="#ffffff", emoji_px=emoji_px)
        if img is not None:
            best = max(best, img.height)
    return best


def render_pill(text, text_font_path, text_size, fg, fill_color, radius=10,
                 pad_x=14, pad_y=8, emoji_px=None, min_content_h=None, min_w=None):
    """이모지 섞인 문자열을 배경색이 채워진 둥근 사각형('알약') 위에 그려
    RGBA 이미지로 반환한다. Tk 버튼 자체는 각진 사각형만 그릴 수 있어서,
    버튼처럼 배경색이 있는 위젯의 모서리를 둥글게 보이려면 배경까지 포함해
    통째로 이미지로 그려 얹는 수밖에 없다.

    min_content_h — 이모지가 없는 문자열(예: "[게임 진행 중]")은 이모지가
    있는 옆 버튼보다 글자 높이가 낮아서 그대로 두면 같은 줄에서 유독 작고
    낮아 보인다(실측 지적). 이모지 아이콘 높이만큼 최소 높이를 보장해
    같은 게임바의 버튼들 키를 맞춘다.

    min_w — 그리드 배치 등에서 버튼 최소 너비를 보장하여 문구 길이가 달라도
    동일한 너비와 중앙 정렬을 유지한다."""
    from PIL import Image
    content = render_mixed_text(text, text_font_path, text_size, fg=fg, emoji_px=emoji_px)
    if content is None:
        content = _plain_text_image(text, text_font_path, text_size, fg)
    content_h = max(content.height, min_content_h or 0)
    w = max(content.width + pad_x * 2, min_w or 0)
    h = content_h + pad_y * 2
    mask = _rounded_mask(w, h, radius)
    pill = Image.new("RGBA", (max(w, 1), max(h, 1)), fill_color)
    pill.putalpha(mask)
    x_off = (w - content.width) // 2
    y_off = pad_y + (content_h - content.height) // 2
    pill.alpha_composite(content, (x_off, y_off))
    return pill


def enable_pill_button(widget, font_path, font_size, radius=10, pad_x=14, pad_y=8,
                       emoji_px=None, min_content_h=None, min_w=None, hover_bg=None):
    """버튼을 둥근 모서리 '알약' 스타일로 다시 그린다.

    Tk 버튼은 항상 각진 사각형이라, 모서리를 둥글게 보이려면 실제 위젯의
    배경은 부모(마피아 게임바)의 배경색과 항상 똑같이 맞춰 눈에 안 띄게
    만들고, 그 위에 '배경색+글자'를 통째로 그린 둥근 사각형 이미지를
    얹는다 — 그래서 이미지 바깥 네 귀퉁이는 부모 배경에 자연스럽게 녹아든다.

    기존 코드가 하던 `.config(text=..., bg=..., state=...)` 호출은 그대로
    두고, 이 함수가 .config/.configure를 감싸서 text→새 문구, bg→'알약'
    색, state→비활성 시 회색 처리로 자동 반영한다(호출부 수정 불필요)."""
    orig_configure = widget.configure
    orig_cget = widget.cget
    _eff_emoji_px = emoji_px or max(font_size + 6, 16)
    _min_h = _eff_emoji_px if min_content_h is None else min_content_h
    base_bg = widget.cget("bg")
    st = {
        "text": widget.cget("text") or "",
        "fill": base_bg,
        "base_fill": base_bg,
        "disabled": (str(widget.cget("state")) == "disabled"),
        "command": widget.cget("command"),
        "keepalive": [],
    }

    def _redraw():
        try:
            pbg = widget.master.cget("bg")
        except Exception:
            pbg = st["fill"]
        try:
            # state는 항상 normal로 고정 — 실제로 disabled를 걸면 Tk가 이미지
            # 위에 제 알아서 사각형 격자무늬(stipple)를 덧씌워서 애써 그린
            # 둥근 모서리가 네모난 반투명 베일에 가려져 버린다(실측 지적).
            # 클릭 차단은 state 대신 command를 비워서 대신한다.
            orig_configure(bg=pbg, activebackground=pbg, highlightbackground=pbg, state="normal")
        except Exception:
            pass
        fill = "#4b5563" if st["disabled"] else st["fill"]
        try:
            fg = widget.cget("fg") or "white"
        except Exception:
            fg = "white"
        img = render_pill(st["text"], font_path, font_size, fg=fg, fill_color=fill,
                           radius=radius, pad_x=pad_x, pad_y=pad_y, emoji_px=emoji_px,
                           min_content_h=_min_h, min_w=min_w)
        photo = to_photoimage(img)
        st["keepalive"].append(photo)
        if len(st["keepalive"]) > 6:
            del st["keepalive"][0]
        orig_configure(image=photo, text="", compound="center")

    def configure(cnf=None, **kw):
        merged = dict(cnf) if cnf else {}
        merged.update(kw)
        changed = False
        if "state" in merged:
            new_state = merged.pop("state")
            was_disabled = st["disabled"]
            st["disabled"] = (str(new_state) == "disabled")
            if st["disabled"] and not was_disabled:
                st["command"] = widget.cget("command")
                orig_configure(command="")
            elif was_disabled and not st["disabled"]:
                orig_configure(command=st["command"])
            changed = True
        if "text" in merged:
            st["text"] = merged.pop("text")
            changed = True
        for bgkey in ("bg", "background"):
            if bgkey in merged:
                newbg = merged.pop(bgkey)
                st["base_fill"] = newbg
                # 비활성 중에는 호버 바인딩(<Enter>가 bg=hover색으로 바꾸려는
                # 시도)이 여전히 걸려 있어도 무시 — 안 그러면 회색이어야 할
                # 버튼이 마우스를 올리는 순간 원래 색으로 되돌아가 버린다.
                if not st["disabled"]:
                    st["fill"] = newbg
                    changed = True
        merged.pop("activebackground", None)
        merged.pop("activeforeground", None)
        if "command" in merged:
            # 비활성 중에는 실제 command를 항상 빈 문자열로 유지해야 클릭이
            # 막힌다(state는 항상 normal로 고정해두므로) — 외부에서 비활성
            # 중에 새 command로 재설정하면 그대로 orig_configure에 흘려보내
            # 클릭이 몰래 다시 살아나는 구멍이 있었다.
            new_cmd = merged.pop("command")
            st["command"] = new_cmd
            if not st["disabled"]:
                merged["command"] = new_cmd
        if "fg" in merged:
            changed = True
        if merged:
            orig_configure(**merged)
        if changed:
            _redraw()
        return None

    if hover_bg:
        def _on_enter(e):
            if not st["disabled"]:
                st["fill"] = hover_bg
                _redraw()
        def _on_leave(e):
            if not st["disabled"]:
                st["fill"] = st["base_fill"]
                _redraw()
        widget.bind("<Enter>", _on_enter, add="+")
        widget.bind("<Leave>", _on_leave, add="+")

    def cget(key):
        if key == "text":
            return st["text"]
        if key == "state":
            return "disabled" if st["disabled"] else "normal"
        if key == "command":
            return st["command"]
        return orig_cget(key)

    widget.configure = configure
    widget.config = configure
    widget.cget = cget
    widget.__getitem__ = cget
    try:
        widget.__class__ = type(widget.__class__.__name__ + "_Pill", (widget.__class__,), {
            "__getitem__": lambda self, k: self.cget(k)
        })
    except Exception:
        pass
    widget._pill_redraw = _redraw  # 텍스트/색은 그대로 두고 재도색만 하고 싶을 때
    _redraw()


def make_pill_button(parent, text, cmd, bg, fg, hover_bg=None,
                     font_path=FONT_PATH_REGULAR, font_size=10,
                     radius=8, pad_x=12, pad_y=5, min_w=None, min_h=None,
                     state="normal"):
    """둥근 모서리 알약형 버튼을 생성하고 enable_pill_button을 적용해 반환한다."""
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                  state=state, relief="flat", bd=0, highlightthickness=0,
                  cursor="hand2")
    enable_pill_button(b, font_path, font_size, radius=radius,
                       pad_x=pad_x, pad_y=pad_y, min_content_h=min_h,
                       min_w=min_w, hover_bg=hover_bg)
    return b


def apply(widget, font):
    """이미 만들어진 Label/Button 하나를 즉석에서 컬러 이모지로 다시 그린다.
    font는 tk 폰트 튜플 (family, size[, "bold"...]) — 굵기만 보고 맑은고딕
    일반/굵게 파일을 고른다. 한 줄이면 되므로 매번 생성되는 팝업/배너처럼
    enable_color_emoji + 재설정을 매번 손으로 반복하기 번거로운 곳에 쓴다."""
    size = font[1] if len(font) > 1 else 10
    bold = len(font) > 2 and "bold" in font[2]
    path = FONT_PATH_BOLD if bold else FONT_PATH_REGULAR
    px = round(size * 96 / 72)
    enable_color_emoji(widget, path, px)
    widget.config(text=widget.cget("text"))


def enable_color_emoji(widget, font_path, font_size):
    """위젯의 .config/.configure를 감싸서, text= 로 설정되는 문자열에
    이모지가 있으면 자동으로 컬러 이미지로 바꿔치기한다. 나머지 옵션(bg,
    state 등)은 원래대로 tk에 그대로 전달되므로 호출부 코드는 손댈 필요가
    없다."""
    orig_configure = widget.configure
    orig_cget = widget.cget
    keepalive = []
    current_text = [widget.cget("text") or ""]

    def _current_fg(explicit_fg, disabled):
        if explicit_fg:
            return explicit_fg
        if disabled:
            try:
                return widget.cget("disabledforeground") or widget.cget("fg")
            except Exception:
                pass
        try:
            return widget.cget("fg")
        except Exception:
            return "#ffffff"

    def configure(cnf=None, **kw):
        merged = dict(cnf) if cnf else {}
        merged.update(kw)
        text = merged.pop("text", None)
        if text is not None:
            current_text[0] = text
        if merged:
            orig_configure(**merged)
        if text is None:
            return None
        disabled = merged.get("state") == "disabled"
        if has_emoji(text):
            fg = _current_fg(merged.get("fg"), disabled)
            img = render_mixed_text(text, font_path, font_size, fg=fg)
        else:
            img = None
        if img is not None:
            photo = to_photoimage(img)
            keepalive.append(photo)
            if len(keepalive) > 6:
                del keepalive[0]
            orig_configure(image=photo, text="", compound="center")
        else:
            orig_configure(image="", text=text, compound="none")
        return None

    def cget(key):
        if key == "text":
            return current_text[0]
        return orig_cget(key)

    widget.configure = configure
    widget.config = configure
    widget.cget = cget
    widget.__getitem__ = cget
    try:
        widget.__class__ = type(widget.__class__.__name__ + "_ColorEmoji", (widget.__class__,), {
            "__getitem__": lambda self, k: self.cget(k)
        })
    except Exception:
        pass

