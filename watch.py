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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SCHOOL_ID = "zse-bydgoszcz"
SUBS_URL = "https://zastepstwa.zse.bydgoszcz.pl/index.html"
RSS_URLS = [
    ("rss_news", "https://zse.bydgoszcz.pl/rss.xml"),
    ("rss_latest", "https://zse.bydgoszcz.pl/rsslatest.xml"),
]
STATE_PATH = Path(__file__).parent / "state.json"
USER_AGENT = "eLektron-push-watcher/0.2 (+https://github.com/stasolejnik/elektron-push-watcher)"

# (połączenie, odczyt) w sekundach. Serwer szkoły bywa chwilowo nieosiągalny z maszyn
# GitHuba - lepiej szybko ponowić niż czekać, bo przebieg i tak powtórzy się za 5 min.
TIMEOUT = (10, 30)


class SourceUnavailable(Exception):
    """Strona szkoły nie odpowiedziała (sieć, timeout, 5xx). To nie jest błąd watchera."""


def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3, connect=3, read=2, status=3,
        backoff_factor=2,  # 0 s, 4 s, 8 s
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = USER_AGENT
    return session


SESSION = _make_session()


def http_get(url: str) -> requests.Response:
    try:
        resp = SESSION.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp
    except (requests.ConnectionError, requests.Timeout) as e:
        raise SourceUnavailable(f"{url}: brak połączenia ({type(e).__name__})") from e
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        if code >= 500 or code == 429:
            raise SourceUnavailable(f"{url}: serwer zwrócił {code}") from e
        raise


def warn(msg: str) -> None:
    """Ostrzeżenie widoczne w podsumowaniu przebiegu GitHub Actions (bez maila o błędzie)."""
    print(f"::warning::{msg}")


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


ARCHIVE_DIR = Path(__file__).parent / "archiwum" / "zastepstwa"


def archive_page(raw: bytes) -> None:
    """Zapisuje stronę zastępstw do archiwum/zastepstwa/, gdy szkoła ją zmieniła (po sumie
    SHA-256). Archiwum służy do testów parsera na prawdziwych stronach z wielu dni (1.0).
    Błąd zapisu nigdy nie przerywa wysyłania pushy."""
    try:
        import hashlib
        from datetime import datetime
        from zoneinfo import ZoneInfo
        digest = hashlib.sha256(raw).hexdigest()
        last_file = ARCHIVE_DIR / ".ostatni-sha256"
        if last_file.exists() and last_file.read_text().strip() == digest:
            return
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d_%H%M%S")
        (ARCHIVE_DIR / f"{stamp}.html").write_bytes(raw)   # oryginalne bajty (ISO-8859-2)
        last_file.write_text(digest + "\n")
        print(f"Archiwum: zapisano {stamp}.html")
    except Exception as e:  # noqa: BLE001 - archiwum jest dodatkiem
        print(f"::warning::Archiwum stron zastępstw: {e}")


def fetch_substitutions() -> list[dict]:
    resp = http_get(SUBS_URL)
    archive_page(resp.content)
    html = resp.content.decode("iso-8859-2", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        print("Brak <table> w dokumencie zastępstw", file=sys.stderr)
        return []

    # WAŻNE: klasy CSS (st0, st7, st14...) Optivum numeruje od nowa przy każdym eksporcie -
    # zmieniają się z dnia na dzień. Dawniej wiersze rozpoznawane po "st7"/"st10" - 30.09.2026
    # z 32 zastępstw odczytane zostało 1. Teraz wyłącznie struktura tabeli (jak w aplikacji):
    #  - 1 komórka + "Zastępstwa w dniu dd.mm.rrrr" -> data,
    #  - 1 komórka tuż przed nagłówkami kolumn (albo tuż przed pierwszym wpisem) -> nauczyciel,
    #  - 4 komórki, pierwsza to numer lekcji -> wpis; puste wiersze pomijane.
    out = []
    current_date_raw = None
    current_teacher = None
    candidate_teacher = None

    for row in (tr for t in tables for tr in t.find_all("tr")):
        cells = row.find_all(["td", "th"], recursive=False)
        if not cells:
            continue
        texts = [c.get_text(strip=True) for c in cells]

        if len(cells) == 1:
            m = DATE_RE.search(texts[0])
            if m:
                current_date_raw = m.group(1)
                current_teacher = None
                candidate_teacher = None
            elif texts[0]:
                candidate_teacher = texts[0]
            continue

        if len(cells) != 4:
            continue
        if not any(texts):
            continue
        if {t.lower() for t in texts} == {"lekcja", "opis", "zastępca", "uwagi"}:
            current_teacher = candidate_teacher
            candidate_teacher = None
            continue

        try:
            lesson_no = int(texts[0])
        except ValueError:
            continue
        # Blok nauczyciela bez wiersza nagłówków kolumn - nazwisko tuż nad wpisem.
        if candidate_teacher is not None:
            current_teacher = candidate_teacher
            candidate_teacher = None
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


def fetch_announcements() -> tuple[list[dict], bool]:
    """Zwraca (ogłoszenia, czy_wszystkie_kanały_pobrane). Niedostępny kanał jest pomijany."""
    out = []
    complete = True
    for source, url in RSS_URLS:
        try:
            resp = http_get(url)
        except SourceUnavailable as e:
            warn(f"Pominięto kanał RSS - {e}")
            complete = False
            continue
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
    return out, complete


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


def send_fcm(topic: str, data: dict, project_id: str, access_token: str) -> bool:
    """Wysyła push na temat FCM. True - FCM przyjął wiadomość; False - nie udało się."""
    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    payload = {
        "message": {
            "topic": topic,
            "android": {"priority": "high"},
            "data": {k: str(v) for k, v in data.items() if v is not None},
        }
    }
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload, timeout=20,
        )
    except (requests.ConnectionError, requests.Timeout) as e:
        warn(f"FCM: nie wysłano powiadomienia ({type(e).__name__})")
        return False
    if not resp.ok:
        print(f"FCM send błąd ({resp.status_code}): {resp.text}", file=sys.stderr)
        return False
    return True


