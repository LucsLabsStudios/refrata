"""Build every brand asset from the AI masters in build/brand/raw.

    python tools/brand/build_assets.py [--icon c1_prism]

Outputs (all committed under assets/):
    assets/icon.png              512 px app/window icon
    assets/icon.ico              Windows icon, PNG entries 16…256
    assets/brand/icon-1024.png   master icon
    assets/brand/symbol.png      prism symbol (no tile)
    assets/brand/hero.jpg        wide key visual (About card, README)
    assets/brand/about.jpg       about-card crop
    assets/installer/wizard*.bmp Inno Setup side images (100 % / 250 %)
    assets/installer/small*.bmp  Inno Setup header images
    build/brand/proof.png        small-size proof sheet (QA only)
"""
from __future__ import annotations

import argparse
import io
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "build" / "brand" / "raw"
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def scaled(img: QImage, w: int, h: int | None = None) -> QImage:
    h = h or w
    # step down in halves for crisp small sizes
    cur = img
    while cur.width() / 2 >= w and cur.height() / 2 >= h:
        cur = cur.scaled(cur.width() // 2, cur.height() // 2, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return cur.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba.data())


SMALL_MAX = 32  # sizes up to this use the simplified vector glyph


def small_glyph(S: int = 256) -> QImage:
    """Bold, simplified prism for 16–32 px: same tile, thick outline, solid beam."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QBrush, QLinearGradient, QPainterPath, QPen, QPolygonF

    img = QImage(S, S, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    tile = QPainterPath()
    m = S * 0.06
    tile.addRoundedRect(QRectF(m, m, S - 2 * m, S - 2 * m), S * 0.24, S * 0.24)
    g = QLinearGradient(0, 0, 0, S)
    g.setColorAt(0, QColor("#24242A"))
    g.setColorAt(1, QColor("#0B0B0D"))
    p.fillPath(tile, QBrush(g))
    p.setClipPath(tile)
    # beam: spectrum wedge from the tip to the right edge
    tip = QPointF(S * 0.66, S * 0.50)
    wedge = QPolygonF([tip, QPointF(S, S * 0.36), QPointF(S, S * 0.70)])
    bg = QLinearGradient(0, S * 0.36, 0, S * 0.70)
    bg.setColorAt(0, QColor("#FFB36B"))
    bg.setColorAt(0.5, QColor("#FF7A2F"))
    bg.setColorAt(1, QColor("#E0398C"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bg))
    p.drawPolygon(wedge)
    # incoming white ray
    p.setPen(QPen(QColor(255, 255, 255, 230), S * 0.035, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
    p.drawLine(QPointF(0, S * 0.52), QPointF(S * 0.34, S * 0.50))
    # glass triangle: translucent fill + thick white outline
    tri = QPainterPath()
    tri.addPolygon(QPolygonF([QPointF(S * 0.34, S * 0.27), QPointF(S * 0.70, S * 0.50), QPointF(S * 0.34, S * 0.73)]))
    tri.closeSubpath()
    fg = QLinearGradient(S * 0.34, 0, S * 0.70, 0)
    fg.setColorAt(0, QColor(255, 255, 255, 70))
    fg.setColorAt(1, QColor(255, 160, 90, 120))
    p.setBrush(QBrush(fg))
    p.setPen(QPen(QColor("#FFFFFF"), S * 0.07, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(tri)
    p.end()
    return img


def write_ico(img: QImage, path: Path):
    """ICO with PNG-compressed entries (supported by Windows Vista and later at every size)."""
    small = small_glyph()
    blobs = [png_bytes(scaled(small if s <= SMALL_MAX else img, s).convertToFormat(QImage.Format.Format_ARGB32))
             for s in ICO_SIZES]
    out = io.BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(blobs)))
    offset = 6 + 16 * len(blobs)
    for s, b in zip(ICO_SIZES, blobs):
        out.write(struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(b), offset))
        offset += len(b)
    for b in blobs:
        out.write(b)
    path.write_bytes(out.getvalue())


def crop(img: QImage, x: int, y: int, w: int, h: int) -> QImage:
    return img.copy(QRect(x, y, w, h))


def on_color(img: QImage, color: str) -> QImage:
    out = QImage(img.size(), QImage.Format.Format_RGB32)
    out.fill(QColor(color))
    p = QPainter(out)
    p.drawImage(0, 0, img)
    p.end()
    return out


def save_bmp(img: QImage, path: Path):
    img.convertToFormat(QImage.Format.Format_RGB888).save(str(path), "BMP")


def proof_sheet(icon: QImage, path: Path):
    sizes = [16, 20, 24, 32, 48, 64, 128, 256]
    W = sum(sizes) + 40 * (len(sizes) + 1)
    H = 2 * (256 + 80)
    sheet = QImage(W, H, QImage.Format.Format_RGB32)
    p = QPainter(sheet)
    for row, bg in enumerate(["#1B1B1F", "#F3EEE7"]):
        p.fillRect(0, row * H // 2, W, H // 2, QColor(bg))
        x = 40
        small = small_glyph()
        for s in sizes:
            p.drawImage(x, row * H // 2 + 40 + (256 - s), scaled(small if s <= SMALL_MAX else icon, s))
            x += s + 40
    p.end()
    sheet.save(str(path))


def centered(icon: QImage, w: int, h: int, frac: float) -> QImage:
    """The icon at frac of the shorter side, centred on a transparent canvas (Start tiles)."""
    out = QImage(w, h, QImage.Format.Format_ARGB32)
    out.fill(Qt.GlobalColor.transparent)
    s = int(min(w, h) * frac)
    p = QPainter(out)
    p.drawImage((w - s) // 2, (h - s) // 2, scaled(icon, s))
    p.end()
    return out


def msix_assets(icon: QImage, out: Path):
    """Visual assets of the MSIX/Store package (resource-qualified names, indexed by makepri)."""
    out.mkdir(parents=True, exist_ok=True)
    small = small_glyph()
    pick = lambda s: small if s <= SMALL_MAX else icon  # noqa: E731
    for scale in (100, 125, 150, 200, 400):
        k = scale / 100
        scaled(pick(round(44 * k)), round(44 * k)).save(str(out / f"Square44x44Logo.scale-{scale}.png"))
        scaled(icon, round(50 * k)).save(str(out / f"StoreLogo.scale-{scale}.png"))
        centered(icon, round(71 * k), round(71 * k), 0.72).save(str(out / f"SmallTile.scale-{scale}.png"))
        centered(icon, round(150 * k), round(150 * k), 0.62).save(str(out / f"Square150x150Logo.scale-{scale}.png"))
        centered(icon, round(310 * k), round(150 * k), 0.70).save(str(out / f"Wide310x150Logo.scale-{scale}.png"))
        centered(icon, round(310 * k), round(310 * k), 0.58).save(str(out / f"LargeTile.scale-{scale}.png"))
    for s in (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 256):
        img = scaled(pick(s), s)
        for suffix in ("", "_altform-unplated", "_altform-lightunplated"):
            img.save(str(out / f"Square44x44Logo.targetsize-{s}{suffix}.png"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--icon", default="c1_prism")
    ap.add_argument("--symbol", default="s1_symbol")
    ap.add_argument("--hero", default="h1_hero")
    args = ap.parse_args()
    QGuiApplication(sys.argv)

    assets, brand, inst = ROOT / "assets", ROOT / "assets" / "brand", ROOT / "assets" / "installer"
    for d in (brand, inst, ROOT / "build" / "brand"):
        d.mkdir(parents=True, exist_ok=True)

    icon = QImage(str(RAW / f"{args.icon}.png")).convertToFormat(QImage.Format.Format_ARGB32)
    if icon.isNull():
        raise SystemExit(f"missing {RAW / args.icon}.png — run tools/brand/generate.mjs first")
    icon.save(str(brand / "icon-1024.png"))
    scaled(icon, 512).save(str(assets / "icon.png"))
    scaled(icon, 256).save(str(brand / "icon-256.png"))
    scaled(icon, 96).save(str(brand / "icon-96.png"))
    write_ico(icon, assets / "icon.ico")
    msix_assets(icon, assets / "msix")

    sym = QImage(str(RAW / f"{args.symbol}.png"))
    if not sym.isNull():
        scaled(sym, 512).save(str(brand / "symbol.png"))

    hero = QImage(str(RAW / f"{args.hero}.png")).convertToFormat(QImage.Format.Format_RGB32)
    hero.save(str(brand / "hero.jpg"), "JPG", 88)
    # about card: wide strip around the beam/prism (centre ≈ y 450 of 1024)
    crop(hero, 0, 190, 1536, 560).save(str(brand / "about.jpg"), "JPG", 88)

    # Inno Setup: tall side image centred on the prism (x ≈ 1075), header icon on white
    for w, h, name in [(164, 314, "wizard.bmp"), (410, 785, "wizard@2.5x.bmp")]:
        cw = int(1024 * w / h)
        x0 = max(0, min(1536 - cw, 1075 - cw // 2))
        save_bmp(scaled(crop(hero, x0, 0, cw, 1024), w, h), inst / name)
    for s, name in [(55, "small.bmp"), (138, "small@2.5x.bmp")]:
        save_bmp(on_color(scaled(icon, s), "#FFFFFF"), inst / name)

    proof_sheet(icon, ROOT / "build" / "brand" / "proof.png")
    print("brand assets ok")


if __name__ == "__main__":
    main()
