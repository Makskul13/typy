# -*- coding: utf-8 -*-
"""
Skaner kursów -> docs/data.json (dane dla aplikacji webowej na iPhone'a).

1. Pobiera kursy h2h z The Odds API dla wybranych lig.
2. Przelicza kursy na prawdopodobieństwa, usuwając marżę, i uśrednia po bukmacherach.
3. Zapisuje typy z prawdopodobieństwem >= 55% do docs/data.json
   (próg wyświetlania, domyślnie 65%, ustawia się suwakiem w aplikacji).
Aplikacja (docs/index.html) tylko wyświetla ten plik.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ============================ KONFIGURACJA ============================
ODDS_API_KEY = os.environ["ODDS_API_KEY"]   # z GitHub Secrets

# klucz The Odds API: (nazwa, typ sportu)
SPORTS = {
    "soccer_epl": ("Premier League", "soccer"),
    "soccer_spain_la_liga": ("La Liga", "soccer"),
    "soccer_italy_serie_a": ("Serie A", "soccer"),
    "soccer_germany_bundesliga": ("Bundesliga", "soccer"),
    "soccer_poland_ekstraklasa": ("Ekstraklasa", "soccer"),
    "basketball_nba": ("NBA", "basketball"),
    "icehockey_nhl": ("NHL", "hockey"),
}

STORE_MIN_PROBABILITY = 0.55   # zapisujemy typy od 55%; próg wyświetlania ustawia się w aplikacji
DEFAULT_THRESHOLD = 0.65       # domyślny próg w aplikacji (suwak)
HOURS_AHEAD = 48
MIN_BOOKMAKERS = 3
REGIONS = "eu"
LOCAL_TZ = ZoneInfo("Europe/Warsaw")
OUT_FILE = Path(__file__).parent / "docs" / "data.json"
# =====================================================================

API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"


def fetch_odds(sport_key):
    """Zwraca (lista_meczów, pozostałe_zapytania, czy_sukces)."""
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": REGIONS,
        "markets": "h2h",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    try:
        r = requests.get(API_URL.format(sport=sport_key), params=params, timeout=30)
    except requests.RequestException as e:
        print(f"[BŁĄD] {sport_key}: {e}")
        return [], None, False

    remaining = r.headers.get("x-requests-remaining")
    if r.status_code != 200:
        print(f"[UWAGA] {sport_key}: HTTP {r.status_code} - {r.text[:200]}")
        return [], remaining, False
    return r.json(), remaining, True


def analyze_event(event):
    """Średnie prawdopodobieństwo (bez marży) najbardziej prawdopodobnego wyniku."""
    prob_sums, prob_counts, best_odds = {}, {}, {}
    used = 0

    for bm in event.get("bookmakers", []):
        market = next((m for m in bm.get("markets", []) if m["key"] == "h2h"), None)
        if not market:
            continue
        outcomes = market.get("outcomes", [])
        if len(outcomes) < 2 or any(o["price"] <= 1.0 for o in outcomes):
            continue

        raw = {o["name"]: 1.0 / o["price"] for o in outcomes}
        total = sum(raw.values())
        used += 1

        for o in outcomes:
            name = o["name"]
            prob_sums[name] = prob_sums.get(name, 0.0) + raw[name] / total
            prob_counts[name] = prob_counts.get(name, 0) + 1
            if o["price"] > best_odds.get(name, (0, ""))[0]:
                best_odds[name] = (o["price"], bm["title"])

    if used < MIN_BOOKMAKERS:
        return None

    avg = {n: prob_sums[n] / prob_counts[n] for n in prob_sums}
    pick = max(avg, key=avg.get)
    return {
        "pick": pick,
        "prob": round(avg[pick], 4),
        "best_odds": round(best_odds[pick][0], 2),
        "best_bookmaker": best_odds[pick][1],
        "bookmakers": used,
    }


def build_data():
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=HOURS_AHEAD)
    leagues, remaining, ok_count = [], None, 0

    for sport_key, (name, sport_type) in SPORTS.items():
        events, rem, ok = fetch_odds(sport_key)
        ok_count += ok
        remaining = rem if rem is not None else remaining
        picks = []

        for ev in events:
            start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if not (now <= start <= horizon):
                continue
            res = analyze_event(ev)
            if res and res["prob"] >= STORE_MIN_PROBABILITY:
                res.update(
                    home=ev["home_team"],
                    away=ev["away_team"],
                    start=start.astimezone(LOCAL_TZ).isoformat(timespec="minutes"),
                )
                picks.append(res)

        picks.sort(key=lambda p: p["prob"], reverse=True)
        print(f"{name}: mecze={len(events)}, typy >= {STORE_MIN_PROBABILITY:.0%}: {len(picks)}")
        leagues.append({"key": sport_key, "name": name, "sport": sport_type, "picks": picks})
        time.sleep(0.3)

    data = {
        "generated_at": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        "min_probability": STORE_MIN_PROBABILITY,
        "default_threshold": DEFAULT_THRESHOLD,
        "hours_ahead": HOURS_AHEAD,
        "requests_remaining": remaining,
        "leagues": leagues,
    }
    return data, ok_count


def main():
    data, ok_count = build_data()
    if ok_count == 0:
        # Nie nadpisujemy starych danych pustym wynikiem (np. zły klucz API).
        print("Żadne zapytanie nie powiodło się - zachowuję poprzednie dane.")
        sys.exit(1)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(l["picks"]) for l in data["leagues"])
    print(f"Zapisano {OUT_FILE} ({total} typów).")
    if data["requests_remaining"] is not None:
        print(f"Pozostałe zapytania w The Odds API: {data['requests_remaining']}")


if __name__ == "__main__":
    main()
