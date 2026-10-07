"""Every published photo carries no metadata beyond JFIF and the colour profile."""
import unittest

import helpers

ALLOWED = {0xE0: b"JFIF\x00", 0xE2: b"ICC_PROFILE\x00"}


def header_segments(data):
    """(marker, payload) for every segment before the first scan."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    i, found = 2, []
    while True:
        if data[i] != 0xFF:
            raise ValueError(f"bad marker at {i}")
        marker = data[i + 1]
        if marker == 0xDA:
            return found
        length = int.from_bytes(data[i + 2:i + 4], "big")
        found.append((marker, data[i + 4:i + 2 + length]))
        i += 2 + length


class PublishedPhotoTests(unittest.TestCase):
    def test_only_jfif_and_colour_profile(self):
        files = sorted((helpers.ROOT / "photos").glob("*/*.jpg"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.name):
                for marker, payload in header_segments(path.read_bytes()):
                    if 0xE0 <= marker <= 0xEF or marker == 0xFE:
                        prefix = ALLOWED.get(marker)
                        self.assertTrue(prefix and payload.startswith(prefix),
                                        f"{path.parent.name}/{path.name}: segment FF{marker:02X}")


if __name__ == "__main__":
    unittest.main()
