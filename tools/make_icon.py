# -*- coding: utf-8 -*-
"""生成应用图标（学习帽 + 蓝色渐变），输出 PNG 与多尺寸 ICO。

用法：python tools/make_icon.py
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024  # 超采样尺寸，最后缩小保证边缘平滑

# 主题色：默认红色系（可用环境变量 ICON_THEME=blue 切换回蓝青）
THEMES = {
    "red": ((239, 68, 68), (249, 115, 22)),
    "blue": ((37, 99, 235), (6, 182, 212)),
}
THEME = os.environ.get("ICON_THEME", "red")
C1, C2 = THEMES.get(THEME, THEMES["red"])
OUT_DIR = Path(__file__).resolve().parent.parent


def build_base() -> Image.Image:
    """画渐变底 + 圆角遮罩。"""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for y in range(S):
        t = y / (S - 1)
        color = (
            int(C1[0] + (C2[0] - C1[0]) * t),
            int(C1[1] + (C2[1] - C1[1]) * t),
            int(C1[2] + (C2[2] - C1[2]) * t),
            255,
        )
        draw.line([(0, y), (S, y)], fill=color)

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=255)
    img.putalpha(mask)
    return img


def draw_cap(img: Image.Image) -> Image.Image:
    """叠加白色学士帽图形（带柔和投影）。"""
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = S // 2
    cy = int(S * 0.46)

    half_w = int(S * 0.325)
    half_h = int(S * 0.17)

    diamond = [(cx, cy - half_h), (cx + half_w, cy), (cx, cy + half_h), (cx - half_w, cy)]
    body = [
        (cx - int(S * 0.145), cy + int(S * 0.038)),
        (cx + int(S * 0.145), cy + int(S * 0.038)),
        (cx + int(S * 0.102), cy + int(S * 0.19)),
        (cx - int(S * 0.102), cy + int(S * 0.19)),
    ]
    tx = cx + half_w - int(S * 0.038)
    tassel_y = cy + int(S * 0.168)
    tassel_r = int(S * 0.028)

    # 投影
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    offset = int(S * 0.012)
    sd.polygon([(x, y + offset) for x, y in diamond], fill=(8, 25, 60, 70))
    sd.polygon([(x, y + offset) for x, y in body], fill=(8, 25, 60, 70))
    sd.line([(tx, cy + offset), (tx, tassel_y + offset)], fill=(8, 25, 60, 60),
            width=int(S * 0.02))
    sd.ellipse([tx - tassel_r, tassel_y + offset - tassel_r,
                tx + tassel_r, tassel_y + offset + tassel_r], fill=(8, 25, 60, 60))
    shadow = shadow.filter(ImageFilter.GaussianBlur(int(S * 0.012)))
    layer = Image.alpha_composite(layer, shadow)

    d = ImageDraw.Draw(layer)
    # 帽顶（菱形）
    d.polygon(diamond, fill=(255, 255, 255, 255))
    # 帽身（梯形）
    d.polygon(body, fill=(255, 255, 255, 255))
    # 流苏
    d.line([(tx, cy), (tx, tassel_y)], fill=(255, 255, 255, 255), width=int(S * 0.019))
    d.ellipse([tx - tassel_r, tassel_y - tassel_r, tx + tassel_r, tassel_y + tassel_r],
              fill=(255, 255, 255, 255))

    return Image.alpha_composite(img, layer)


def main() -> None:
    """生成并保存图标。"""
    base = draw_cap(build_base())

    static_dir = OUT_DIR / "web" / "static" / "img"
    static_dir.mkdir(parents=True, exist_ok=True)
    png_path = static_dir / "icon.png"
    base.resize((256, 256), Image.LANCZOS).save(png_path, "PNG")
    base.resize((512, 512), Image.LANCZOS).save(static_dir / "icon-512.png", "PNG")

    resource_dir = OUT_DIR / "resource"
    resource_dir.mkdir(parents=True, exist_ok=True)
    ico_path = resource_dir / "chaoxing.ico"
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    base.save(ico_path, format="ICO", sizes=sizes)

    print("saved:", png_path)
    print("saved:", ico_path)


if __name__ == "__main__":
    main()
