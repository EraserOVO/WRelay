"""Render filtered forward content into WRelay PNG cards off the event loop."""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from io import BytesIO
import math
import os
from pathlib import Path
import re
from threading import get_ident
from time import monotonic
from urllib.parse import urlsplit

from loguru import logger

from plugins.json_io import run_in_thread_to_completion
from plugins.paths import PROJECT_ROOT


WIDTH, PAD, RADIUS = 760, 33, 18
CONTENT = WIDTH - 2 * PAD
MAX_PAGE_HEIGHT = 2560
MAX_PAGES = 6
MAX_PNG_BYTES = 6 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
AVATAR_GAP = 13
AVATAR_HOSTS = {
    "Discord": ("cdn.discordapp.com", "media.discordapp.net"),
    "X": ("pbs.twimg.com",),
    "Bilibili": ("hdslb.com",),
    "YouTube": ("ytimg.com", "ggpht.com", "googleusercontent.com"),
    "QQ": ("qlogo.cn",),
    "Steam": ("steamstatic.com",),
}
_avatar_cache = OrderedDict()
_render_slots = asyncio.Semaphore(2)


@dataclass(frozen=True)
class CardSource:
    platform: str
    author: str
    detail: str = ""
    url: str = ""
    avatar_url: str = ""


@dataclass(frozen=True)
class Palette:
    surface: str
    panel: str
    border: str
    ink: str
    muted: str
    accent: str
    dot: str
    qr_bg: tuple
    qr_ink: tuple


PALETTES = {
    "dark": Palette("#1b2535", "#222e41", "#364359", "#f0f2fc", "#b3bed0", "#a1afff", "#556d9c", (41, 59, 83), (204, 219, 241)),
    "light": Palette("#ffffff", "#f4f6fb", "#dae0ee", "#273555", "#5b687d", "#5367b6", "#c7d2ed", (240, 244, 251), (46, 63, 100)),
}


