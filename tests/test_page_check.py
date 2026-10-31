"""Ucięta strona zastępstw i strona awarii nie mogą zmieniać stanu - ta sama kontrola co w aplikacji."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watch  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "zastepstwa_2026-10-22.html"


def fetch(content: bytes) -> list[dict]:
    with mock.patch.object(watch, "http_get", return_value=mock.Mock(content=content)), \
            mock.patch.object(watch, "archive_page"):
        return watch.fetch_substitutions()


class PageCheckTest(unittest.TestCase):

    def test_full_page_passes(self):
        self.assertEqual(32, len(fetch(FIXTURE.read_bytes())))

    def test_truncated_page_is_rejected(self):
        page = FIXTURE.read_bytes()
        with self.assertRaises(watch.SourceUnavailable):
            fetch(page[:int(len(page) * 0.6)])

    def test_error_page_with_http_200_is_rejected(self):
        with self.assertRaises(watch.SourceUnavailable):
            fetch("<html><head><title>Błąd serwera</title></head><body>Przerwa techniczna</body></html>".encode("iso-8859-2"))

    def test_empty_export_on_a_day_off_passes(self):
        page = "<html><head><title>Inf. o zast.</title></head><body></body></html>".encode("iso-8859-2")
        self.assertEqual([], fetch(page))


if __name__ == "__main__":
    unittest.main()
