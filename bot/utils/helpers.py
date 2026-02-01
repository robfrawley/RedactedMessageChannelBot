from __future__ import annotations

import io
import os
import tempfile
import random
import textwrap
import re
import cv2
from typing import Optional, Tuple, Union

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance, ImageChops
from PIL.ImageFont import ImageFont as ImageFontBase, FreeTypeFont as FreeTypeFontBase
from wordfreq import top_n_list
from better_profanity import profanity

from bot.utils.logger import logger


_WORDS_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z0-9]+")

_WORDS_LISTING: list[str] = [
    w for w in top_n_list("en", 50_000)
    if w.isalpha()
    and len(w) >= 2
    and not profanity.contains_profanity(w)
]

_WORDS_LISTING_BY_LENGTH: dict[int, list[str]] = {
    n: [w for w in _WORDS_LISTING if len(w) == n]
    for n in {len(w) for w in _WORDS_LISTING}
}


def _match_case(src: str, repl: str) -> str:
    if src.isupper():
        return repl.upper()
    if src.islower():
        return repl.lower()
    if src[:1].isupper() and src[1:].islower():
        return repl[:1].upper() + repl[1:].lower()
    return repl


def randomize_preserve_lengths(text: str, *, seed: Optional[int] = None) -> str:
    rng = random.Random(seed)

    def make_replacement(n: int) -> str:
        candidates = _WORDS_LISTING_BY_LENGTH.get(n)
        if candidates:
            return rng.choice(candidates)

        alphabet = "abcdefghijklmnopqrstuvwxyz"
        return "".join(rng.choice(alphabet) for _ in range(n))

    def repl(m: re.Match[str]) -> str:
        src = m.group(0)
        replacement = make_replacement(len(src))
        return _match_case(src, replacement)

    return _WORDS_PATTERN.sub(repl, text)


