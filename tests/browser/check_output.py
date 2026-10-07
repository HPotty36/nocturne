"""Check what the browser harness (tests/browser/process.html) made of the test photos.

Usage:  py tests\\browser\\check_output.py

Reads tests/browser/out/results.json (written by the harness through serve.py) and prints OK
when every check passes, exit code 0. Otherwise it prints each failed check and exits with 1.
Needs the photos from make_inputs.py: rot_gps (EXIF orientation 6, GPS, TestCam) and gray.
"""
import base64
import io
import json
import re
import sys
from pathlib import Path

from PIL import Image

RESULTS = Path(__file__).resolve().parent / "out" / "results.json"

# Bytes that must not show up in a web copy. A bare b"GPS" is not searched for: compressed data can contain it by chance.
LEAKED = (b"Exif\x00\x00", b"TestCam")


def decode(item, kind):
    """Bytes of a result's full or thumb JPEG."""
    return base64.b64decode(item[kind])


SCAN_END = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")  # the next real marker after compressed scan data


def segments(data):
    """([(marker, payload), ...], offset after the last byte read) for a JPEG: every segment, also those
    between progressive scans, up to EOI. Compressed scan data is skipped. ValueError if it is not complete."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("does not start with SOI")
    found, pos = [], 2
    while True:
        if pos + 2 > len(data) or data[pos] != 0xFF:
            raise ValueError(f"no marker at byte {pos}")
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker == 0xD9:
            return found, pos + 2
        size = int.from_bytes(data[pos + 2:pos + 4], "big")
        if size < 2 or pos + 2 + size > len(data):
            raise ValueError(f"bad segment length at byte {pos}")
        found.append((marker, data[pos + 4:pos + 2 + size]))
        pos += 2 + size
        if marker == 0xDA:
            after_scan = SCAN_END.search(data, pos)
            if not after_scan:
                raise ValueError("scan data without an end")
            pos = after_scan.start()


def allowed(marker, payload):
    """Only JFIF (APP0) and the colour profile (APP2) may be there; every other APPn and COM must not."""
    if marker == 0xE0:
        return payload.startswith(b"JFIF\x00")
    if marker == 0xE2:
        return payload.startswith(b"ICC_PROFILE\x00")
    return not (0xE1 <= marker <= 0xEF or marker == 0xFE)


def metadata_problems(label, data):
    """Why this JPEG still carries metadata (empty list when it does not)."""
    problems = [f"{label} contains {needle!r}" for needle in LEAKED if needle in data]
    with Image.open(io.BytesIO(data)) as im:
        if im.format != "JPEG":
            problems.append(f"{label} is {im.format}, not JPEG")
        if im.getexif():
            problems.append(f"{label} has EXIF tags: {sorted(im.getexif())}")
    try:
        found, end = segments(data)
    except ValueError as error:
        return problems + [f"{label} is not a well-formed JPEG: {error}"]
    for marker, payload in found:
        if not allowed(marker, payload):
            name = "COM" if marker == 0xFE else f"APP{marker - 0xE0}"
            problems.append(f"{label} has segment {name} ({len(payload)} bytes); only APP0 JFIF and APP2 ICC_PROFILE are allowed")
    if end != len(data):
        problems.append(f"{label} has {len(data) - end} bytes after the end of the image")
    return problems


def size_of(data):
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def is_red(rgb):
    return rgb[0] > 180 and rgb[1] < 100 and rgb[2] < 100


def corner_pixels(data):
    """Colours 20 px inside the four corners: top-left, top-right, bottom-left, bottom-right."""
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        w, h = im.size
        return [im.getpixel(p) for p in ((20, 20), (w - 21, 20), (20, h - 21), (w - 21, h - 21))]


def main():
    if not RESULTS.exists():
        print(f"FAIL: {RESULTS} does not exist; run the harness first (see tests/browser/process.html)")
        return 1
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    by_name = {item["name"]: item for item in results}
    failures = []

    def check(ok, what):
        if not ok:
            failures.append(what)

    for item in results:
        name = item["name"]
        for kind in ("full", "thumb"):
            try:
                failures.extend(metadata_problems(f"{name} {kind}", decode(item, kind)))
            except Exception as error:  # a broken JPEG is a failed check, not a crash
                failures.append(f"{name} {kind} cannot be read: {error}")
            else:
                if kind == "full":
                    full_size = size_of(decode(item, "full"))
                    check(full_size == (item["width"], item["height"]),
                          f"{name}: width/height {item['width']}x{item['height']} differ from the full JPEG {full_size}")

    check("rot_gps" in by_name, "results has no rot_gps")
    if "rot_gps" in by_name:
        item = by_name["rot_gps"]
        try:
            check(size_of(decode(item, "full")) == (1600, 2400), f"rot_gps full is {size_of(decode(item, 'full'))}, expected (1600, 2400)")
            check(size_of(decode(item, "thumb")) == (667, 1000), f"rot_gps thumb is {size_of(decode(item, 'thumb'))}, expected (667, 1000)")
            top_left, top_right, _, _ = corner_pixels(decode(item, "full"))
            check(is_red(top_right) and not is_red(top_left),
                  f"rot_gps was not turned clockwise: top-left {top_left}, top-right {top_right}; the red marker belongs top-right")
        except Exception as error:
            failures.append(f"rot_gps cannot be measured: {error}")
        check(item["hints"].get("camera", "").startswith("TestCam"), f"rot_gps hints.camera is {item['hints'].get('camera')!r}, expected TestCam...")
        check(item["hints"].get("date") == "2025.11.30", f"rot_gps hints.date is {item['hints'].get('date')!r}, expected '2025.11.30'")
        check(item["grayscale"] is False, f"rot_gps grayscale is {item['grayscale']!r}, expected False")

    check("gray" in by_name, "results has no gray")
    if "gray" in by_name:
        item = by_name["gray"]
        check(item["grayscale"] is True, f"gray grayscale is {item['grayscale']!r}, expected True")
        try:  # 2000x1500 is below 2400: the full copy is never enlarged; the thumb is cut to 1000
            check(size_of(decode(item, "full")) == (2000, 1500), f"gray full is {size_of(decode(item, 'full'))}, expected (2000, 1500): never upscale")
            check(size_of(decode(item, "thumb")) == (1000, 750), f"gray thumb is {size_of(decode(item, 'thumb'))}, expected (1000, 750)")
        except Exception as error:
            failures.append(f"gray cannot be measured: {error}")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
