#!/usr/bin/env python3
"""
Парсер API ФНЛ (fnl.pro).
Каждый эндпоинт -> отдельный raw JSON (с таймстампом) + отдельный CSV.
Запуск:  python fnl_parser.py
"""
import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

# ---------- КОНФИГ ----------
BASE_URL = "https://fnl-app.fnl.pro/api/v1"
LEAGUE_ID = 100
SEASON_ID = 1066
REQUEST_DELAY = 0.5
TIMEOUT = 30

HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Origin": "https://fnl.pro",
    "Referer": "https://fnl.pro/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/155.0.0.0 Safari/537.36"
    ),
}

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
CSV_DIR = ROOT / "data" / "csv"
RAW_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ---------- УТИЛИТЫ ----------
def get(path: str, params: dict | None = None) -> dict | list:
    url = f"{BASE_URL}{path}"
    for attempt in range(3):
        try:
            r = SESSION.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"  ! {url} попытка {attempt + 1}: {e}")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Не удалось получить {url}")


def save_raw(name: str, payload):
    """Сырой JSON -> data/raw/<name>_<timestamp>.json"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RAW_DIR / f"{name}_{ts}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> raw: {path.relative_to(ROOT)}")


def save_csv(name: str, df: pd.DataFrame):
    """Нормализованный CSV -> data/csv/<name>.csv (перезаписывается)"""
    if df is None or df.empty:
        print(f"  (пропуск CSV {name}: пусто)")
        return
    path = CSV_DIR / f"{name}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  -> csv: {path.relative_to(ROOT)} ({len(df)} строк)")


# ---------- 01. SLIDER ----------
def fetch_slider():
    print("[01] slider")
    data = get("/center/calendar/matches/slider", {"leagueId": LEAGUE_ID})
    save_raw("slider", data)

    rows = []
    for m in data.get("matches", []):
        rows.append({
            "matchId": m.get("matchId"),
            "fnlId": m.get("fnlId"),
            "status": m.get("status"),
            "tourId": m.get("tourId"),
            "tour": m.get("tour"),
            "date": m.get("date"),
            "seasonId": m.get("seasonId"),
            "stadium": (m.get("stadium") or {}).get("title"),
            "city": (m.get("stadium") or {}).get("city"),
            "viewers": (m.get("stadium") or {}).get("viewers"),
            "judge": (m.get("judges") or {}).get("name"),
            "home_teamId": (m.get("home") or {}).get("teamId"),
            "home_name": (m.get("home") or {}).get("name"),
            "home_score": (m.get("home") or {}).get("score"),
            "guest_teamId": (m.get("guest") or {}).get("teamId"),
            "guest_name": (m.get("guest") or {}).get("name"),
            "guest_score": (m.get("guest") or {}).get("score"),
            "broadcast": m.get("isAvailableBroadcast"),
        })
    save_csv("01_slider_matches", pd.DataFrame(rows))


# ---------- 02. LOGOS ----------
def fetch_logos():
    print("[02] logoTeamSeason")
    data = get("/info/logoTeamSeason", {"leagueId": LEAGUE_ID})
    save_raw("logoTeamSeason", data)

    df = pd.DataFrame(data)
    if not df.empty:
        df = df[["leagueId", "teamId", "teamSlug", "logoUrl", "modifiedDate"]].copy()
        df["modifiedDate"] = pd.to_datetime(df["modifiedDate"], errors="coerce")
        df = df.sort_values("modifiedDate", ascending=False).drop_duplicates("teamId")
        df = df.sort_values("teamId").reset_index(drop=True)
    save_csv("02_team_logos", df)


# ---------- 03. ACTIVE SEASON ----------
def fetch_active_season():
    print("[03] activeSeason")
    data = get("/info/activeSeason", {"leagueId": LEAGUE_ID})
    save_raw("activeSeason", data)
    # один объект -> одна строка CSV
    save_csv("03_active_season", pd.DataFrame([data]))


# ---------- 04. SEASON HISTORY ----------
def fetch_season_history():
    print("[04] seasonHistory")
    data = get("/info/seasonHistory", {"leagueId": LEAGUE_ID})
    save_raw("seasonHistory", data)
    save_csv("04_seasons", pd.DataFrame(data))


# ---------- 05. MEDIA ----------
def fetch_media(media_type: str = "LINK", page_size: int = 100, max_pages: int = 50):
    print(f"[05] media (type={media_type})")
    all_items, offset, total = [], 0, None

    for page in range(max_pages):
        data = get("/center/media", {
            "leagueId": LEAGUE_ID,
            "type": media_type,
            "limit": page_size,
            "offset": offset,
        })
        if page == 0:
            save_raw(f"media_{media_type}_page0", data)

        items = data.get("media", [])
        all_items.extend(items)
        total = data.get("total")
        offset += page_size
        print(f"  страница {page + 1}: +{len(items)} (накоплено {len(all_items)}/{total})")

        if not items or offset >= (total or 0):
            break
        time.sleep(REQUEST_DELAY)

    # дедуп по id медиа
    seen, uniq = set(), []
    for it in all_items:
        if it["id"] not in seen:
            seen.add(it["id"])
            uniq.append(it)

    save_csv(f"05_media_{media_type}", pd.DataFrame(uniq))


# ---------- 06. PLAYER STATS (по одному типу) ----------
def fetch_player_stats(event_type: str):
    print(f"[06] player stats ({event_type})")
    # ВАЖНО: эндпоинт /center/player/stats не принимает limit и offset —
    # только leagueId, seasonId, eventType. Иначе 400 Bad Request.
    data = get("/center/player/stats", {
        "leagueId": LEAGUE_ID,
        "seasonId": SEASON_ID,
        "eventType": event_type,
    })
    save_raw(f"player_stats_{event_type}", data)

    players = data.get("players", [])
    for p in players:
        p = dict(p)
        p["eventType"] = event_type

    save_csv(f"06_player_stats_{event_type}", pd.DataFrame(players))
    total = data.get("total")
    print(f"  получено записей: {len(players)} (total в ответе: {total})")

# ---------- 07. STANDINGS ----------
def fetch_standings(extended: bool = True):
    path = "/center/rank/extended" if extended else "/center/rank"
    name = "standings_extended" if extended else "standings"
    prefix = "07_"
    print(f"[07] {name}")

    data = get(path, {"leagueId": LEAGUE_ID, "seasonId": SEASON_ID})
    save_raw(name, data)

    rows = []
    for t in data.get("teams", []):
        row = {k: v for k, v in t.items() if k not in ("lastGamesResult", "lastGames")}
        row["last5"] = "".join(
            {"WIN": "В", "DRAW": "Н", "LOSS": "П"}.get(x, "?")
            for x in (t.get("lastGamesResult") or [])
        )
        row["last5_ids"] = ",".join(str(g["gameId"]) for g in (t.get("lastGames") or []))
        rows.append(row)

    save_csv(f"{prefix}{name}", pd.DataFrame(rows))


# ---------- 08. LEAGUE STATS ----------
def fetch_league_stats():
    print("[08] league stats")
    data = get("/center/league/stats", {"leagueId": LEAGUE_ID, "seasonId": SEASON_ID})
    save_raw("league_stats", data)

    types = {t["id"]: t["description"] for t in data.get("eventType", [])}
    rows = []
    for e in data.get("events", []):
        row = dict(e)
        row["description"] = types.get(e["id"])
        rows.append(row)

    save_csv("08_league_stats", pd.DataFrame(rows))


# ---------- MAIN ----------
def main():
    print(f"Старт парсинга ФНЛ (leagueId={LEAGUE_ID}, seasonId={SEASON_ID})")
    print(f"raw -> {RAW_DIR}")
    print(f"csv -> {CSV_DIR}\n")

    fetch_slider()
    fetch_logos()
    fetch_active_season()
    fetch_season_history()
    fetch_media("LINK")

    for et in [
        "BOMBARDIER",
        "ASSISTANT",
        "SHOTS_TARGET",
        "SAVES",
        "YELLOW_CARD",
        "FOULS_ON_PLAYER",
    ]:
        fetch_player_stats(et)

    fetch_standings(extended=True)
    fetch_standings(extended=False)
    fetch_league_stats()

    print("\nГотово.")
    print("Сырые JSON: data/raw/")
    print("Таблицы:    data/csv/")


if __name__ == "__main__":
    main()
