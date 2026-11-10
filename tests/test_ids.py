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

    def test_empty_description_is_still_a_substitution(self):
        # Prawdziwy wiersz z archiwum: "4 B(1) -" z zastępcą i uwagą "złączenie grup".
        html = ("<html><body><table><tr><td colspan=4>Zastępstwa w dniu 05.11.2099 czwartek</td></tr>"
                "<tr><td colspan=4>Jan Kowalski</td></tr>"
                "<tr><td>lekcja</td><td>opis</td><td>zastępca</td><td>uwagi</td></tr>"
                "<tr><td>2</td><td>4 B(1) -</td><td>Anna Nowak</td><td>złączenie grup</td></tr>"
                "</table><a href='http://www.vulcan.pl'>VULCAN</a></body></html>")
        watch.http_get = lambda url: types.SimpleNamespace(content=html.encode("iso-8859-2"))
        subs = watch.fetch_substitutions()
        self.assertEqual([("4B", 1, "")], [(s["classShortName"], s["groupNumber"], s["roomOrInfo"]) for s in subs])

    def test_java_hashcode(self):
        # Wartości z Kotlina/Javy: "".hashCode() == 0, "abc".hashCode() == 96354.
        self.assertEqual(0, watch.java_string_hashcode(""))
        self.assertEqual(96354, watch.java_string_hashcode("abc"))


if __name__ == "__main__":
    unittest.main()
