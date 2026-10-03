"""
Zgodność ID zastępstw z aplikacją eLektron.

watch.py i aplikacja (SubstitutionMapper.kt) liczą ID niezależnie - jeśli się rozjadą,
to samo zastępstwo przyjdzie dwa razy (z pusha i z synchronizacji w aplikacji).
fixtures/zastepstwa-2026-10-01.ids to ID z aplikacji dla tej samej zapisanej strony
(w aplikacji pilnuje tego WatcherIdCompatibilityTest, który porównuje z tym samym plikiem).

Uruchomienie:  python -m unittest discover -s tests
"""
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import watch  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


class SubstitutionIdTest(unittest.TestCase):

    def setUp(self):
        self._http_get, self._archive = watch.http_get, watch.archive_page
        # Strona szkoły przychodzi w ISO-8859-2 (zapisana kopia jest w UTF-8).
        html = (FIXTURES / "zastepstwa-2026-10-01.html").read_text(encoding="utf-8")
        watch.http_get = lambda url: types.SimpleNamespace(content=html.encode("iso-8859-2"))
        watch.archive_page = lambda raw: None   # bez zapisu do archiwum/

    def tearDown(self):
        watch.http_get, watch.archive_page = self._http_get, self._archive

    def test_ids_match_app(self):
        expected = [l for l in (FIXTURES / "zastepstwa-2026-10-01.ids").read_text(encoding="utf-8").splitlines() if l.strip()]
        ids = [s["id"] for s in watch.fetch_substitutions()]
        self.assertEqual(32, len(expected))
        self.assertEqual(expected, ids)

    def test_java_hashcode(self):
        # Wartości z Kotlina/Javy: "".hashCode() == 0, "abc".hashCode() == 96354.
        self.assertEqual(0, watch.java_string_hashcode(""))
        self.assertEqual(96354, watch.java_string_hashcode("abc"))


if __name__ == "__main__":
    unittest.main()
