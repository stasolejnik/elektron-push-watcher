"""
Push, którego FCM nie przyjął, nie może przepaść: wpis trafia do "widzianych" dopiero po
udanym wysłaniu; nieudany jest ponawiany w kolejnych przebiegach, a po MAX_SEND_ATTEMPTS
pomijany (jeden uszkodzony wpis nie blokuje kolejki).

Bez sieci: podmienione pobieranie stron, token i send_fcm; stan w pliku tymczasowym.
Uruchomienie:  python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import watch  # noqa: E402


def sub(n):
    return {"id": f"20730|Jan Nowak|{n}|2A|0|abc", "date": "2026-10-05", "lessonNumber": n,
            "classShortName": "2A", "groupNumber": None, "roomOrInfo": "105",
            "substituteTeacher": "X", "notes": None, "originalTeacher": "Jan Nowak"}


def ann(n):
    return {"id": f"https://zse.bydgoszcz.pl/a-{n}.html", "title": f"Ogłoszenie {n}",
            "url": f"https://zse.bydgoszcz.pl/a-{n}.html", "publishedAt": "2026-10-05T10:00:00Z",
            "excerpt": None, "coverImageUrl": None, "source": "RSS_NEWS"}


class SendRetryTest(unittest.TestCase):

    def setUp(self):
        self._saved = {k: getattr(watch, k) for k in
                       ("STATE_PATH", "fetch_substitutions", "fetch_announcements", "get_access_token", "send_fcm")}
        self._env = os.environ.get("FCM_SERVICE_ACCOUNT_JSON")
        os.environ["FCM_SERVICE_ACCOUNT_JSON"] = "{}"   # atrapa - token i tak podmieniony
        self.tmp = tempfile.TemporaryDirectory()
        watch.STATE_PATH = Path(self.tmp.name) / "state.json"
        # Stan po wcześniejszych przebiegach: znane zastępstwo 1 i ogłoszenie 1.
        watch.STATE_PATH.write_text(json.dumps({"seen_subs": [sub(1)["id"]], "seen_anns": [ann(1)["id"]]}))
        self.subs = [sub(1)]
        self.anns = [ann(1)]
        self.failing = set()       # ID, których FCM nie przyjmuje
        self.sent = []             # ID przyjęte przez FCM
        self.attempts = []         # wszystkie próby wysłania
        watch.fetch_substitutions = lambda: list(self.subs)
        watch.fetch_announcements = lambda: (list(self.anns), True)
        watch.get_access_token = lambda sa: ("token", "projekt")

        def fake_send(topic, data, project_id, token):
            self.attempts.append(data["id"])
            if data["id"] in self.failing:
                return False
            self.sent.append(data["id"])
            return True
        watch.send_fcm = fake_send

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(watch, k, v)
        if self._env is None:
            os.environ.pop("FCM_SERVICE_ACCOUNT_JSON", None)
        else:
            os.environ["FCM_SERVICE_ACCOUNT_JSON"] = self._env
        self.tmp.cleanup()

    def state(self):
        return json.loads(watch.STATE_PATH.read_text())

    def test_successful_send_is_marked_seen(self):
        self.subs = [sub(1), sub(2)]
        watch.main()
        self.assertEqual([sub(2)["id"]], self.sent)
        self.assertIn(sub(2)["id"], self.state()["seen_subs"])
        watch.main()                                      # kolejny przebieg - bez duplikatu
        self.assertEqual(1, self.attempts.count(sub(2)["id"]))

    def test_failed_send_is_retried_in_next_run(self):
        # Dawniej: stan zapisany mimo błędu FCM -> push przepadał na zawsze.
        self.subs = [sub(1), sub(2)]
        self.anns = [ann(1), ann(2)]
        self.failing = {sub(2)["id"], ann(2)["id"]}
        watch.main()
        st = self.state()
        self.assertNotIn(sub(2)["id"], st["seen_subs"])
        self.assertNotIn(ann(2)["id"], st["seen_anns"])
        self.assertIn(sub(1)["id"], st["seen_subs"])    # znane zostają znane

        self.failing = set()                              # FCM znowu działa
        watch.main()
        self.assertEqual([sub(2)["id"], ann(2)["id"]], self.sent)
        st = self.state()
        self.assertIn(sub(2)["id"], st["seen_subs"])
        self.assertIn(ann(2)["id"], st["seen_anns"])
        self.assertEqual({}, st["failed_subs"])
        self.assertEqual({}, st["failed_anns"])

    def test_permanently_failing_entry_is_skipped_after_max_attempts(self):
        self.subs = [sub(1), sub(2)]
        self.failing = {sub(2)["id"]}
        for _ in range(watch.MAX_SEND_ATTEMPTS + 2):
            watch.main()
        self.assertEqual(watch.MAX_SEND_ATTEMPTS, self.attempts.count(sub(2)["id"]))
        self.assertIn(sub(2)["id"], self.state()["seen_subs"])

    def test_broken_entry_does_not_block_others(self):
        self.subs = [sub(1), sub(2)]
        self.failing = {sub(2)["id"]}
        watch.main()
        self.subs = [sub(1), sub(2), sub(3)]
        watch.main()
        self.assertEqual([sub(3)["id"]], self.sent)
        self.assertIn(sub(3)["id"], self.state()["seen_subs"])

    def test_all_failed_is_not_treated_as_first_run(self):
        # Gdy nie wysłało się nic, a listy "widzianych" są puste, następny przebieg nie może
        # uznać się za pierwsze uruchomienie (które oznacza wszystko jako widziane bez wysyłki).
        self.anns = []
        watch.STATE_PATH.write_text(json.dumps({"seen_subs": [sub(1)["id"]], "seen_anns": []}))
        self.subs = [sub(5)]                              # sub(1) zniknął ze strony
        self.failing = {sub(5)["id"]}
        watch.main()
        self.assertEqual([], self.state()["seen_subs"])
        self.failing = set()
        watch.main()
        self.assertEqual([sub(5)["id"]], self.sent)

    def test_first_run_sends_nothing(self):
        watch.STATE_PATH.write_text(json.dumps({"seen_subs": [], "seen_anns": []}))
        self.subs = [sub(1), sub(2)]
        watch.main()
        self.assertEqual([], self.attempts)
        self.assertEqual(sorted([sub(1)["id"], sub(2)["id"]]), self.state()["seen_subs"])


if __name__ == "__main__":
    unittest.main()
