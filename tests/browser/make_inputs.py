"""Make the two input photos for the browser check of admin/lib/image.js.

Usage:  py tests\\browser\\make_inputs.py

Writes into tests/browser/out/ (ignored by git):
  rot_gps.jpg  3000x2000 colour photo stored sideways: EXIF orientation 6 (shown 2000x3000),
               a GPS block (zero coordinates, never a real place), Model "TestCam" and a date.
               A red block marks the stored top-left corner, so the check can tell which way it was turned.
  gray.jpg     2000x1500 neutral grey gradient (R = G = B), no EXIF.
"""
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent / "out"

ROT_SIZE = (3000, 2000)
GRAY_SIZE = (2000, 1500)
MARKER = (0, 0, 600, 400)  # red block in the stored top-left corner of rot_gps.jpg
MARKER_COLOR = (230, 30, 30)


def ramp(size, vertical):
    """A 0..255 gradient of the given size, along the vertical or the horizontal axis."""
    base = Image.linear_gradient("L")  # 256x256, black at the top, white at the bottom
    if not vertical:
        base = base.rotate(90, expand=True)  # turned counter-clockwise: black at the left, white at the right
    return base.resize(size, Image.BILINEAR)


def make_rot_gps(path):
    r, g = ramp(ROT_SIZE, vertical=False), ramp(ROT_SIZE, vertical=True)
    b = Image.new("L", ROT_SIZE, 140)
    im = Image.merge("RGB", (r, g, b))
    im.paste(MARKER_COLOR, MARKER)

    exif = Image.Exif()
    exif[0x0110] = "TestCam"
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    exif[0x0132] = "2025:11:30 23:13:46"
    place = exif.get_ifd(0x8825)
    place[1], place[2] = "N", (0.0, 0.0, 0.0)
    place[3], place[4] = "E", (0.0, 0.0, 0.0)
    im.save(path, "JPEG", quality=90, exif=exif)


def make_gray(path):
    Image.merge("RGB", (ramp(GRAY_SIZE, vertical=False),) * 3).save(path, "JPEG", quality=90)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    make_rot_gps(OUT / "rot_gps.jpg")
    make_gray(OUT / "gray.jpg")
    print(f"wrote {OUT / 'rot_gps.jpg'} and {OUT / 'gray.jpg'}")


if __name__ == "__main__":
    main()
