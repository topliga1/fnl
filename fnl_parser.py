#!/usr/bin/env python3
"""
Парсер API ФНЛ (fnl.pro).
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
LOGO_BASE_URL = "https://s3.fnl.pro:9000"  # <-- правильная база для картинок
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
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RAW_DIR / f"{name}_{ts}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> raw: {path.relative_to(ROOT)}")


def save_csv(name: str, df: pd.DataFrame):
    if df is None or df.empty:
        print(f"  (пропуск CSV {name}: пусто)")
        return
    path = CSV_DIR / f"{name}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  -> csv: {path.relative_to(ROOT)} ({len(df)} строк)")


def match_to_row(m: dict) -> dict:
    """Нормализует объект матча (из слайдера или календаря)."""
    return {
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
    }


# ---------- 01. SLIDER ----------
def fetch_slider():
    print("[01] slider")
    data = get("/center/calendar/matches/slider", {"leagueId": LEAGUE_ID})
    save_raw("slider", data)
    rows = [match_to_row(m) for m in data.get("matches", [])]
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


# ---------- 02.5. TEAMS BY SEASON ----------
def fetch_teams_by_season():
    print("[02.5] teamsBySeason")
    data = get("/info/teamsBySeason", {"leagueId": LEAGUE_ID, "seasonId": SEASON_ID})
    save_raw("teamsBySeason", data)

    df = pd.DataFrame(data)
    if not df.empty:
        df = df[["leagueId", "teamId", "title", "teamSlug", "city"]].copy()
        df = df.sort_values("teamId").reset_index(drop=True)
    save_csv("02_5_teams_by_season", df)


# ---------- 03. ACTIVE SEASON ----------
def fetch_active_season():
    print("[03] activeSeason")
    data = get("/info/activeSeason", {"leagueId": LEAGUE_ID})
    save_raw("activeSeason", data)
    save_csv("03_active_season", pd.DataFrame([data]))


# ---------- 04. SEASON HISTORY ----------
def fetch_season_history():
    print("[04] seasonHistory")
    data = get("/info/seasonHistory", {"leagueId": LEAGUE_ID})
    save_raw("seasonHistory", data)
    save_csv("04_seasons", pd.DataFrame(data))


# ---------- 04.5. MONTHS & TOURS ----------
def fetch_months():
    print("[04.5] monthsByParams")
    data = get("/info/monthsByParams", {"leagueId": LEAGUE_ID, "seasonId": SEASON_ID})
    save_raw("monthsByParams", data)
    save_csv("04_5_months", pd.DataFrame({"month": data}))

def fetch_tours():
    print("[04.6] toursBySeason")
    data = get("/info/toursBySeason", {"leagueId": LEAGUE_ID, "seasonId": SEASON_ID})
    save_raw("toursBySeason", data)
    rows = []
    for t in data:
        rows.append({
            "tourId": t.get("id"),
            "name": t.get("name"),
            "months": ",".join(str(x) for x in (t.get("months") or [])),
        })
    save_csv("04_6_tours", pd.DataFrame(rows))


# ---------- 06. PLAYER STATS ----------
def fetch_player_stats(event_type: str):
    print(f"[06] player stats ({event_type})")
    data = get("/center/player/stats", {
        "leagueId": LEAGUE_ID, "seasonId": SEASON_ID, "eventType": event_type,
    })
    save_raw(f"player_stats_{event_type}", data)

    players = data.get("players", [])
    for p in players:
        p = dict(p)
        p["eventType"] = event_type
    save_csv(f"06_player_stats_{event_type}", pd.DataFrame(players))


# ---------- 07. STANDINGS ----------
def fetch_standings(extended: bool = True):
    path = "/center/rank/extended" if extended else "/center/rank"
    name = "standings_extended" if extended else "standings"
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
    save_csv(f"07_{name}", pd.DataFrame(rows))


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


# ---------- 09. FULL CALENDAR (только в CSV) ----------
def fetch_calendar_full():
    print("[09] calendar full")
    data = get("/center/calendar/matches/date", {
        "leagueId": LEAGUE_ID,
        "seasonId": SEASON_ID,
        "move": "ALL",
    })
    save_raw("calendar_full", data)

    all_matches = {}
    for day in data.get("matches", []):
        for m in day.get("matches", []):
            mid = m.get("matchId")
            if mid not in all_matches:
                all_matches[mid] = match_to_row(m)

    print(f"  получено матчей из календаря: {len(all_matches)}")

    slider_path = CSV_DIR / "01_slider_matches.csv"
    if slider_path.exists():
        slider_df = pd.read_csv(slider_path)
        for _, r in slider_df.iterrows():
            mid = r.get("matchId")
            if pd.notna(mid):
                all_matches[int(mid)] = {
                    k: (None if pd.isna(v) else v) for k, v in r.items()
                }

    df = pd.DataFrame(all_matches.values())
    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)
    save_csv("01_calendar_full", df)


# ---------- 10. HTML ----------
def build_html():
    print("[10] build index.html")
    import html as _html

    def read_csv(name):
        p = CSV_DIR / f"{name}.csv"
        return pd.read_csv(p) if p.exists() else pd.DataFrame()

    slider = read_csv("01_slider_matches")
    standings = read_csv("07_standings_extended")
    logos_df = read_csv("02_team_logos")
    teams_df = read_csv("02_5_teams_by_season")
    bomb = read_csv("06_player_stats_BOMBARDIER")
    ass = read_csv("06_player_stats_ASSISTANT")

    # Справочники
    logos = {}
    if not logos_df.empty:
        for _, r in logos_df.iterrows():
            url = r.get("logoUrl")
            if pd.notna(url):
                # <-- ИСПРАВЛЕНИЕ: база для картинок s3.fnl.pro:9000
                logos[int(r["teamId"])] = (
                    f"{LOGO_BASE_URL}{url}" if str(url).startswith("/") else url
                )

    cities = {}
    if not teams_df.empty:
        for _, r in teams_df.iterrows():
            cities[int(r["teamId"])] = r.get("city")

    def s(v, default=""):
        return default if pd.isna(v) else str(v)

    def logo_html(team_id, name):
        url = logos.get(int(team_id)) if pd.notna(team_id) else None
        if url:
            return f'<img src="{_html.escape(url)}" alt="" class="logo">'
        letter = (s(name, "?")[:1]).upper()
        return f'<div class="logo logo-fallback">{_html.escape(letter)}</div>'

    def short_name(full_name):
        parts = s(full_name).split()
        return " ".join(parts[:2]) if parts else ""

    # --- 1) Текущий тур ---
    current_tour_id = None
    current_tour_name = None
    if not slider.empty:
        future = slider[slider["status"] == "FUTURE"]
        pick = future if not future.empty else slider
        current_tour_id = int(pick["tourId"].max())
        current_tour_name = pick.loc[pick["tourId"] == current_tour_id, "tour"].iloc[0]

    tour_matches = pd.DataFrame()
    if current_tour_id is not None and not slider.empty:
        tour_matches = slider[slider["tourId"] == current_tour_id].copy()
        tour_matches = tour_matches.sort_values("date").head(8)

    # --- 2) Рендер матчей ---
    def render_match(row):
        status = row.get("status", "")
        hs, gs = row.get("home_score"), row.get("guest_score")
        if status == "END" and pd.notna(hs) and pd.notna(gs):
            score = f"{int(hs)} : {int(gs)}"
        else:
            score = "vs"

        date = s(row.get("date"), "—")
        stadium = s(row.get("stadium"), "")
        city = s(row.get("city"), "")
        viewers = row.get("viewers")

        stadium_line = _html.escape(stadium)
        if city:
            stadium_line += (", " if stadium_line else "") + _html.escape(city)
        if pd.notna(viewers):
            stadium_line += " · 👥 " + f"{int(viewers):,}".replace(",", " ")

        return f"""
        <div class="match">
          <div class="match-date">{_html.escape(date)}</div>
          <div class="match-row">
            <div class="team-block">
              {logo_html(row.get("home_teamId"), s(row.get("home_name")))}
              <div class="team-name">{_html.escape(s(row.get("home_name")))}</div>
            </div>
            <div class="score">{score}</div>
            <div class="team-block">
              {logo_html(row.get("guest_teamId"), s(row.get("guest_name")))}
              <div class="team-name">{_html.escape(s(row.get("guest_name")))}</div>
            </div>
          </div>
          {f'<div class="match-meta">{stadium_line}</div>' if stadium_line else ''}
        </div>"""

    matches_html = "".join(render_match(r) for _, r in tour_matches.iterrows()) \
        or "<p class='empty'>Нет матчей текущего тура.</p>"

    # --- 3) Таблица ---
    def render_standings(df):
        if df.empty:
            return "<p class='empty'>Нет данных.</p>"
        rows = []
        for _, r in df.iterrows():
            tid = r.get("teamId")
            tname = s(r.get("teamName"))
            tcity = s(cities.get(int(tid))) if pd.notna(tid) else ""
            city_str = f'<div class="team-city">{_html.escape(tcity)}</div>' if tcity else ""
            rows.append(f"""
              <tr>
                <td class="rank">{int(r['rank'])}</td>
                <td class="team-cell">
                  {logo_html(tid, tname)}
                  <div><span>{_html.escape(tname)}</span>{city_str}</div>
                </td>
                <td>{int(r['allGame'])}</td>
                <td>{int(r['win'])}</td>
                <td>{int(r['draw'])}</td>
                <td>{int(r['loss'])}</td>
                <td class="pts">{int(r['score'])}</td>
              </tr>""")
        return f"""
        <div class="table-wrap">
          <table class="standings">
            <thead>
              <tr><th>#</th><th>Команда</th><th>И</th><th>В</th><th>Н</th><th>П</th><th>О</th></tr>
            </thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>"""

    # --- 4) Топ-игроки ---
    def render_top(df, title, metric_label):
        if df.empty:
            return f"<div class='card'><h3>{title}</h3><p class='empty'>Нет данных.</p></div>"
        rows = []
        for i, r in df.head(10).iterrows():
            rows.append(f"""
              <tr>
                <td class="rank">{i + 1}</td>
                <td>{_html.escape(short_name(r['playerName']))}</td>
                <td>{_html.escape(s(r['teamTitle']))}</td>
                <td class="pts">{int(r['eventCnt'])}</td>
              </tr>""")
        return f"""
        <div class="card">
          <h3>{title}</h3>
          <div class="table-wrap">
            <table class="top">
              <thead><tr><th>#</th><th>Игрок</th><th>Команда</th><th>{metric_label}</th></tr></thead>
              <tbody>{''.join(rows)}</tbody>
            </table>
          </div>
        </div>"""

    # --- 5) Финальный HTML ---
    html_out = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ФНЛ — Первая лига</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
            background: #0f172a; color: #e2e8f0; }}
    header {{ padding: 24px 16px; background: linear-gradient(135deg, #1e293b, #0f172a);
              border-bottom: 1px solid #334155; }}
    header h1 {{ margin: 0; font-size: 24px; }}
    header .sub {{ color: #94a3b8; font-size: 14px; margin-top: 4px; }}
    main {{ max-width: 1200px; margin: 0 auto; padding: 16px; }}
    h2 {{ font-size: 18px; margin: 28px 0 14px; color: #f8fafc; }}
    .empty {{ color: #94a3b8; font-style: italic; }}

    /* Матчи */
    .matches {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }}
    .match {{ background: #1e293b; border: 1px solid #334155; border-radius: 14px; padding: 14px; }}
    .match-date {{ color: #94a3b8; font-size: 12px; text-align: center; margin-bottom: 10px; }}
    .match-row {{ display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 10px; }}
    .team-block {{ display: flex; flex-direction: column; align-items: center; gap: 6px; }}
    .team-name {{ font-weight: 600; font-size: 13px; text-align: center; line-height: 1.2; }}
    .score {{ font-weight: 800; font-size: 20px; background: #0f172a; padding: 6px 12px;
              border-radius: 10px; white-space: nowrap; }}
    .match-meta {{ color: #94a3b8; font-size: 12px; text-align: center; margin-top: 10px; }}

    /* Логотипы */
    .logo {{ width: 40px; height: 40px; object-fit: contain; }}
    .logo-fallback {{ width: 40px; height: 40px; border-radius: 50%; background: #334155;
                      display: flex; align-items: center; justify-content: center;
                      font-weight: 700; color: #cbd5e1; }}

    /* Таблицы */
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; background: #1e293b;
             border: 1px solid #334155; border-radius: 14px; overflow: hidden; }}
    th, td {{ padding: 10px 12px; text-align: left; font-size: 13px;
              border-bottom: 1px solid #334155; }}
    th {{ background: #0f172a; color: #cbd5e1; font-weight: 600; }}
    tr:last-child td {{ border-bottom: none; }}
    td.rank {{ color: #94a3b8; width: 40px; }}
    td.pts {{ font-weight: 700; }}
    .team-cell {{ display: flex; align-items: center; gap: 10px; font-weight: 600; }}
    .team-cell .logo, .team-cell .logo-fallback {{ width: 26px; height: 26px; font-size: 12px; }}
    .team-city {{ font-size: 11px; color: #94a3b8; font-weight: 400; }}

    /* Карточки лидеров */
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 14px; padding: 14px; }}
    .card h3 {{ margin: 0 0 10px; font-size: 15px; }}

    footer {{ color: #64748b; font-size: 12px; text-align: center; padding: 24px 16px; }}
  </style>
</head>
<body>
  <header>
    <h1>ФНЛ — Первая лига</h1>
    <div class="sub">Сезон {_html.escape(str(slider['seasonId'].iloc[0]) if not slider.empty else '—')} ·
      {_html.escape(str(current_tour_name or '—'))} ·
      обновлено {datetime.now().strftime('%d.%m.%Y %H:%M')}</div>
  </header>
  <main>
    <h2>Матчи тура</h2>
    <section class="matches">{matches_html}</section>

    <h2>Турнирная таблица</h2>
    <section>{render_standings(standings)}</section>

    <h2>Лидеры</h2>
    <section class="cards">
      {render_top(bomb, "Бомбардиры", "Голы")}
      {render_top(ass, "Ассистенты", "Передачи")}
    </section>
  </main>
  <footer>Данные: fnl.pro · сгенерировано автоматически</footer>
</body>
</html>
"""
    out_path = ROOT / "index.html"
    out_path.write_text(html_out, encoding="utf-8")
    print(f"  -> html: {out_path.relative_to(ROOT)}")


# ---------- MAIN ----------
def main():
    print(f"Старт парсинга ФНЛ (leagueId={LEAGUE_ID}, seasonId={SEASON_ID})")
    print(f"raw -> {RAW_DIR}")
    print(f"csv -> {CSV_DIR}\n")

    fetch_slider()
    fetch_logos()
    fetch_teams_by_season()
    fetch_active_season()
    fetch_season_history()
    fetch_months()
    fetch_tours()

    for et in ["BOMBARDIER", "ASSISTANT", "SHOTS_TARGET", "SAVES", "YELLOW_CARD", "FOULS_ON_PLAYER"]:
        fetch_player_stats(et)

    fetch_standings(extended=True)
    fetch_standings(extended=False)
    fetch_league_stats()
    fetch_calendar_full()
    build_html()

    print("\nГотово.")
    print("Сырые JSON: data/raw/")
    print("Таблицы:    data/csv/")
    print("Страница:   index.html")


if __name__ == "__main__":
    main()