def valid_source_url(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 1024:
        return ""
    value = value.strip()
    if any(ord(char) <= 32 for char in value):
        return ""
    try:
        url = urlsplit(value)
        if url.scheme in ("http", "https") and url.hostname and not url.username and not url.password:
            return value
    except ValueError:
        pass
    return ""


def part_text(part):
    return (getattr(part, "data", None) or {}).get("text") or ""


def card_time(parts):
    return part_text(parts[0]).partition("\n时间：")[2] if parts else ""


def source_header_count(parts, platform):
    return int(bool(parts) and part_text(parts[0]).startswith("【" + platform))


def source_link(parts):
    """Read a standalone address already included by the source formatter."""
    return next((url for part in reversed(parts) for line in reversed(part_text(part).splitlines())
                 if (url := valid_source_url(line))), "")


def _image_bytes(part):
    data = (getattr(part, "data", None) or {}).get("content")
    return data if getattr(part, "type", "") == "file_image" and isinstance(data, bytes) else None


def _read_image(data, size):
    from PIL import Image, ImageOps

    with Image.open(BytesIO(data)) as original:
        if original.width * original.height > MAX_IMAGE_PIXELS:
            raise ValueError("图片像素超出卡片渲染上限")
        image = ImageOps.exif_transpose(original).convert("RGBA")
        image.thumbnail(size, Image.Resampling.LANCZOS)
        return image


async def _load_avatar(source):
    hosts = AVATAR_HOSTS.get(source.platform, ())
    if not hosts:
        return None
    url = source.avatar_url
    if isinstance(url, str) and url.startswith("//"):
        url = "https:" + url
    try:
        url = valid_source_url(url)
    except UnicodeError:
        return None
    if not url:
        return None
    parsed = urlsplit(url)
    host = parsed.hostname
    if not any(host == domain or host.endswith("." + domain) for domain in hosts):
        return None
    if parsed.netloc.lower() not in (host, host + ":443"):
        return None
    url = parsed._replace(scheme="https", fragment="").geturl()
    if url in _avatar_cache:
        expires, data = _avatar_cache[url]
        if expires > monotonic():
            _avatar_cache.move_to_end(url)
            return data
    from plugins.media import fetch_bytes

    try:
        data = await asyncio.wait_for(
            fetch_bytes(url, max_bytes=512 * 1024, follow_redirects=False), timeout=3,
        )
    except Exception:
        data = None
    _avatar_cache[url] = (monotonic() + (600 if data else 60), data)
    _avatar_cache.move_to_end(url)
    if len(_avatar_cache) > 64:
        _avatar_cache.popitem(last=False)
    return data


def _avatar_icon(data, diameter):
    from PIL import Image, ImageDraw, ImageOps

    size = (diameter, diameter)
    icon = Image.new("RGBA", size, "#8a929e")
    if data:
        try:
            with Image.open(BytesIO(data)) as original:
                if original.width * original.height <= MAX_IMAGE_PIXELS:
                    image = ImageOps.exif_transpose(original).convert("RGBA")
                    icon.alpha_composite(ImageOps.fit(image, size, Image.Resampling.LANCZOS))
        except Exception:
            pass  # A missing or unreadable avatar must not prevent card delivery.
    mask = Image.new("L", (diameter * 4, diameter * 4))
    ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
    icon.putalpha(mask.resize(size, Image.Resampling.LANCZOS))
    return icon


@lru_cache(maxsize=64)
def _font_for_thread(thread_id, size, bold, emoji):
    from PIL import ImageFont

    name = "seguiemj.ttf" if emoji else "msyhbd.ttc" if bold else "msyh.ttc"
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    try:
        return ImageFont.truetype(str(fonts / name), size)
    except OSError:
        if emoji:
            return _font_for_thread(thread_id, size, bold, False)
        raise


def _font(size, bold=False, emoji=False):
    return _font_for_thread(get_ident(), size, bold, emoji)


# Keep emoji variation selectors, skin tones and joined emoji on the same line.
_WORD = re.compile(r"\n|[^\S\n]+|[!-~]+(?:\ufe0f\u20e3)?|.(?:[\ufe0e\ufe0f\U0001f3fb-\U0001f3ff]|\u200d.)*")
_STEAM_PRICE = re.compile(r"^原价：([^\n]{1,32})\n现价：([^\n]{1,32}?)（-(\d{1,3})%）$", re.MULTILINE)


def _unicode_emoji(value):
    return "\ufe0f" in value or "\u200d" in value or any(0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF for c in value)


def _wrap(parts, width, size, bold=False):
    """Measure text and image runs together. Discord stickers never get separated from their run."""
    lines, row, used = [], [], 0

    def add(value, font=None, color_emoji=False):
        nonlocal used, row
        length = math.ceil(font.getlength(value)) if font else value.width + 4
        if used and used + length > width:
            lines.append(row)
            row, used = [], 0
        if not used and length > width and font and len(value) > 1 and not color_emoji:
            for char in value:
                add(char, font)
            return
        if not used and isinstance(value, str) and not value.strip():
            return
        row.append((value, font, color_emoji, length))
        used += length

    for part in parts:
        if isinstance(part, bytes):
            add(_read_image(part, (size + 5, size + 5)))
            continue
        for chunk in _WORD.findall(part.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")):
            if chunk == "\n":
                lines.append(row)
                row, used = [], 0
                continue
            color_emoji = _unicode_emoji(chunk)
            add(chunk, _font(size, bold, color_emoji), color_emoji)
    if row:
        lines.append(row)
    while lines and not lines[0]:
        del lines[0]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _draw_runs(canvas, draw, runs, x, center_y, ink):
    for value, font, colored, length in runs:
        if font:
            ascent, descent = font.getmetrics()
            draw.text((x, center_y - (ascent + descent) // 2), value,
                      fill=ink, font=font, embedded_color=colored)
        else:
            canvas.alpha_composite(value, (x + 2, center_y - value.height // 2))
        x += length


def _footer(url, colors):
    if not url:
        return None, []
    import qrcode
    from qrcode.image.styledpil import StyledPilImage
    from qrcode.image.styles.colormasks import SolidFillColorMask
    from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer, VerticalBarsDrawer

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    # Expand denser codes rather than compressing their modules below two pixels.
    side = qr.modules_count + 8
    size = max(120, side * 2)
    code = qr.make_image(image_factory=StyledPilImage,
        module_drawer=VerticalBarsDrawer(horizontal_shrink=Decimal("0.9")),
        eye_drawer=RoundedModuleDrawer(),
        color_mask=SolidFillColorMask(back_color=colors.qr_bg, front_color=colors.qr_ink)).get_image()
    from PIL import Image, ImageDraw
    code = code.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), 8, fill=255)
    code.putalpha(mask)
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    display = origin if len(url) > 210 else origin + "\n" + url[len(origin):]
    return code, _wrap([display], CONTENT - size - 24, 16)


def _png_page(colors, header, rows, footer, index, avatar):
    from PIL import Image, ImageColor, ImageDraw

    qr, links = footer
    header_height = 48 + sum(height for _, height, _, _ in header)
    footer_height = 18 + max(qr.height, len(links) * 23) if qr else 0
    height = header_height + sum(row[1] for row in rows) + footer_height + 22
    canvas = Image.new("RGBA", (WIDTH, height), colors.surface)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((0, 0, WIDTH - 1, height - 1), RADIUS, outline=colors.border)
    base, dot = ImageColor.getrgb(colors.surface), ImageColor.getrgb(colors.dot)
    for x in range(WIDTH - 196, WIDTH, 11):
        for y in range(5, 113, 11):
            opacity = max(0, min(1, (x - WIDTH + 196) / 196 - y / 143))
            if opacity > 0.07:
                fill = tuple(round(a + (b - a) * opacity) for a, b in zip(base, dot))
                draw.ellipse((x, y, x + 2, y + 2), fill=fill)

    try:
        with Image.open(PROJECT_ROOT / "panel" / "wrelay_panel.ico") as mark:
            icon = mark.convert("RGBA")
            icon.thumbnail((44, 38), Image.Resampling.LANCZOS)
            canvas.alpha_composite(icon, (WIDTH - PAD - 44, 28))
    except (OSError, ValueError):
        pass

    y = 25
    x = PAD
    text_offset = 0
    if avatar is not None:
        text_height = header_height - 48
        avatar_y = y + (text_height - avatar.height) // 2
        canvas.alpha_composite(avatar, (PAD, avatar_y))
        x += avatar.width + AVATAR_GAP
        last_line_height, last_line = header[-1][1:3]
        last_center = y + text_height - last_line_height + last_line_height // 2
        bottoms = []
        for value, font, _, _ in last_line:
            if font:
                ascent, descent = font.getmetrics()
                if value.strip():
                    bottoms.append(last_center - (ascent + descent) // 2 + font.getbbox(value)[3])
            else:
                bottoms.append(last_center - value.height // 2 + value.height)
        if bottoms:
            text_offset = avatar_y + avatar.height - max(bottoms)
    for _, line_height, line, color in header:
        _draw_runs(canvas, draw, line, x, y + line_height // 2 + text_offset, color)
        y += line_height
    y = header_height
    for kind, row_height, data, ink in rows:
        if kind == "image":
            canvas.alpha_composite(data, (PAD, y))
        elif kind == "price":
            old, current, percent = data
            center = y + row_height // 2
            draw.rounded_rectangle((PAD, y, WIDTH - PAD, y + row_height), 12,
                                   fill=colors.panel, outline=colors.border)
            left = PAD + 18
            before_font, now_font, discount_font = _font(20), _font(30, True), _font(21, True)
            before_width = math.ceil(before_font.getlength(old))
            discount = f"−{percent}%"
            discount_x = WIDTH - PAD - 18 - math.ceil(discount_font.getlength(discount))
            draw.text((left, center), old, font=before_font, fill=colors.muted, anchor="lm")
            draw.line((left, center, left + before_width, center), fill=colors.muted)
            draw.text((left + before_width + 14, center), current, font=now_font, fill=colors.ink, anchor="lm")
            draw.text((discount_x, center), discount, font=discount_font, fill=colors.accent, anchor="lm")
        elif kind != "space":
            inset = 0
            if kind == "quote":
                draw.rectangle((PAD, y, WIDTH - PAD, y + row_height), fill=colors.panel)
                draw.rectangle((PAD, y, PAD + 2, y + row_height), fill=colors.accent)
                inset = 16
            _draw_runs(canvas, draw, data, PAD + inset, y + row_height // 2, ink)
        y += row_height
    if qr:
        footer_top = y + 18
        canvas.alpha_composite(qr, (WIDTH - PAD - qr.width, footer_top))
        link_y = footer_top + max(0, (qr.height - len(links) * 23) // 2)
        for line in links:
            _draw_runs(canvas, draw, line, PAD, link_y + 11, colors.accent)
            link_y += 23

    # Only the four outer corners are transparent. The entire reading surface remains opaque.
    mask = Image.new("L", (WIDTH * 2, height * 2), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, WIDTH * 2 - 1, height * 2 - 1), RADIUS * 2, fill=255)
    canvas.putalpha(mask.resize(canvas.size, Image.Resampling.BOX))
    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    if output.tell() > MAX_PNG_BYTES:
        raise ValueError(f"卡片第 {index} 张超过图片大小上限")
    return output.getvalue()


def _card_header(source, colors):
    name = (source.author or "").strip() or source.platform
    metadata = " · ".join(value for value in (source.platform, source.detail) if value)
    diameter = 55 if source.platform in AVATAR_HOSTS else 0
    while True:
        width = CONTENT - 65 - (diameter + AVATAR_GAP if diameter else 0)
        header = [("line", 35, row, colors.ink) for row in _wrap([name], width, 26, True)]
        header.extend(("line", 25, row, colors.muted) for row in _wrap([metadata], width, 16))
        if not diameter:
            return header, 0
        # Size the circle to wrapped text too, while reserving space for unusually long headers.
        target = min(sum(height for _, height, _, _ in header) * 11 // 12, CONTENT // 2)
        if target == diameter:
            return header, diameter
        diameter = target


def _render(source, theme, body, original, attachments, avatar_data=None):
    from PIL import Image

    colors = PALETTES.get(theme, PALETTES["dark"])
    header, diameter = _card_header(source, colors)
    avatar = _avatar_icon(avatar_data, diameter) if diameter else None

    rows = []
    for parts, label in ((body, ""), (original, "原文")):
        if not parts:
            continue
        pending = []

        def add_text(parts, quote=False):
            size = 19 if quote else 27
            for line in _wrap(parts, CONTENT - (32 if quote else 0), size):
                rows.append(("quote" if quote else "line", 31 if quote else 38, line, colors.ink))

        if label:
            rows.append(("space", 12, None, ""))
            add_text([label], quote=True)
        for part in parts:
            text = part.lstrip() if isinstance(part, str) else ""
            price = _STEAM_PRICE.search(text) if source.platform == "Steam" and not label else None
            if price:
                old, current, percent = price.groups()
                if _font(20).getlength(old) + _font(30, True).getlength(current) <= CONTENT - 160:
                    pending.append(text[:price.start()])
                    add_text(pending)
                    pending.clear()
                    rows.extend((("space", 10, None, ""), ("price", 62, (old, current, percent), ""), ("space", 10, None, "")))
                    pending.append(text[price.end():])
                    continue
            if text.startswith(("┃ 引用 ", "↩︎ ", "↩ ")):
                add_text(pending, quote=bool(label))
                pending.clear()
                rows.append(("space", 9, None, ""))
                quote_text = re.sub(r"^↩︎? ", "引用 ", text.removeprefix("┃ "))
                add_text([quote_text], quote=True)
                rows.append(("space", 9, None, ""))
            else:
                pending.append(part)
        add_text(pending, quote=bool(label))

    for index in range(0, len(attachments), 2):
        pair = attachments[index:index + 2]
        if len(pair) == 1:
            image = _read_image(pair[0], (CONTENT, 780))
        else:
            column = (CONTENT - 12) // 2
            left, right = [_read_image(data, (column, 480)) for data in pair]
            image = Image.new("RGBA", (left.width + 12 + right.width, max(left.height, right.height)))
            image.alpha_composite(left, (0, 0))
            image.alpha_composite(right, (left.width + 12, 0))
        rows.append(("space", 12, None, ""))
        rows.append(("image", image.height, image, ""))

    footer = _footer(valid_source_url(source.url), colors)
    if not rows:
        return [_png_page(colors, header, [], footer, 1, avatar)] if footer[0] else []
    reserved = 70 + sum(row[1] for row in header)
    if footer[0]:
        reserved += max(footer[0].height, len(footer[1]) * 23) + 18
    pages, page, used = [], [], reserved
    for row in rows:
        if used + row[1] > MAX_PAGE_HEIGHT and page:
            pages.append(page)
            page, used = [], reserved
        page.append(row)
        used += row[1]
    if page:
        pages.append(page)
    if len(pages) > MAX_PAGES or reserved >= MAX_PAGE_HEIGHT - 800:
        raise ValueError("卡片超过排版上限")
    return [_png_page(colors, header, page, footer if i == len(pages) else (None, []), i, avatar)
            for i, page in enumerate(pages, 1)]


async def render_configured_card(source, options, before_translation, text_parts, media_items,
                                 translation_config=None, *, header_parts=1):
    """Apply the shared settings to prepared content; all sending stays with the caller."""
    if options.get("image_card_enabled", True):
        return await render_forward_card(source, options.get("image_card_theme", "dark"),
                                         before_translation, text_parts, media_items,
                                         translation_config, header_parts=header_parts)
    return text_parts, media_items


async def render_forward_card(source, theme, before_translation, text_parts, media_items,
                              translation_config, *, header_parts=1):
    """Return PNG pages and untouched non-image media, or the existing output on failure."""
    from nonebot.adapters.qq import MessageSegment

    if any(getattr(part, "type", "") not in ("text", "file_image") for part in (*before_translation, *text_parts)):
        return text_parts, media_items

    def runs(parts):
        values = [part_text(part) or _image_bytes(part) for part in parts]
        return [value for value in values if value]

    body, original = runs(text_parts[header_parts:]), []
    if text_parts is not before_translation and (translation_config or {}).get("append_original", True):
        body, original = runs(text_parts[-1:]), runs(before_translation[header_parts:])
    if source.platform == "X":
        body = [part for part in body if not isinstance(part, str) or not part.startswith("\n↻ 转发 ")]
        original = [part for part in original if not isinstance(part, str) or not part.startswith("\n↻ 转发 ")]
    elif source.platform == "Steam":
        for parts in (body, original):
            if parts and isinstance(parts[0], str) and parts[0].startswith("【Steam · "):
                lines = parts[0].splitlines()[1:]
                if lines and lines[0].strip() == source.author:
                    lines.pop(0)
                parts[0] = "\n".join(lines)
    pictures, rest = [], []
    for item in media_items:
        data = _image_bytes(item.get("segment"))
        if data:
            pictures.append(data)
        else:
            rest.append(item)
    if not body and not pictures:
        return text_parts, media_items
    if sum(len(part) for part in body + original if isinstance(part, str)) > 40_000 or len(pictures) > 24:
        return text_parts, media_items
    try:
        url = valid_source_url(source.url)
        if url:
            # A standalone source address belongs in the footer once. Leave URLs within prose alone.
            for parts in (body, original):
                parts[:] = ["\n".join(line for line in part.split("\n") if line.strip() != url)
                            if isinstance(part, str) else part for part in parts]
        avatar_data = await _load_avatar(source)
        async with _render_slots:
            pages = await run_in_thread_to_completion(_render, source, theme, body, original, pictures, avatar_data)
        if not pages:
            return text_parts, media_items
        preview = " ".join(part_text(part) for part in text_parts)[:300]
        items = [dict(kind="转发卡片", segment=MessageSegment.file_image(data, f"wrelay-card-{i}.png"),
                      card_part=f"page-{i}", card_preview=preview if i == 1 else "")
                 for i, data in enumerate(pages, 1)]
        items.extend(dict(item, card_part=f"media-{i}") for i, item in enumerate(rest, 1))
        return [], items
    except Exception as exc:
        logger.warning("{} 图片化转发失效，按原格式转发: {}", source.platform, type(exc).__name__)
        return text_parts, media_items