# Ile przebiegów z rzędu ponawiamy wysłanie tego samego wpisu, zanim go pominiemy
# (jeden uszkodzony wpis, np. odrzucany przez FCM, nie może blokować kolejki na zawsze).
MAX_SEND_ATTEMPTS = 3


def send_fresh(items: list[dict], kind: str, topic: str, failures: dict,
               project_id: str, token: str) -> set[str]:
    """
    Wysyła nowe wpisy. Zwraca ID, które NIE trafiają jeszcze do "widzianych" (nieudane,
    do ponowienia w następnym przebiegu). [failures] (id -> liczba nieudanych prób)
    jest aktualizowane w miejscu; po MAX_SEND_ATTEMPTS wpis jest pomijany na stałe.
    """
    retry = set()
    for item in items:
        payload = dict(item)
        payload["type"] = kind
        if send_fcm(topic, payload, project_id, token):
            failures.pop(item["id"], None)
            continue
        attempts = failures.get(item["id"], 0) + 1
        if attempts >= MAX_SEND_ATTEMPTS:
            warn(f"FCM: pomijam {kind} {item['id']} po {attempts} nieudanych próbach")
            failures.pop(item["id"], None)
        else:
            failures[item["id"]] = attempts
            retry.add(item["id"])
    return retry


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
    # ID -> liczba nieudanych prób wysłania (patrz send_fresh).
    failed_subs = dict(state.get("failed_subs", {}))
    failed_anns = dict(state.get("failed_anns", {}))

    # Każde źródło osobno: awaria jednego nie blokuje drugiego. Jeśli źródło nie odpowiedziało,
    # jego część stanu zostaje bez zmian - inaczej następny udany przebieg uznałby wszystkie
    # zastępstwa/ogłoszenia za nowe i rozesłał je ponownie.
    try:
        subs = fetch_substitutions()
    except SourceUnavailable as e:
        warn(f"Zastępstwa niedostępne - {e}")
        subs = None
    anns, anns_complete = fetch_announcements()

    if subs is None and not anns and not anns_complete:
        warn("Strony szkoły nie odpowiadają - spróbuję w następnym przebiegu.")
        return

    fresh_subs = [s for s in (subs or []) if s["id"] not in seen_subs]
    # To samo ogłoszenie bywa w obu kanałach RSS - jedno powiadomienie na wpis.
    fresh_anns = list({a["id"]: a for a in anns if a["id"] not in seen_anns}.values())

    print(f"Nowych zastępstw: {len(fresh_subs)}, nowych ogłoszeń: {len(fresh_anns)}")

    # Pierwsze uruchomienie (pusty state.json) — nie zalewamy pushami całej historii,
    # tylko zapisujemy snapshot. Dokładnie ta sama zasada co initial_sync_done w appce.
    # Liczniki nieudanych prób też się liczą: gdy WSZYSTKIE nowości się nie wysłały, listy
    # "widzianych" mogą być puste - to nie jest pierwsze uruchomienie.
    is_first_run = (not state.get("seen_subs") and not state.get("seen_anns")
                    and not failed_subs and not failed_anns)

    if (fresh_subs or fresh_anns) and not is_first_run:
        try:
            token, project_id = get_access_token(sa_json)
        except (requests.ConnectionError, requests.Timeout, OSError) as e:
            # Stan nie jest zapisywany - te same nowości zostaną wysłane w następnym przebiegu.
            warn(f"Brak połączenia z Google (token FCM): {type(e).__name__} - ponowię za chwilę.")
            return
        # Do "widzianych" trafia wpis dopiero po udanym wysłaniu. Dawniej stan zapisywał się
        # zawsze, więc push, którego FCM nie przyjął, przepadał na zawsze.
        retry_subs = send_fresh(fresh_subs, "substitution", f"elektron-subs-{SCHOOL_ID}",
                                failed_subs, project_id, token)
        retry_anns = send_fresh(fresh_anns, "announcement", f"elektron-anns-{SCHOOL_ID}",
                                failed_anns, project_id, token)
    else:
        retry_subs, retry_anns = set(), set()

    if subs is not None:
        current_subs = {s["id"] for s in subs}
        state["seen_subs"] = sorted(current_subs - retry_subs)
        # Liczniki prób tylko dla wpisów wciąż obecnych na stronie.
        failed_subs = {k: v for k, v in failed_subs.items() if k in current_subs}
    # Pełny odczyt RSS = lista jest aktualna (stare wpisy wypadają). Częściowy = tylko dopisujemy.
    anns_ids = {a["id"] for a in anns}
    state["seen_anns"] = sorted((anns_ids if anns_complete else seen_anns | anns_ids) - retry_anns)
    if anns_complete:
        failed_anns = {k: v for k, v in failed_anns.items() if k in anns_ids}
    state["failed_subs"] = failed_subs
    state["failed_anns"] = failed_anns
    save_state(state)

if __name__ == "__main__":
    main()