def redacted_document_image(
    message: str,
    *,
    username: Optional[str] = None,
    width: int = 1920,
    padding: int = 80,
    bg: Tuple[int, int, int] | None = None,
    text_color: Tuple[int, int, int] = (25, 25, 25),
    font_path: Optional[str] = None,
    font_size: int = 28,
    line_spacing: int = 16,
    redaction_chance: float = 0.95,
    min_run: int = 1,
    max_run: int = 8,
    seed: Optional[int] = None,
    add_header: bool = True,
    add_footer: bool = True,
) -> Image.Image:
    rng = random.Random(seed)

    if bg is None:
        bg_rgb = rng.randint(140, 200)
        bg = (bg_rgb, bg_rgb, bg_rgb)

    if font_path:
        font = ImageFont.truetype(font_path, font_size)
        header_font = ImageFont.truetype(font_path, int(font_size * 0.85))
        stamp_font = ImageFont.truetype(font_path, int(font_size * 1.2))
        meta_font = ImageFont.truetype(font_path, int(font_size * 0.9))
    else:
        try:
            font = ImageFont.truetype("DejaVuSansMono.ttf", font_size)
            header_font = ImageFont.truetype("DejaVuSansMono.ttf", int(font_size * 0.85))
            stamp_font = ImageFont.truetype("DejaVuSansMono.ttf", int(font_size * 1.2))
            meta_font = ImageFont.truetype("DejaVuSansMono.ttf", int(font_size * 0.9))
        except OSError:
            font = ImageFont.load_default()
            header_font = font
            stamp_font = font
            meta_font = font

    def text_size(draw: ImageDraw.ImageDraw, s: str, f: ImageFontBase | FreeTypeFontBase) -> Tuple[int, int]:
        bbox = draw.textbbox((0, 0), s, font=f)
        return int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1])

    message = randomize_preserve_lengths(message, seed=seed)

    tmp = Image.new("RGB", (width, 260 if username else 200), bg)
    dtmp = ImageDraw.Draw(tmp)

    avg_char_w = max(8, text_size(dtmp, "M", font)[0])
    usable_w = width - 2 * padding
    wrap_cols = max(20, usable_w // avg_char_w)

    paragraphs = [p.strip() for p in message.replace("\r\n", "\n").split("\n")]
    wrapped_lines: list[str] = []
    for p in paragraphs:
        if not p:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(textwrap.wrap(p, width=wrap_cols, break_long_words=False))

    token_lines: list[list[Tuple[str, bool]]] = []
    for line in wrapped_lines:
        if not line.strip():
            token_lines.append([])
            continue

        tokens = line.split(" ")
        redact_flags = [False] * len(tokens)

        i = 0
        while i < len(tokens):
            if rng.random() < redaction_chance and tokens[i].strip():
                run = rng.randint(min_run, max_run)
                for j in range(i, min(i + run, len(tokens))):
                    if tokens[j].strip():
                        redact_flags[j] = True
                i += run
            else:
                i += 1

        token_lines.append(list(zip(tokens, redact_flags)))

    line_h = text_size(dtmp, "Ag", font)[1]
    header_h = int(line_h * 2.2) if add_header else 0
    footer_pad = int(line_h * 1.4)
    footer_h = (int(line_h * 2.0) + footer_pad) if add_footer else 0

    username_block_h = 0
    if username:
        username_block_h = line_h + 18

    body_h = len(wrapped_lines) * (line_h + line_spacing)
    height = padding + header_h + username_block_h + body_h + footer_h + padding

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    for _ in range((width * height) // 12000):
        x = rng.randrange(0, width)
        y = rng.randrange(0, height)
        shade = rng.randint(220, 242)
        img.putpixel((x, y), (shade, shade, shade))

    y = padding

    if add_header:
        header_text_r = f"FILE: {rng.randint(100,999)}-{rng.randint(1000,9999)}-{rng.randint(10,99)}"
        header_text_l = "JURISDICTION   : UNITED STATES GOVERNMENT (LOS ANGELES COUNTY OF CALIFORNIA)"
        header_subtxt = "CLASSIFICATION : [REDACTED]"

        draw.text((padding, y), header_text_l, font=header_font, fill=text_color)
        header_r_w, _ = text_size(draw, header_text_r, header_font)
        x_r = width - padding - header_r_w
        draw.text((x_r, y), header_text_r, font=header_font, fill=text_color)
        y += line_h + 6

        draw.text((padding, y), header_subtxt, font=header_font, fill=text_color)
        y += line_h + 14

        draw.line((padding, y, width - padding, y), fill=(60, 60, 60), width=2)
        y += 22

    if username:
        meta_line = f"SUBJECT: {username}"
        draw.text((padding, y), meta_line, font=meta_font, fill=text_color)
        y += line_h + 10
        draw.line((padding, y, width - padding, y), fill=(90, 90, 90), width=1)
        y += 8

    for tokens in token_lines:
        if not tokens:
            y += line_h + line_spacing
            continue

        x = padding
        for token, do_redact in tokens:
            draw.text((x, y), token, font=font, fill=text_color)
            tw, th = text_size(draw, token, font)

            if do_redact and token.strip():
                bar_pad_x = rng.randint(6, 12)
                bar_h = max(10, int(th * rng.uniform(0.72, 0.92)))
                bar_y = y + rng.randint(2, max(2, th - bar_h))
                draw.rectangle(
                    (x - bar_pad_x, bar_y, x + tw + bar_pad_x, bar_y + bar_h),
                    fill=(0, 0, 0),
                )

            space_w, _ = text_size(draw, " ", font)
            x += tw + space_w

        y += line_h + line_spacing

    if add_footer:
        y = height - padding - footer_h + footer_pad
        draw.line((padding, y, width - padding, y), fill=(60, 60, 60), width=2)
        y += 18

        page = rng.randint(1, 9)
        total = rng.randint(page, 12)
        footer_text = f"PAGE {page} OF {total}     AUTHORITY: [REDACTED]     DATE: [REDACTED]"
        draw.text((padding, y), footer_text, font=header_font, fill=text_color)

        stamp = rng.choice(["REDACTED", "CONFIDENTIAL", "EYES ONLY", "DECLASSIFIED"])
        stamp_w, stamp_h = text_size(draw, stamp, stamp_font)
        stamp_x = width - padding - stamp_w - 20
        stamp_y = height - padding - stamp_h - 10

        draw.rectangle(
            (stamp_x - 14, stamp_y - 10, stamp_x + stamp_w + 14, stamp_y + stamp_h + 10),
            outline=(120, 30, 30),
            width=5,
        )
        draw.text((stamp_x, stamp_y), stamp, font=stamp_font, fill=(120, 30, 30))

    img = apply_scan_effect(img, seed=seed)

    return img


def apply_scan_effect(
    img: Image.Image,
    *,
    seed: Optional[int] = None,
    tilt_degrees: float = 1.5,     # max absolute rotation
    perspective: float = 0.025,     # 0..~0.06 (small is realistic)
    vignette: float = 0.25,        # 0..1 (edge darkening strength)
    grain: float = 0.25,           # 0..1 (paper noise)
    dust: int = 60,               # number of dust specks
    crease_chance: float = 0.10,   # chance to add a crease line
    dogear_chance: float = 0.20,   # chance to fold a corner
) -> Image.Image:
    rng = random.Random(seed)
    base = img.convert("RGB")
    w, h = base.size

    # ---- slight rotation (tilt)
    angle = rng.uniform(-tilt_degrees, tilt_degrees)
    # expand keeps corners; fillcolor approximates paper/scan bed
    base = base.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(18, 18, 18))

    # ---- mild perspective warp (scanner skew)
    if perspective > 0:
        w2, h2 = base.size
        dx = int(w2 * perspective * rng.uniform(0.5, 1.0))
        dy = int(h2 * perspective * rng.uniform(0.5, 1.0))

        # source corners
        src = [(0, 0), (w2, 0), (w2, h2), (0, h2)]
        # destination corners (subtle random skew)
        dst = [
            (rng.randint(0, dx), rng.randint(0, dy)),
            (w2 - rng.randint(0, dx), rng.randint(0, dy)),
            (w2 - rng.randint(0, dx), h2 - rng.randint(0, dy)),
            (rng.randint(0, dx), h2 - rng.randint(0, dy)),
        ]

        base = _perspective_transform(base, src, dst)

    # ---- scan softness
    base = base.filter(ImageFilter.GaussianBlur(radius=0.45))

    # ---- contrast/brightness nudge (scanner look)
    base = ImageEnhance.Contrast(base).enhance(rng.uniform(0.95, 1.08))
    base = ImageEnhance.Brightness(base).enhance(rng.uniform(0.95, 1.05))

    # ---- vignette (edge darkening)
    if vignette > 0:
        v = _make_vignette_mask(base.size, strength=vignette)
        # dark overlay through vignette mask
        dark = Image.new("RGB", base.size, (0, 0, 0))
        base = Image.composite(dark, base, v)

    # ---- paper grain + dust
    base = _add_grain(base, rng=rng, strength=grain)
    base = _add_dust(base, rng=rng, count=dust)

    # ---- optional crease line
    if rng.random() < crease_chance:
        base = _add_crease(base, rng=rng)

    # ---- optional dog-eared corner
    if rng.random() < dogear_chance:
        base = _add_dogear(base, rng=rng)

    return base


def _perspective_transform(img: Image.Image, src: list[tuple[int, int]], dst: list[tuple[int, int]]) -> Image.Image:
    # Compute perspective coefficients for PIL's transform.
    # Based on solving a linear system for the 8 coeffs.
    import numpy as np

    def _matrix(pts1, pts2):
        m = []
        for (x, y), (u, v) in zip(pts1, pts2):
            m.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
            m.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        return np.array(m, dtype=float)

    A = _matrix(dst, src)
    B = np.array([p for uv in src for p in uv], dtype=float)

    coeffs = np.linalg.lstsq(A, B, rcond=None)[0].tolist()
    return img.transform(img.size, Image.Transform.PERSPECTIVE, coeffs, resample=Image.Resampling.BICUBIC)


def _make_vignette_mask(size: tuple[int, int], *, strength: float) -> Image.Image:
    w, h = size
    # White = replace with dark overlay; black = keep original
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)

    # Draw a large ellipse that stays black in the center, then blur it.
    inset = int(min(w, h) * 0.06)
    draw.ellipse((inset, inset, w - inset, h - inset), fill=255)

    # Invert so edges are stronger
    mask = ImageChops.invert(mask).filter(ImageFilter.GaussianBlur(radius=int(min(w, h) * 0.08)))

    # Scale strength
    mask = ImageEnhance.Brightness(mask).enhance(max(0.0, min(1.0, strength)) * 2.0)
    return mask


