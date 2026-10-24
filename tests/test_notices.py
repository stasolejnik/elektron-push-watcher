"""Informacje szkoły: te same reguły co ZastepstwaParser.notices w aplikacji i push typu "notice"."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup  # noqa: E402

import watch  # noqa: E402

PAGE = """<html><body><table>
<tr><td>Zastępstwa w dniu 26.10.2099<br>Lekcje skrócone do 30 minut.<br>Publikacja: niedziela, godz. 13:17</td></tr>
<tr><td>Zastępstwa w dniu 27.10.2099<br>Publikacja godz. 16:10</td></tr>
</table></body></html>"""


class NoticesTest(unittest.TestCase):

    def test_parse_skips_publication_lines_and_days_without_text(self):
        notices = watch.parse_notices(BeautifulSoup(PAGE, "html.parser"))
        self.assertEqual(["2099-10-26"], [n["date"] for n in notices])
        changed = watch.parse_notices(BeautifulSoup(PAGE.replace("30 minut", "25 minut"), "html.parser"))
        self.assertNotEqual(notices[0]["id"], changed[0]["id"])

    def test_new_notice_is_pushed_once_but_not_on_the_first_run_with_notices(self):
        saved = {k: getattr(watch, k) for k in
                 ("STATE_PATH", "fetch_substitutions", "fetch_announcements", "fetch_notices", "get_access_token", "send_fcm")}
        env = os.environ.get("FCM_SERVICE_ACCOUNT_JSON")
        os.environ["FCM_SERVICE_ACCOUNT_JSON"] = "{}"
        tmp = tempfile.TemporaryDirectory()
        sent = []
        try:
            watch.STATE_PATH = Path(tmp.name) / "state.json"
            watch.STATE_PATH.write_text(json.dumps({"seen_subs": ["s1"], "seen_anns": ["a1"]}))
            notices = watch.parse_notices(BeautifulSoup(PAGE, "html.parser"))
            watch.fetch_substitutions = lambda: []
            watch.fetch_announcements = lambda: ([], True)
            watch.fetch_notices = lambda: list(notices)
            watch.get_access_token = lambda sa: ("token", "projekt")
            watch.send_fcm = lambda topic, data, project_id, token: sent.append((topic, data["type"], data["id"])) or True

            watch.main()   # stan bez "seen_notices" (watcher sprzed tej funkcji) - tylko zapis
            self.assertEqual([], sent)
            watch.main()
            self.assertEqual([], sent)
            changed = watch.parse_notices(BeautifulSoup(PAGE.replace("30 minut", "25 minut"), "html.parser"))
            watch.fetch_notices = lambda: list(changed)
            watch.main()
            self.assertEqual([("elektron-subs-zse-bydgoszcz", "notice", changed[0]["id"])], sent)
            watch.main()
            self.assertEqual(1, len(sent))
        finally:
            for k, v in saved.items():
                setattr(watch, k, v)
            if env is None:
                os.environ.pop("FCM_SERVICE_ACCOUNT_JSON", None)
            else:
                os.environ["FCM_SERVICE_ACCOUNT_JSON"] = env
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
