#!/usr/bin/env python3
"""
eLektron push-watcher — funkcja #7.

Uruchamiane cyklicznie przez GitHub Actions (darmowe, bez limitu na publicznym repo).
Sprawdza zastępstwa i ogłoszenia ZSE Bydgoszcz, porównuje z poprzednim stanem
(state.json, commitowany z powrotem do repo przez workflow) i dla nowości publikuje
na temat FCM — appka odbiera to w ElektronFirebaseMessagingService.

WAŻNE: formuły ID muszą być identyczne z Kotlinem (SubstitutionMapper.kt,
AnnouncementMapper.kt), inaczej appka zobaczy "nowe" zastępstwo dwa razy — raz
z pusha, raz gdy sama je zeskrobie za 15 minut. Stąd java_string_hashcode() niżej —
to dokładnie ten sam algorytm co Kotlin/Java String.hashCode().
"""
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SCHOOL_ID = "zse-bydgoszcz"
SUBS_URL = "https://zastepstwa.zse.bydgoszcz.pl/index.html"
RSS_URLS = [
    ("rss_news", "https://zse.bydgoszcz.pl/rss.xml"),
    ("rss_latest", "https://zse.bydgoszcz.pl/rsslatest.xml"),
]
STATE_PATH = Path(__file__).parent / "state.json"
USER_AGENT = "eLektron-push-watcher/0.1 (+https://gitlab.com/stasolejnik/atomik)"

DATE_RE = re.compile(r"Zastępstwa w dniu\s+(\d{2}\.\d{2}\.\d{4})")
DESC_RE = re.compile(r"^(\d+)\s*([A-Z])(?:\((\d+)\))?\s*-\s*(.+)$")


def java_string_hashcode(s: str) -> int:
    """Replika Kotlin/Java String.hashCode() — 32-bit, potem interpretowana jako unsigned."""
    h = 0
    for ch in s:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    return h


def epoch_day(d: date) -> int:
    return (d - date(1970, 1, 1)).days


def substitution_id(date_obj: date, original_teacher: str, lesson_number: int,
                     class_short: str, group_number, room_or_info: str, substitute_teacher: str) -> str:
    room_hash = format(java_string_hashcode(f"{room_or_info}|{substitute_teacher or ''}"), "x")
    group = group_number if group_number is not None else 0
    return f"{epoch_day(date_obj)}|{original_teacher}|{lesson_number}|{class_short}|{group}|{room_hash}"


