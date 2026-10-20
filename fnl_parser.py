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


# ---------- 09. HTML ----------
def build_html():
    """Собирает index.html из уже сохранённых CSV."""
    print("[09] build index.html")

    import html as _html

    def read_csv(name):
        p = CSV_DIR / f"{name}.csv"
        if not p.exists():
            return pd.DataFrame()
        return pd.read_csv(p)

    slider = read_csv("01_slider_matches")
    standings = read_csv("07_standings_extended")
    bomb = read_csv("06_player_stats_BOMBARDIER")
    ass = read_csv("06_player_stats_ASSISTANT")
    league = read_csv("08_league_stats")
    media = read_csv("05_media_LINK")

    # --- 1) Определяем "текущий тур" ---
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
                # безопасно приводим к строке — в CSV пустые ячейки читаются как NaN (float)
        def s(v, default=""):
            if pd.isna(v):
                return default
            return str(v)

        date = s(row.get("date"), "—")
        stadium = s(row.get("stadium"), "—")
        city = s(row.get("city"), "")
        viewers = row.get("viewers")
        viewers_str = f"{int(viewers):,}".replace(",", " ") if pd.notna(viewers) else ""

        stadium_line = _html.escape(stadium)
        if city:
            stadium_line += ", " + _html.escape(city)
        if viewers_str:
            stadium_line += " · 👥 " + viewers_str

        return f"""
        <div class="match">
          <div class="teams">
            <div class="team home">{_html.escape(s(row.get("home_name")))}</div>
            <div class="score">{score}</div>
            <div class="team guest">{_html.escape(s(row.get("guest_name")))}</div>
          </div>
          <div class="meta">{_html.escape(date)}</div>
          <div class="meta">{stadium_line}</div>
        </div>"""

    matches_html = "".join(render_match(r) for _, r in tour_matches.iterrows()) \
        or "<p class='empty'>Нет матчей текущего тура.</p>"

    # --- 3) Таблица ---
    def render_standings(df):
        if df.empty:
            return "<p class='empty'>Нет данных.</p>"
        rows = []
        for _, r in df.iterrows():
            rows.append(f"""
              <tr>
                <td>{int(r['rank'])}</td>
                <td class="team-name">{_html.escape(str(r['teamName']))}</td>
                <td>{int(r['allGame'])}</td>
                <td>{int(r['win'])}</td>
                <td>{int(r['draw'])}</td>
                <td>{int(r['loss'])}</td>
                <td>{int(r['scoredGoal'])}:{int(r['missedGoal'])}</td>
                <td>{int(r['diffGoal'])}</td>
                <td class="pts">{int(r['score'])}</td>
                <td class="form">{_html.escape(str(r.get('last5', '')))}</td>
              </tr>""")
        return f"""
        <table class="standings">
          <thead>
            <tr><th>#</th><th>Команда</th><th>И</th><th>В</th><th>Н</th><th>П</th>
                <th>Голы</th><th>Разн.</th><th>О</th><th>Форма</th></tr>
          </thead>
          <tbody>{''.join(rows)}</tbody>
        </table>"""

    # --- 4) Топ-игроки ---
    def render_top(df, title, metric_label):
        if df.empty:
            return f"<div class='card'><h3>{title}</h3><p class='empty'>Нет данных.</p></div>"
        rows = []
        for i, r in df.head(10).iterrows():
            rows.append(f"""
              <tr>
                <td>{i + 1}</td>
                <td>{_html.escape(str(r['playerName']))}</td>
                <td>{_html.escape(str(r['teamTitle']))}</td>
                <td>{int(r['eventCnt'])}</td>
              </tr>""")
        return f"""
        <div class="card">
          <h3>{title}</h3>
          <table class="top">
            <thead><tr><th>#</th><th>Игрок</th><th>Команда</th><th>{metric_label}</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>"""

    # --- 5) Агрегаты лиги ---
    def render_league(df):
        if df.empty:
            return "<p class='empty'>Нет данных.</p>"
        rows = []
        for _, r in df.iterrows():
            rows.append(f"""
              <tr>
                <td>{_html.escape(str(r.get('description', '')))}</td>
                <td>{r.get('home')}</td>
                <td>{r.get('guest')}</td>
                <td class="pts">{r.get('total')}</td>
              </tr>""")
        return f"""
        <table class="league">
          <thead><tr><th>Метрика</th><th>Дома</th><th>Гости</th><th>Всего</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>"""

    # --- 6) Последние видео ---
    def render_media(df):
        if df.empty:
            return "<p class='empty'>Нет данных.</p>"
        cards = []
        for _, r in df.head(6).iterrows():
            title = _html.escape(str(r.get('title', '')))
            video = r.get('videoUrl') or ''
            cover = r.get('url') or ''
            cover_full = f"https://fnl.pro{cover}" if cover.startswith('/') else cover
            cards.append(f"""
              <a class="media-card" href="{_html.escape(video)}" target="_blank" rel="noopener">
                <div class="media-cover" style="background-image:url('{_html.escape(cover_full)}')"></div>
                <div class="media-title">{title}</div>
              </a>""")
        return f"<div class='media-grid'>{''.join(cards)}</div>"

    # --- 7) Финальный HTML ---
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
    h2 {{ font-size: 18px; margin: 24px 0 12px; color: #f8fafc; }}
    .matches {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }}
    .match {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 12px; }}
    .teams {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
    .team {{ font-weight: 600; font-size: 14px; flex: 1; }}
    .team.home {{ text-align: left; }}
    .team.guest {{ text-align: right; }}
    .score {{ font-weight: 700; font-size: 16px; background: #0f172a; padding: 4px 8px;
              border-radius: 8px; white-space: nowrap; }}
    .meta {{ color: #94a3b8; font-size: 12px; margin-top: 6px; }}
    table {{ width: 100%; border-collapse: collapse; background: #1e293b;
             border: 1px solid #334155; border-radius: 12px; overflow: hidden; }}
    th, td {{ padding: 8px 10px; text-align: left; font-size: 13px;
              border-bottom: 1px solid #334155; }}
    th {{ background: #0f172a; color: #cbd5e1; font-weight: 600; }}
    tr:last-child td {{ border-bottom: none; }}
    td.pts {{ font-weight: 700; }}
    td.form {{ letter-spacing: 1px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 12px; }}
    .card h3 {{ margin: 0 0 8px; font-size: 15px; }}
    .media-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }}
    .media-card {{ text-decoration: none; color: inherit; background: #1e293b;
                   border: 1px solid #334155; border-radius: 12px; overflow: hidden; }}
    .media-cover {{ height: 120px; background-size: cover; background-position: center; }}
    .media-title {{ padding: 8px 10px; font-size: 13px; }}
    .empty {{ color: #94a3b8; font-style: italic; }}
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

    <h2>Статистика лиги</h2>
    <section>{render_league(league)}</section>

    <h2>Последние видео</h2>
    <section>{render_media(media)}</section>
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
    build_html()

    print("\nГотово.")
    print("Сырые JSON: data/raw/")
    print("Таблицы:    data/csv/")
    print("Страница:   index.html")


if __name__ == "__main__":
    main()

    
