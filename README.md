# eLektron push watcher

[![Watcher](https://github.com/stasolejnik/elektron-push-watcher/actions/workflows/watch.yml/badge.svg)](https://github.com/stasolejnik/elektron-push-watcher/actions/workflows/watch.yml)

Serwer powiadomień dla aplikacji [eLektron](https://github.com/stasolejnik/elektron) -
nieoficjalnej aplikacji na Androida z planem lekcji, zastępstwami i ogłoszeniami ZSE w Bydgoszczy.

Co około 5 minut sprawdza zastępstwa i ogłoszenia szkoły i, gdy pojawi się coś nowego, wysyła
powiadomienie push przez Firebase Cloud Messaging (FCM). Działa na GitHub Actions - bez własnego
serwera i bez kosztów.

## Jak to działa

```
 strona zastępstw ──┐
                    ├─► watch.py ──► porównanie z state.json ──► nowe wpisy ──► FCM ──► aplikacje
 kanały RSS szkoły ─┘                        │
                                             └─► zapis stanu (commit do repozytorium)
```

1. Pobiera stronę zastępstw i dwa kanały RSS ogłoszeń.
2. Liczy identyfikatory wpisów i porównuje je z listą „widzianych” w `state.json`.
3. Dla nowych wpisów publikuje wiadomość na temat FCM (`elektron-subs-zse-bydgoszcz` dla
   zastępstw, `elektron-anns-zse-bydgoszcz` dla ogłoszeń). Aplikacja filtruje je po klasie i grupach.
4. Zapisuje stan z powrotem do repozytorium (`state.json`).

Aplikacja i tak sama pobiera dane co kilkanaście minut - push skraca tylko czas, po którym
użytkownik dowiaduje się o zastępstwie.

## Niezawodność

- **Ponawianie.** Wpis trafia do „widzianych” dopiero po przyjęciu przez FCM. Nieudane wysłanie
  jest ponawiane w kolejnych przebiegach (do 3 prób, potem wpis jest pomijany, żeby jeden
  uszkodzony wpis nie blokował kolejki). Liczniki prób są w `state.json`.
- **Pierwsze uruchomienie** (pusty stan) zapisuje tylko snapshot - nie zalewa użytkowników
  powiadomieniami o całej historii.
- **Awaria źródła.** Gdy strona szkoły nie odpowiada, stan tego źródła zostaje bez zmian, więc
  następny udany przebieg nie uzna wszystkich wpisów za nowe.
- **Jeden przebieg naraz.** Workflow używa kolejki (`concurrency`), pracuje na najnowszym stanie
  gałęzi `main` i ma limit 5 minut na przebieg.
- **Identyczne ID jak w aplikacji.** Identyfikatory zastępstw i ogłoszeń są liczone tym samym
  algorytmem co w aplikacji (`String.hashCode()` z Javy), inaczej to samo zastępstwo
  powiadomiłoby użytkownika dwa razy. Test zgodności pilnuje tego na zapisanej stronie szkoły.

## Harmonogram

Zegar zewnętrzny ([cron-job.org](https://cron-job.org)) wywołuje workflow co 5 minut przez API
GitHuba (`workflow_dispatch`). Harmonogram GitHuba (`schedule`) zostaje jako zapas - sam odpala
się w praktyce co 15-25 minut. Workflow co jakiś czas zapisuje też plik `heartbeat.txt`, żeby GitHub
nie wyłączył harmonogramu po 60 dniach bez aktywności.

## Archiwum stron zastępstw

Przy każdej zmianie strony zastępstw przez szkołę watcher zapisuje jej kopię w
`archiwum/zastepstwa/` (oryginalne bajty, ISO-8859-2). Służy do testowania parsera aplikacji na
prawdziwych stronach z wielu dni.

## Konfiguracja

Do wysyłania wymagany jest sekret repozytorium `FCM_SERVICE_ACCOUNT_JSON` - klucz konta usługi
Firebase. Krok po kroku: [PORADNIK_PUSH.md](https://github.com/stasolejnik/elektron/blob/main/PORADNIK_PUSH.md)
w repozytorium aplikacji.

## Testy

```
pip install -r requirements.txt
python -m unittest discover -s tests
```

Testy działają bez sieci (wysyłka FCM jest podmieniana). Nie są częścią workflow uruchamianego
co 5 minut, żeby błąd testu nie zatrzymał wysyłki powiadomień.

## Pliki

| Plik | Opis |
|---|---|
| `watch.py` | cały watcher: pobieranie, parsowanie, porównanie, wysyłka |
| `state.json` | stan: „widziane” wpisy i liczniki prób (aktualizowany przez workflow) |
| `.github/workflows/watch.yml` | przebieg na GitHub Actions |
| `archiwum/zastepstwa/` | kopie strony zastępstw |
| `tests/` | testy |

## Prywatność

Watcher nie zna użytkowników: powiadomienia idą na ogólne tematy FCM, a wybór klasy i grup
odbywa się w aplikacji, na telefonie.

## Kontakt

kontakt.elektron@pm.me