def _add_grain(img: Image.Image, *, rng: random.Random, strength: float) -> Image.Image:
    if strength <= 0:
        return img
    w, h = img.size
    # Create a noise layer in L mode, blur slightly, then blend.
    noise = Image.new("L", (w, h), 0)
    px = noise.load()
    assert px is not None
    amt = int(255 * max(0.0, min(1.0, strength)))
    for _ in range((w * h) // 35):
        x = rng.randrange(0, w)
        y = rng.randrange(0, h)
        px[x, y] = rng.randint(0, amt)
    noise = noise.filter(ImageFilter.GaussianBlur(radius=1.2))
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    return Image.blend(img, noise_rgb, alpha=0.08 + strength * 0.10)


def _add_dust(img: Image.Image, *, rng: random.Random, count: int) -> Image.Image:
    if count <= 0:
        return img
    w, h = img.size
    out = img.copy()
    draw = ImageDraw.Draw(out)
    for _ in range(count):
        x = rng.randrange(0, w)
        y = rng.randrange(0, h)
        r = rng.randint(1, 2)
        shade = rng.randint(20, 80)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(shade, shade, shade))
    return out


def _add_crease(img: Image.Image, *, rng: random.Random) -> Image.Image:
    w, h = img.size
    out = img.copy()
    draw = ImageDraw.Draw(out)

    # random diagonal-ish crease
    x1 = rng.randint(-w // 5, w // 5)
    y1 = rng.randint(h // 5, h // 2)
    x2 = rng.randint(w - w // 5, w + w // 5)
    y2 = rng.randint(h // 2, h - h // 5)

    # shadow line
    draw.line((x1, y1, x2, y2), fill=(40, 40, 40), width=rng.randint(2, 4))
    # highlight line slightly offset
    draw.line((x1 + 3, y1 - 2, x2 + 3, y2 - 2), fill=(220, 220, 220), width=rng.randint(1, 3))

    return out.filter(ImageFilter.GaussianBlur(radius=0.6))


def _add_dogear(img: Image.Image, *, rng: random.Random) -> Image.Image:
    w, h = img.size
    out = img.copy()
    draw = ImageDraw.Draw(out)

    corner = rng.choice(["tl", "tr", "bl", "br"])
    size = int(min(w, h) * rng.uniform(0.06, 0.11))

    if corner == "tr":
        pts = [(w - size, 0), (w, 0), (w, size)]
        shade = (210, 210, 210)
        draw.polygon(pts, fill=shade)
        draw.line((w - size, 0, w, size), fill=(120, 120, 120), width=2)
    elif corner == "tl":
        pts = [(0, 0), (size, 0), (0, size)]
        draw.polygon(pts, fill=(210, 210, 210))
        draw.line((0, size, size, 0), fill=(120, 120, 120), width=2)
    elif corner == "br":
        pts = [(w - size, h), (w, h), (w, h - size)]
        draw.polygon(pts, fill=(210, 210, 210))
        draw.line((w - size, h, w, h - size), fill=(120, 120, 120), width=2)
    else:  # bl
        pts = [(0, h), (size, h), (0, h - size)]
        draw.polygon(pts, fill=(210, 210, 210))
        draw.line((0, h - size, size, h), fill=(120, 120, 120), width=2)

    return out.filter(ImageFilter.GaussianBlur(radius=0.4))


AssetInput = Union[Image.Image, bytes, io.BytesIO, str]


def _first_frame_pil(asset: AssetInput) -> Image.Image:
    """
    Return a PIL RGB image representing the first frame of:
      - still images (png/jpg/webp/etc)
      - animated GIFs (frame 0)
      - videos (frame 0) via OpenCV (optional dependency)
    """
    # Already a PIL image (could be animated)
    if isinstance(asset, Image.Image):
        try:
            asset.seek(0)  # type: ignore[attr-defined]
        except Exception:
            pass
        return asset.copy().convert("RGB")

    # Normalize to bytes or file path
    data: Optional[bytes] = None
    path: Optional[str] = None

    if isinstance(asset, str):
        path = asset
    elif isinstance(asset, io.BytesIO):
        data = asset.getvalue()
    elif isinstance(asset, (bytes, bytearray)):
        data = bytes(asset)
    else:
        raise TypeError(f"Unsupported asset type: {type(asset)!r}")

    # Try Pillow first (works for images + GIF)
    try:
        if data is not None:
            with Image.open(io.BytesIO(data)) as im:
                try:
                    im.seek(0)
                except Exception:
                    pass
                return im.copy().convert("RGB")
        else:
            assert path is not None
            with Image.open(path) as im:
                try:
                    im.seek(0)
                except Exception:
                    pass
                return im.copy().convert("RGB")
    except Exception:
        pass  # likely video or unsupported image

    # Video first frame via OpenCV (optional)
    if cv2 is None:
        raise RuntimeError(
            "Video first-frame extraction requires opencv-python (and numpy). "
            "Install: pip install opencv-python numpy"
        )

    if data is not None:
        # OpenCV VideoCapture generally needs a filepath, not raw bytes -> temp file
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            cap = cv2.VideoCapture(tmp_path)
            ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                raise ValueError("Could not decode video first frame")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    else:
        assert path is not None
        cap = cv2.VideoCapture(path)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            raise ValueError("Could not decode video first frame")

    # OpenCV outputs BGR; convert to RGB
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(frame_rgb).convert("RGB")


def redact_image(
    img: Image.Image,
    *,
    seed: Optional[int] = None,
    exposed_fraction: float = 0.03,
    window_count: int = 60,
    min_window_size: tuple[int, int] = (24, 18),
    max_window_size_fraction: tuple[float, float] = (0.10, 0.08),
    blur_radius: float = 0.35,
    feather: int = 1,
    pixel_size: int = 16,
) -> Image.Image:
    rng = random.Random(seed)

    base = img.convert("RGB")
    w, h = base.size

    # Blur a little to soften detail
    if blur_radius > 0:
        base = base.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    # Pixelate (downscale then upscale with NEAREST)
    if pixel_size > 1:
        pw = max(1, w // pixel_size)
        ph = max(1, h // pixel_size)
        base = (
            base.resize((pw, ph), resample=Image.Resampling.BILINEAR)
                .resize((w, h), resample=Image.Resampling.NEAREST)
        )

    exposed_fraction = max(0.0, min(0.25, float(exposed_fraction)))

    # Start with full black; reveal only small windows of the (already pixelated) base
    black = Image.new("RGB", (w, h), (0, 0, 0))
    mask = Image.new("L", (w, h), 0)
    draw_mask = ImageDraw.Draw(mask)

    total_area = w * h
    target_reveal_area = int(total_area * exposed_fraction)
    revealed_area = 0

    max_w = max(min_window_size[0], int(w * max_window_size_fraction[0]))
    max_h = max(min_window_size[1], int(h * max_window_size_fraction[1]))

    attempts = 0
    max_attempts = max(2000, window_count * 25)

    while revealed_area < target_reveal_area and attempts < max_attempts:
        attempts += 1

        ww = rng.randint(min_window_size[0], max_w)
        hh = rng.randint(min_window_size[1], max_h)

        x1 = rng.randint(0, max(0, w - ww))
        y1 = rng.randint(0, max(0, h - hh))
        x2 = x1 + ww
        y2 = y1 + hh

        draw_mask.rectangle((x1, y1, x2, y2), fill=255)
        revealed_area += ww * hh  # overlaps bias toward revealing less, which is fine

    if feather > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=float(feather)))

    final = Image.composite(base, black, mask)

    # Overlay "[REDACTED]" text in fully redacted areas
    draw = ImageDraw.Draw(final)
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", max(14, w // 80))
    except OSError:
        font = ImageFont.load_default()

    text = "[REDACTED]"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = int(bbox[2] - bbox[0])
    text_h = int(bbox[3] - bbox[1])

    spacing_x: int = int(text_w + rng.randint(20, 40))
    spacing_y: int = int(text_h + rng.randint(16, 28))

    mask_px = mask.load()
    assert mask_px is not None

    for yy in range(0, h, spacing_y):
        for xx in range(0, w, spacing_x):
            sx = min(w - 1, max(0, xx + text_w // 2))
            sy = min(h - 1, max(0, yy + text_h // 2))

            mval = mask_px[sx, sy]
            m = int(mval) if not isinstance(mval, tuple) else int(mval[0])

            if m < 10:
                shade = rng.randint(80, 120)
                draw.text(
                    (xx + rng.randint(-4, 4), yy + rng.randint(-2, 2)),
                    text,
                    fill=(shade, shade, shade),
                    font=font,
                )

    final = apply_scan_effect(final, seed=seed)

    return final


def redact_asset(
    asset: AssetInput,
    *,
    seed: Optional[int] = None,
    exposed_fraction: float = 0.03,
    window_count: int = 60,
    min_window_size: tuple[int, int] = (24, 18),
    max_window_size_fraction: tuple[float, float] = (0.10, 0.08),
    blur_radius: float = 0.35,
    feather: int = 1,
    pixel_size: int = 16,
) -> Image.Image:
    """
    Accepts:
      - PIL Image (including animated GIFs)
      - bytes / BytesIO (images, GIFs, videos)
      - file path (images, GIFs, videos)

    Extracts first frame (for GIF/video) then applies redact_image exactly the same.
    """
    first = _first_frame_pil(asset)
    return redact_image(
        first,
        seed=seed,
        exposed_fraction=exposed_fraction,
        window_count=window_count,
        min_window_size=min_window_size,
        max_window_size_fraction=max_window_size_fraction,
        blur_radius=blur_radius,
        feather=feather,
        pixel_size=pixel_size,
    )


def pil_to_discord_file(img: Image.Image, *, filename: str) -> discord.File:
    # Encode the PIL image to an in-memory PNG so we don't touch disk.
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    # Wrap the in-memory buffer as a discord.File to upload as an attachment.
    return discord.File(fp=buf, filename=filename)


async def fetch_bytes(session: aiohttp.ClientSession, url: str) -> bytes:
    # Fetch raw bytes for a URL, raising if the HTTP status isn't 2xx.
    # Used for downloading sticker asset content for redaction.
    async with session.get(url) as resp:
        resp.raise_for_status()
        return await resp.read()

def sticker_first_frame_png_url(sticker_id: int) -> str:
    """
    Animated Discord stickers are usually Lottie (.json) and not directly PIL-loadable.
    The CDN can rasterize to PNG; this URL yields a PNG (effectively first frame).
    """
    return f"https://cdn.discordapp.com/stickers/{sticker_id}.png"

_GIF_URL_ONLY_RE: re.Pattern[str] = re.compile(
    r"^\s*<?https?://[^\s<>]+?\.gif(?:\?[^\s<>]*)?>?\s*$",
    re.IGNORECASE,
)

def extract_gif_url_only(content: str) -> str | None:
    m = _GIF_URL_ONLY_RE.match(content or "")
    if not m:
        return None
    # strip whitespace + optional <...>
    url = content.strip()
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1].strip()
    return url