def fetch_substitutions() -> list[dict]:
    resp = requests.get(SUBS_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    html = resp.content.decode("iso-8859-2", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        print("Brak <table> w dokumencie zastępstw", file=sys.stderr)
        return []
    table = tables[0]

    out = []
    current_date_raw = None
    current_teacher = None

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue

        if len(cells) == 1:
            cls = (cells[0].get("class") or [])
            text = cells[0].get_text(strip=True)
            if "st0" in cls:
                m = DATE_RE.search(text)
                if m:
                    current_date_raw = m.group(1)
            elif "st1" in cls:
                current_teacher = text or None
            continue

        if len(cells) != 4:
            continue

        texts = [c.get_text(strip=True) for c in cells]
        if {t.lower() for t in texts} == {"lekcja", "opis", "zastępca", "uwagi"}:
            continue

        first_cls = set(cells[0].get("class") or [])
        if "st7" not in first_cls and "st10" not in first_cls:
            continue

        try:
            lesson_no = int(texts[0])
        except ValueError:
            continue
        if not (0 <= lesson_no <= 12):
            continue
        if current_date_raw is None or current_teacher is None:
            continue

        m = DESC_RE.match(texts[1])
        if not m:
            continue
        class_short = f"{m.group(1)}{m.group(2)}"
        group = int(m.group(3)) if m.group(3) else None
        room_or_info = m.group(4).strip()
        substitute_teacher = texts[2] or None
        notes = texts[3] or None

        date_obj = datetime.strptime(current_date_raw, "%d.%m.%Y").date()
        sub_id = substitution_id(date_obj, current_teacher, lesson_no, class_short,
                                  group, room_or_info, substitute_teacher)
        out.append({
            "id": sub_id,
            "date": date_obj.isoformat(),
            "lessonNumber": lesson_no,
            "classShortName": class_short,
            "groupNumber": group,
            "roomOrInfo": room_or_info,
            "substituteTeacher": substitute_teacher,
            "notes": notes,
            "originalTeacher": current_teacher,
        })

    print(f"Sparsowano {len(out)} zastępstw")
    return out


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def fetch_announcements() -> list[dict]:
    out = []
    for source, url in RSS_URLS:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "xml")
        for item in soup.find_all("item"):
            guid_tag = item.find("guid")
            link_tag = item.find("link")
            guid = (guid_tag.get_text(strip=True) if guid_tag else None) or \
                   (link_tag.get_text(strip=True) if link_tag else None)
            if not guid:
                continue
            title = (item.find("title").get_text(strip=True) if item.find("title") else "")
            link = (link_tag.get_text(strip=True) if link_tag else "")
            desc_tag = item.find("description")
            description = desc_tag.get_text() if desc_tag else ""
            excerpt = strip_html(description)[:280] or None
            img_match = re.search(r'<img[^>]+src="([^"]+)"', description or "")
            cover = img_match.group(1) if img_match else None
            pub_tag = item.find("pubDate")
            pub_raw = pub_tag.get_text(strip=True) if pub_tag else ""
            published_at = parse_pubdate(pub_raw)
            out.append({
                "id": guid,
                "title": title,
                "url": link,
                "publishedAt": published_at,
                "excerpt": excerpt,
                "coverImageUrl": cover,
                "source": "RSS_NEWS" if source == "rss_news" else "RSS_LATEST",
            })
    print(f"Sparsowano {len(out)} ogłoszeń RSS")
    return out


def parse_pubdate(raw: str) -> str:
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"seen_subs": [], "seen_anns": []}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def send_fcm(topic: str, data: dict, project_id: str, access_token: str) -> None:
    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    payload = {
        "message": {
            "topic": topic,
            "android": {"priority": "high"},
            "data": {k: str(v) for k, v in data.items() if v is not None},
        }
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json=payload, timeout=20,
    )
    if not resp.ok:
        print(f"FCM send błąd ({resp.status_code}): {resp.text}", file=sys.stderr)


def get_access_token(service_account_json: str) -> tuple[str, str]:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    info = json.loads(service_account_json)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/firebase.messaging"]
    )
    creds.refresh(Request())
    return creds.token, info["project_id"]


def main() -> None:
    sa_json = os.environ.get("FCM_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        print("Brak sekretu FCM_SERVICE_ACCOUNT_JSON — patrz PORADNIK_PUSH.md", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    seen_subs = set(state.get("seen_subs", []))
    seen_anns = set(state.get("seen_anns", []))

    subs = fetch_substitutions()
    anns = fetch_announcements()

    fresh_subs = [s for s in subs if s["id"] not in seen_subs]
    fresh_anns = [a for a in anns if a["id"] not in seen_anns]

    print(f"Nowych zastępstw: {len(fresh_subs)}, nowych ogłoszeń: {len(fresh_anns)}")

    # Pierwsze uruchomienie (pusty state.json) — nie zalewamy pushami całej historii,
    # tylko zapisujemy snapshot. Dokładnie ta sama zasada co initial_sync_done w appce.
    is_first_run = not state.get("seen_subs") and not state.get("seen_anns")

    if (fresh_subs or fresh_anns) and not is_first_run:
        token, project_id = get_access_token(sa_json)
        for s in fresh_subs:
            payload = dict(s)
            payload["type"] = "substitution"
            send_fcm(f"elektron-subs-{SCHOOL_ID}", payload, project_id, token)
        for a in fresh_anns:
            payload = dict(a)
            payload["type"] = "announcement"
            send_fcm(f"elektron-anns-{SCHOOL_ID}", payload, project_id, token)

    state["seen_subs"] = list({s["id"] for s in subs})
    state["seen_anns"] = list({a["id"] for a in anns})
    save_state(state)


if __name__ == "__main__":
    main()
