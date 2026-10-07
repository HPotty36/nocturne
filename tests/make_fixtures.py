"""Rebuild tests/fixtures: EXIF test JPEGs in both byte orders and what they should read as.

Usage:  py tests\\make_fixtures.py
"""
import json

import helpers


def main():
    helpers.FIXTURES.mkdir(parents=True, exist_ok=True)
    helpers.make_exif_jpeg(helpers.FIXTURES / "exif_le.jpg", "<")
    helpers.make_exif_jpeg(helpers.FIXTURES / "exif_be.jpg", ">")
    expected = {"date": "2025.11.30", "camera": "TestCam · f/1.6 · 1/121s · ISO 100"}
    (helpers.FIXTURES / "exif_expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
