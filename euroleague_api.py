"""Euroleague (men's) free-throw stats fetcher.

Uses the public (unofficial) Euroleague endpoints:
  - Games list: https://api-live.euroleague.net/v2/competitions/E/seasons/E{year}/games
  - Boxscore:   https://live.euroleague.net/api/Boxscore?gamecode={code}&seasoncode=E{year}

No API key required. Endpoints are undocumented and may change without notice.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

GAMES_URL = (
    "https://api-live.euroleague.net/v2/competitions/E/seasons/E{year}/games"
)
BOXSCORE_URL = "https://live.euroleague.net/api/Boxscore"

HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

# phaseType.code -> internal phase key shown in the UI
PHASE_MAP = {
    "RS": "Regular Season",
    "PO": "Playoffs",
    "FF": "Final Four",
}

# Phase name -> short column prefix used in the wide output rows.
PHASE_PREFIX = [
    ("Regular Season", "rs"),
    ("Playoffs", "po"),
    ("Final Four", "f4"),
]

# How many boxscore requests to run concurrently.
_MAX_WORKERS = 8
_TIMEOUT = 30

# A game counts as "close" when the final margin is at most this many points.
CLOSE_MARGIN = 10


class EuroleagueError(Exception):
    """Raised when the API cannot be reached or returns unexpected data."""


def season_code(year: int) -> str:
    """2024 -> 'E2024' (the 2024-25 season)."""
    return f"E{year}"


def season_label(year: int) -> str:
    """2024 -> '2024-25'."""
    return f"{year}-{str(year + 1)[-2:]}"


def label_to_year(label: str) -> int:
    """'2024-25' -> 2024."""
    return int(label.split("-")[0])


def fetch_games(year: int, session: requests.Session):
    """Return the list of played games for a season with phase + team info."""
    url = GAMES_URL.format(year=year)
    try:
        resp = session.get(url, headers=HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise EuroleagueError(f"Could not load games for {season_label(year)}: {exc}")

    games = []
    for g in payload.get("data", []):
        if not g.get("played"):
            continue
        phase_code = (g.get("phaseType") or {}).get("code")
        phase = PHASE_MAP.get(phase_code)
        if phase is None:
            continue  # ignore qualifiers / other phases
        local = ((g.get("local") or {}).get("club")) or {}
        road = ((g.get("road") or {}).get("club")) or {}
        if not local.get("code") or not road.get("code"):
            continue
        games.append(
            {
                "gameCode": g.get("gameCode"),
                "phase": phase,
                "local": {"code": local["code"], "name": local.get("name", local["code"])},
                "road": {"code": road["code"], "name": road.get("name", road["code"])},
            }
        )
    return games


def _fetch_boxscore_ft(year: int, game_code: int, session: requests.Session):
    """Return (home_ft_att, away_ft_att, point_margin) for a game, or None."""
    params = {"gamecode": game_code, "seasoncode": season_code(year)}
    try:
        resp = session.get(
            BOXSCORE_URL, params=params, headers=HEADERS, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    stats = data.get("Stats")
    if not stats or len(stats) < 2:
        return None
    try:
        home = int(stats[0]["totr"]["FreeThrowsAttempted"])
        away = int(stats[1]["totr"]["FreeThrowsAttempted"])
        margin = abs(int(stats[0]["totr"]["Points"]) - int(stats[1]["totr"]["Points"]))
    except (KeyError, TypeError, ValueError):
        return None
    return home, away, margin


def fetch_season(year: int, progress=None):
    """Fetch and aggregate free-throw stats for a whole season.

    Returns the raw per-game results (JSON-serialisable, cacheable):
        [ {phase, local, road, home_ft, away_ft, margin}, ... ]
    The caller turns this into team rows with `aggregate()`, optionally filtering
    by point margin, so any threshold can be applied without re-fetching.

    `progress(done, total, message)` is called as work advances (optional).
    """
    session = requests.Session()

    if progress:
        progress(0, 1, "Loading game schedule...")
    games = fetch_games(year, session)
    total = len(games)
    if total == 0:
        raise EuroleagueError(
            f"No played games found for {season_label(year)}. "
            "The season may not have started yet."
        )

    # Collect raw per-game free-throw + margin data, then aggregate twice.
    results = []  # {phase, local, road, home_ft, away_ft, margin}
    done = 0
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_boxscore_ft, year, g["gameCode"], session): g
            for g in games
        }
        for fut in as_completed(futures):
            g = futures[fut]
            result = fut.result()
            done += 1
            if progress:
                progress(done, total, f"Reading boxscores ({done}/{total})...")
            if result is None:
                continue
            home_ft, away_ft, margin = result
            results.append(
                {
                    "phase": g["phase"],
                    "local": g["local"],
                    "road": g["road"],
                    "home_ft": home_ft,
                    "away_ft": away_ft,
                    "margin": margin,
                }
            )

    return results


def aggregate(results, max_margin=None):
    """Build wide per-team rows from raw game results.

    `results` may span several seasons concatenated oldest first; totals are
    summed across them. If max_margin is given, only games decided by <= max_margin points count.
    """
    if max_margin is None:
        keep = lambda m: True  # noqa: E731
    else:
        keep = lambda m: m <= max_margin  # noqa: E731
    return _aggregate(results, keep)


def _aggregate(results, keep):
    """Build wide per-team rows from raw game results, keeping games where
    keep(margin) is True."""
    # phase -> code -> {att, opp, games}
    agg = {phase: {} for phase in PHASE_MAP.values()}
    # code -> display name. Last one seen wins, so when several seasons are
    # combined (oldest first) a club shows its most recent (sponsor) name.
    names = {}

    def ensure(phase, club):
        names[club["code"]] = club["name"]
        bucket = agg[phase]
        rec = bucket.get(club["code"])
        if rec is None:
            rec = {"att": 0, "opp": 0, "games": 0}
            bucket[club["code"]] = rec
        return rec

    for r in results:
        if not keep(r["margin"]):
            continue
        phase = r["phase"]
        home = ensure(phase, r["local"])
        away = ensure(phase, r["road"])
        home["att"] += r["home_ft"]
        home["opp"] += r["away_ft"]
        home["games"] += 1
        away["att"] += r["away_ft"]
        away["opp"] += r["home_ft"]
        away["games"] += 1

    rows = []
    # Every team that appears in any kept game.
    for code, name in names.items():
        row = {"team": name}
        tot_att = tot_opp = tot_games = 0
        # tot_games (set below) is the number of games these stats are based on.
        for phase, prefix in PHASE_PREFIX:
            rec = agg[phase].get(code)
            att = rec["att"] if rec else 0
            opp = rec["opp"] if rec else 0
            games = rec["games"] if rec else 0
            row[f"{prefix}_att"] = att
            row[f"{prefix}_opp"] = opp
            row[f"{prefix}_diff"] = att - opp
            tot_att += att
            tot_opp += opp
            tot_games += games
        row["games"] = tot_games
        if tot_games:
            row["avg_att"] = round(tot_att / tot_games, 1)
            row["avg_opp"] = round(tot_opp / tot_games, 1)
            row["avg_diff"] = round((tot_att - tot_opp) / tot_games, 1)
        else:
            row["avg_att"] = row["avg_opp"] = row["avg_diff"] = 0.0
        rows.append(row)

    rows.sort(key=lambda r: r["avg_att"], reverse=True)
    return rows
