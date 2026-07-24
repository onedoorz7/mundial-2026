#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Self-contained live updater for the Mundial 2026 bet site.
No third-party dependencies (stdlib only). Run by GitHub Actions frequently.

Pipeline:
  1. Fetch actual/live results from ESPN's public JSON scoreboard (no API key).
  2. Update results_store.json (group + knockout, champion).
  3. Score all participants per the bet rules.
  4. Rebuild site_data.json and index.html (password gate from env GATE_PASSWORD).
"""
import json, os, hashlib, urllib.request, datetime, sys, unicodedata
from collections import Counter
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
def P(name): return os.path.join(HERE, name)

# ---------------- team-name normalization ----------------
NAME_ALIASES = {
    "south korea":"Korea Republic","korea republic":"Korea Republic","korea dpr":"Korea DPR",
    "czechia":"Czech Republic","czech republic":"Czech Republic",
    "türkiye":"Turkey","turkiye":"Turkey","turkey":"Turkey",
    "côte d'ivoire":"Ivory Coast","cote d'ivoire":"Ivory Coast","ivory coast":"Ivory Coast",
    "usa":"United States","united states":"United States","united states of america":"United States",
    "bosnia and herzegovina":"Bosnia and Herzegovina","bosnia & herzegovina":"Bosnia and Herzegovina",
    "bosnia-herzegovina":"Bosnia and Herzegovina",
    "curacao":"Curaçao","curaçao":"Curaçao",
    "cape verde":"Cape Verde","cabo verde":"Cape Verde",
    "dr congo":"DR Congo","congo dr":"DR Congo","democratic republic of the congo":"DR Congo",
    "iran":"Iran","ir iran":"Iran",
}
def norm(name):
    if not name: return name
    return NAME_ALIASES.get(str(name).strip().lower(), str(name).strip())

# ---------------- golden boot name canonicalization ----------------
def scorer_key(name):
    if not name: return ""
    s = unicodedata.normalize("NFKD", str(name).strip().lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))

def canon_scorer(name):
    if not name: return None
    s = scorer_key(name)
    rules = [
        (('אמבפ','אמבא','אמבם','אמפב','mbappe'), 'אמבפה (Mbappé)'),
        (('הלאנ','האלנ','האלא','הלנד','haaland'), 'האלנד (Haaland)'),
        (('קיין','kane'), 'קיין (Kane)'),
        (('יאמ','ימאל','yamal'), 'יאמאל (Yamal)'),
        (('רונאלד','ronaldo'), 'רונאלדו (Ronaldo)'),
        (('מאלן','malen'), 'מאלן (Malen)'),
        (('אוירז','oyarz','oyarzabal'), 'אויארסבאל (Oyarzabal)'),
        (('ראפינ','raphinha'), 'ראפיניה (Raphinha)'),
        (('מסי','messi'), 'מסי (Messi)'),
        (('david','דויד'), 'דויד (David)'),
        (('balogun','באלוגון','בלוגון'), 'באלוגון (Balogun)'),
        (('vinicius','ויניסיוס'), 'ויניסיוס (Vinícius)'),
        (('saibari','סאיבארי'), 'סאיבארי (Saibari)'),
    ]
    for keys, label in rules:
        if any(k in s for k in keys): return label
    return str(name)

# ---------------- ESPN fetch ----------------
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates={}"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary?event={}"
ESPN_LEADERS = "https://sports.core.api.espn.com/v2/sports/soccer/leagues/fifa.world/seasons/2026/types/1/leaders?lang=en&region=us"
ESPN_R32 = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates=20260628-20260703"
def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += datetime.timedelta(days=1)

def round_for_date(d):
    """Map a knockout match date to its round name."""
    md = (d.month, d.day)
    if (d.month==6 and d.day>=28) or (d.month==7 and d.day<=3): return "Round of 32"
    if d.month==7 and 4<=d.day<=8:  return "Round of 16"
    if d.month==7 and 9<=d.day<=12: return "Quarterfinals"
    if d.month==7 and 14<=d.day<=16:return "Semi-Finals"
    if d.month==7 and d.day==18:    return "Third-Place"
    if d.month==7 and d.day==19:    return "Final"
    return None

def fetch_dates():
    """Fetch a live window plus the next week of fixtures; use MUNDIAL_FULL_SYNC=1 for a full sweep."""
    if os.environ.get("MUNDIAL_FULL_SYNC") == "1":
        return list(daterange(datetime.date(2026,6,11), datetime.date(2026,7,20)))
    today = datetime.datetime.utcnow().date()
    start = max(datetime.date(2026,6,11), today - datetime.timedelta(days=1))
    end = min(datetime.date(2026,7,20), today + datetime.timedelta(days=7))
    return list(daterange(start, end))

def parse_score(v):
    try: return int(v)
    except (TypeError, ValueError): return None

def display_date(d):
    return d.strftime("%b ") + str(d.day)

def display_match_date(event_date, fallback_date):
    if event_date:
        try:
            dt = datetime.datetime.fromisoformat(str(event_date).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            local = dt.astimezone(ZoneInfo("Asia/Jerusalem"))
            return f"{display_date(local.date())} · {local.strftime('%H:%M')}"
        except Exception:
            pass
    return display_date(fallback_date)

def fetch_json(url, timeout=25):
    if url.startswith("http://sports.core.api.espn.com/"):
        url = "https://" + url[len("http://"):]
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def regulation_score_from_summary(data):
    """Return the score after periods 1 and 2 from an ESPN match summary."""
    competitions = (data.get("header") or {}).get("competitions") or []
    if not competitions:
        return None
    scores = {}
    for competitor in competitions[0].get("competitors") or []:
        side = competitor.get("homeAway")
        linescores = competitor.get("linescores") or []
        if side not in ("home", "away") or len(linescores) < 2:
            continue
        first = parse_score(linescores[0].get("displayValue"))
        second = parse_score(linescores[1].get("displayValue"))
        if first is not None and second is not None:
            scores[side] = first + second
    if "home" not in scores or "away" not in scores:
        return None
    return scores["home"], scores["away"]

def fetch_regulation_score(event_id):
    if not event_id:
        return None
    return regulation_score_from_summary(fetch_json(ESPN_SUMMARY.format(event_id)))

def went_to_extra_time(m):
    detail = m.get("status_detail") or ""
    clock = m.get("display_clock") or ""
    return "AET" in detail or "Pens" in detail or clock.startswith("120")

def fetch_espn():
    """Return ESPN matches with live/final status metadata."""
    out = []
    for d in fetch_dates():
        url = ESPN.format(d.strftime("%Y%m%d"))
        try:
            data = fetch_json(url)
        except Exception as e:
            sys.stderr.write(f"warn: fetch {d} failed: {e}\n"); continue
        for ev in data.get("events", []):
            comp = ev["competitions"][0]
            status = comp.get("status", {})
            st = status.get("type", {})
            rnd = round_for_date(d)
            home = away = None
            for c in comp["competitors"]:
                team_obj = c.get("team") or {}
                t = norm(team_obj.get("displayName") or team_obj.get("name") or team_obj.get("location") or "")
                sc = parse_score(c.get("score"))
                shootout = parse_score(c.get("shootoutScore"))
                side = (t, sc, bool(c.get("winner")), shootout)
                if c["homeAway"]=="home": home=side
                else: away=side
            if not home or not away or not home[0] or not away[0]: continue
            state = st.get("state")
            completed = bool(st.get("completed"))
            live = (state == "in") and not completed
            winner = None
            home_so = away_so = None
            if rnd:
                winner = home[0] if home[2] else away[0] if away[2] else None
                home_so, away_so = home[3], away[3]
            out.append({"id": ev.get("id"), "date": d, "event_date": ev.get("date"),
                        "home": home[0], "away": away[0],
                        "hs": home[1], "as": away[1], "round": rnd,
                        "winner": winner, "home_shootout_score": home_so, "away_shootout_score": away_so,
                        "completed": completed, "live": live, "status_state": state,
                        "display_clock": status.get("displayClock") or "",
                        "status_detail": st.get("shortDetail") or st.get("detail") or st.get("description") or ""})
    return out

def ref_url(ref):
    if isinstance(ref, dict):
        return ref.get("$ref")
    return ref

def fetch_ref(ref, cache):
    url = ref_url(ref)
    if not url: return {}
    if url not in cache:
        cache[url] = fetch_json(url)
    return cache[url]

def fetch_top_scorers(limit=5):
    """Fetch current Golden Boot leaders from ESPN's public core API."""
    data = fetch_json(ESPN_LEADERS)
    cats = data.get("categories", [])
    cat = next((c for c in cats if c.get("name") == "goalsLeaders"), None)
    if not cat:
        cat = next((c for c in cats if c.get("abbreviation") == "G" or c.get("displayName") == "Goals"), None)
    leaders = (cat or {}).get("leaders") or []
    if not leaders:
        return None

    def goals_of(row):
        try: return int(row.get("value") or 0)
        except (TypeError, ValueError): return 0

    top_goals = goals_of(leaders[0])
    display_rows = leaders[:limit]
    cutoff_goals = goals_of(display_rows[-1])
    needed = list(display_rows)
    for row in leaders[limit:]:
        if goals_of(row) != cutoff_goals:
            break
        needed.append(row)

    cache = {}
    current = []
    golden_boot_leaders = []
    last_goals = None
    last_rank = 0
    for i, row in enumerate(needed):
        goals = goals_of(row)
        rank = last_rank if goals == last_goals else i + 1
        last_goals = goals
        last_rank = rank
        athlete = fetch_ref(row.get("athlete"), cache)
        team_obj = fetch_ref(row.get("team"), cache)
        player = athlete.get("displayName") or athlete.get("fullName") or athlete.get("shortName")
        team = norm(team_obj.get("displayName") or team_obj.get("name") or athlete.get("citizenship") or "")
        if not player:
            continue
        current.append({"rank": rank, "player": player, "team": team, "goals": goals})
        if goals == top_goals:
            golden_boot_leaders.append(player)

    if not current or not golden_boot_leaders:
        return None
    return current, golden_boot_leaders

def is_placeholder_team(team):
    if not team:
        return True
    name = team.get("displayName") or team.get("name") or team.get("location") or ""
    if team.get("isActive") is True:
        return False
    markers = ("Group ", "Third Place", "Winner", "2nd Place")
    return not name or any(m in name for m in markers)

def fetch_r32_qualified_teams():
    """Return teams ESPN has already placed into Round-of-32 fixtures."""
    data = fetch_json(ESPN_R32)
    teams = set()
    for ev in data.get("events", []):
        season = ev.get("season") or {}
        if season.get("slug") != "round-of-32":
            continue
        for comp in (ev.get("competitions") or [])[:1]:
            for row in comp.get("competitors", []):
                team = row.get("team") or {}
                if is_placeholder_team(team):
                    continue
                name = norm(team.get("displayName") or team.get("name") or "")
                if name:
                    teams.add(name)
    return sorted(teams)

def scoreable(m):
    return bool(m.get("played"))

def status_sig(m):
    return (m.get("home_score"), m.get("away_score"), bool(m.get("played")),
            bool(m.get("live")), m.get("status_state") or "",
            m.get("display_clock") or "", m.get("status_detail") or "",
            m.get("winner") or "", m.get("home_shootout_score"), m.get("away_shootout_score"))

def fetched_sig(m, hs=None, as_=None):
    return (hs if hs is not None else m["hs"], as_ if as_ is not None else m["as"],
            bool(m["completed"]), bool(m["live"]), m.get("status_state") or "",
            m.get("display_clock") or "", m.get("status_detail") or "",
            m.get("winner") or "", m.get("home_shootout_score"), m.get("away_shootout_score"))

def apply_match_state(dst, m, hs=None, as_=None):
    dst["home_score"] = hs if hs is not None else m["hs"]
    dst["away_score"] = as_ if as_ is not None else m["as"]
    dst["played"] = bool(m["completed"])
    dst["live"] = bool(m["live"])
    dst["status_state"] = m.get("status_state") or ""
    dst["display_clock"] = m.get("display_clock") or ""
    dst["status_detail"] = m.get("status_detail") or ""
    if m.get("round"):
        if m.get("winner"):
            dst["winner"] = m["winner"]
        else:
            dst.pop("winner", None)
        if m.get("home_shootout_score") is not None or m.get("away_shootout_score") is not None:
            dst["home_shootout_score"] = m.get("home_shootout_score")
            dst["away_shootout_score"] = m.get("away_shootout_score")
        else:
            dst.pop("home_shootout_score", None)
            dst.pop("away_shootout_score", None)

def apply_match_meta(dst, m):
    if m.get("id"):
        dst["id"] = m["id"]
    dst["date"] = display_match_date(m.get("event_date"), m["date"])
    dst["home"] = m["home"]
    dst["away"] = m["away"]
    dst["round"] = m["round"]

# ---------------- update results store ----------------
def update_store():
    store = json.load(open(P("results_store.json"), encoding="utf-8"))
    gmatches = store["group_matches"]
    # index group fixtures by unordered team pair
    by_pair = {}
    for g in gmatches:
        by_pair[frozenset((norm(g["home"]), norm(g["away"])))] = g
    results = fetch_espn()
    existing_ko = {}
    for km in store.get("knockout_matches", []):
        if km.get("id"):
            existing_ko[("id", km["id"])] = km
        elif km.get("round") and km.get("home") and km.get("away"):
            existing_ko[(km["round"], frozenset((norm(km["home"]), norm(km["away"]))))] = km
    champion = store.get("champion")
    changed = False
    for m in results:
        pair = frozenset((m["home"], m["away"]))
        is_group = (m["round"] is None) and (pair in by_pair)
        if is_group:
            g = by_pair[pair]
            # orient score to fixture home/away
            if norm(g["home"]) == m["home"]:
                hs, as_ = m["hs"], m["as"]
            else:
                hs, as_ = m["as"], m["hs"]
            if m["completed"] or m["live"]:
                if status_sig(g) != fetched_sig(m, hs, as_):
                    apply_match_state(g, m, hs, as_)
                    changed = True
        elif m["round"]:
            pair_key = (m["round"], pair)
            key = ("id", m["id"]) if m.get("id") else pair_key
            km = existing_ko.get(key) or existing_ko.get(pair_key, {"round": m["round"], "home": m["home"], "away": m["away"]})
            if key != pair_key:
                existing_ko.pop(pair_key, None)
            meta_sig = (km.get("id") or "", km.get("date"), km.get("home"), km.get("away"), km.get("round"))
            fetched_meta = (m.get("id") or "", display_match_date(m.get("event_date"), m["date"]), m["home"], m["away"], m["round"])
            if meta_sig != fetched_meta:
                apply_match_meta(km, m)
                existing_ko[key] = km
                changed = True
            if (m["completed"] or m["live"]) and status_sig(km) != fetched_sig(m):
                apply_match_state(km, m)
                existing_ko[key] = km
                changed = True
            if m["completed"]:
                regulation = None
                if went_to_extra_time(m):
                    if km.get("home_score_90") is None or km.get("away_score_90") is None:
                        try:
                            regulation = fetch_regulation_score(m.get("id"))
                        except Exception as e:
                            sys.stderr.write(f"warn: fetch regulation score for {m.get('id')} failed: {e}\n")
                else:
                    regulation = (m["hs"], m["as"])
                if regulation is not None:
                    hs90, as90 = regulation
                    if (km.get("home_score_90"), km.get("away_score_90")) != (hs90, as90):
                        km["home_score_90"], km["away_score_90"] = hs90, as90
                        existing_ko[key] = km
                        changed = True
            if m["round"] == "Final" and m["completed"]:
                champion = m.get("winner") or (m["home"] if m["hs"] >= m["as"] else m["away"])
    if store.get("knockout_matches") != list(existing_ko.values()):
        store["knockout_matches"] = list(existing_ko.values())
        changed = True
    if champion and store.get("champion") != champion:
        store["champion"] = champion
        changed = True
    try:
        top_scorers = fetch_top_scorers()
    except Exception as e:
        sys.stderr.write(f"warn: fetch top scorers failed: {e}\n")
        top_scorers = None
    if top_scorers:
        current, gb_leaders = top_scorers
        if store.get("current_top_scorers") != current:
            store["current_top_scorers"] = current
            changed = True
        if store.get("golden_boot_leaders") != gb_leaders:
            store["golden_boot_leaders"] = gb_leaders
            changed = True
    try:
        qualified_r32 = fetch_r32_qualified_teams()
    except Exception as e:
        sys.stderr.write(f"warn: fetch Round-of-32 qualified teams failed: {e}\n")
        qualified_r32 = None
    if qualified_r32 is not None and store.get("qualified_r32_teams", []) != qualified_r32:
        store["qualified_r32_teams"] = qualified_r32
        changed = True
    if not changed:
        print("no ESPN match changes; site rebuild skipped")
        return store, False
    store["last_updated"] = datetime.date.today().isoformat()
    json.dump(store, open(P("results_store.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=2)
    played = sum(1 for g in gmatches if g["played"])
    live = sum(1 for g in gmatches if g.get("live"))
    print(f"results updated: {played}/72 group played, {live} live, {len(existing_ko)} knockout, champion={store.get('champion')}")
    return store, True

# ---------------- scoring ----------------
STAGE_PTS = {"r32":4,"r16":5,"qf":7,"sf":10,"final":14,"champion":16}
GB_POINTS = 12
def golden_boot_finalized(store):
    return any(
        m.get("round") == "Final" and m.get("played") and not m.get("live")
        for m in store.get("knockout_matches", [])
    )

def golden_boot_pick_hits_current_leader(d, store):
    leaders={canon_scorer(x) for x in store.get("golden_boot_leaders",[]) if x}
    picks={canon_scorer(p) for p in d["golden_boot"] if p}
    return bool(leaders and (picks&leaders))

def match_points(pred, actual):
    if None in pred or None in actual: return 0,""
    hg,ag = pred; hs,as_ = actual
    exact = (hg==hs and ag==as_)
    correct = ((hg-ag>0)==(hs-as_>0)) and ((hg-ag<0)==(hs-as_<0))
    if exact and (hs+as_)>3: return 3,"מדויק 4+ גולים"
    if exact: return 2,"מדויק"
    if correct: return 1,"כיוון נכון"
    return 0,""

def regulation_score(m):
    """Use the 90-minute score for bets, falling back for legacy normal-time data."""
    if m.get("home_score_90") is not None and m.get("away_score_90") is not None:
        return m["home_score_90"], m["away_score_90"]
    # Never award points from a 120-minute score while the summary request is unavailable.
    if went_to_extra_time(m):
        return None, None
    return m.get("home_score"), m.get("away_score")
def pred_sets(d):
    def teams(rms):
        s=set()
        for m in rms:
            for side in m:
                if side.get("team"): s.add(norm(side["team"]))
        return s
    return {"r32":teams(d["r32"]),"r16":teams(d["r16"]),"qf":teams(d["qf"]),
            "sf":teams(d["sf"]),"final":teams([d["final"]]),
            "champion":{norm(d["champion"])} if d.get("champion") else set()}

def is_stage_placeholder_name(name):
    if not name:
        return True
    markers = ("Group ", "Third Place", "Winner", "2nd Place", "Round of 32", "Round of 16",
               "Quarterfinal", "Semi-Final")
    return any(m in str(name) for m in markers)

def match_winner(m):
    if m.get("winner"):
        return m["winner"]
    hs, as_ = m.get("home_score"), m.get("away_score")
    if hs is None or as_ is None or hs == as_:
        return None
    return m.get("home") if hs > as_ else m.get("away")

def reached_sets(store):
    reached={k:set() for k in STAGE_PTS}
    reached["r32"].update(norm(t) for t in store.get("qualified_r32_teams", []) if t)
    r2s={"Round of 32":"r32","Round of 16":"r16","Quarterfinals":"qf","Semi-Finals":"sf","Final":"final"}
    next_stage={"r32":"r16","r16":"qf","qf":"sf","sf":"final"}
    for m in store.get("knockout_matches",[]):
        st=r2s.get(m.get("round"))
        if st:
            for t in (m.get("home"),m.get("away")):
                if not is_stage_placeholder_name(t):
                    reached[st].add(norm(t))
        if not m.get("played"): continue
        winner = match_winner(m)
        if st in next_stage and winner:
            reached[next_stage[st]].add(norm(winner))
    if store.get("champion"): reached["champion"].add(norm(store["champion"]))
    return reached

def actual_group_standings(store, preds):
    """Build current group tables from played/live group-stage results."""
    groups_src = next(iter(preds.values())).get("groups", [])
    groups = []
    team_to_group = {}
    for g in groups_src:
        group = g["group"]
        rows = {}
        for r in g["standings"]:
            team = norm(r["team"])
            rows[team] = {"team": team, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "Pts": 0}
            team_to_group[team] = group
        groups.append({"group": group, "rows": rows})

    for m in store["group_matches"]:
        if not (m.get("played") or m.get("live")):
            continue
        if m.get("home_score") is None or m.get("away_score") is None:
            continue
        home, away = norm(m["home"]), norm(m["away"])
        group = team_to_group.get(home) or team_to_group.get(away)
        if not group:
            continue
        table = next((g["rows"] for g in groups if g["group"] == group), None)
        if table is None:
            continue
        for team in (home, away):
            table.setdefault(team, {"team": team, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "Pts": 0})
        hs, as_ = m["home_score"], m["away_score"]
        table[home]["GF"] += hs; table[home]["GA"] += as_
        table[away]["GF"] += as_; table[away]["GA"] += hs
        if hs > as_:
            table[home]["W"] += 1; table[away]["L"] += 1; table[home]["Pts"] += 3
        elif hs < as_:
            table[away]["W"] += 1; table[home]["L"] += 1; table[away]["Pts"] += 3
        else:
            table[home]["D"] += 1; table[away]["D"] += 1
            table[home]["Pts"] += 1; table[away]["Pts"] += 1

    out = []
    for g in groups:
        rows = sorted(g["rows"].values(), key=lambda r: (-r["Pts"], -(r["GF"] - r["GA"]), -r["GF"], r["team"]))
        out.append({"group": g["group"], "rows": [
            {"place": i, "team": r["team"], "W": r["W"], "D": r["D"], "L": r["L"],
             "GF": r["GF"], "GA": r["GA"], "Pts": r["Pts"]}
            for i, r in enumerate(rows, 1)
        ]})
    return out

def third_place_table(groups, qualified_teams=None):
    qualified_teams = {norm(t) for t in (qualified_teams or []) if t}
    rows = []
    for g in groups or []:
        third = next((r for r in g.get("rows", g.get("standings", [])) if r.get("place") == 3), None)
        if not third:
            continue
        gf = third.get("GF", 0) or 0
        ga = third.get("GA", 0) or 0
        rows.append({"group": g["group"], "team": third["team"], "Pts": third.get("Pts", 0) or 0,
                     "GD": gf - ga, "GF": gf})
    rows.sort(key=lambda r: (-r["Pts"], -r["GD"], -r["GF"],
                             norm(r["team"]) not in qualified_teams if qualified_teams else False,
                             r["team"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["qualified"] = norm(r["team"]) in qualified_teams if qualified_teams else i <= 8
    return rows

def score_one(d, store):
    gmap={g["num"]:g for g in store["group_matches"]}
    gp=0; gh=0; gd=[]
    for m in d["group_matches"]:
        g=gmap.get(m["num"])
        if not g or not g.get("played"): continue
        pts,lab=match_points((m["hg"],m["ag"]),(g["home_score"],g["away_score"]))
        if pts>0: gh+=1
        gp+=pts
        gd.append({"num":m["num"],"home":m["home"],"away":m["away"],
                   "pred":f"{m['hg']}-{m['ag']}","actual":f"{g['home_score']}-{g['away_score']}",
                   "pts":pts,"label":lab})
    kp=0; kd=[]
    predr={"Round of 32":d["r32"],"Round of 16":d["r16"],"Quarterfinals":d["qf"],
           "Semi-Finals":d["sf"],"Third-Place":[d["third"]],"Final":[d["final"]]}
    for am in store.get("knockout_matches",[]):
        if am.get("home_score") is None or not am.get("played"): continue
        actual = regulation_score(am)
        if None in actual: continue
        rnd=am.get("round"); pair={norm(am["home"]),norm(am["away"])}
        for pm in predr.get(rnd,[]):
            pp={norm(pm[0]["team"]),norm(pm[1]["team"])}
            if pp==pair and len(pair)==2:
                if norm(pm[0]["team"])==norm(am["home"]): pred=(pm[0]["score"],pm[1]["score"])
                else: pred=(pm[1]["score"],pm[0]["score"])
                pts,lab=match_points(pred,actual)
                kp+=pts; kd.append({"round":rnd,"match":f"{am['home']} {actual[0]}-{actual[1]} {am['away']}","pts":pts,"label":lab})
                break
    ps=pred_sets(d); rs=reached_sets(store); pp=0; pb={}
    for st,v in STAGE_PTS.items():
        hits=ps[st]&rs[st]; pb[st]={"hits":sorted(hits),"points":len(hits)*v}; pp+=len(hits)*v
    gb_current=golden_boot_pick_hits_current_leader(d, store)
    gb=GB_POINTS if golden_boot_finalized(store) and gb_current else 0
    return {"name":d["name"],"group_points":gp,"group_hits":gh,"group_details":gd,
            "knockout_points":kp,"knockout_details":kd,"progression_points":pp,
            "progression_breakdown":pb,"golden_boot_points":gb,
            "golden_boot_current":gb_current,"total":gp+kp+pp+gb}

# ---------------- site data + html ----------------
def participant_payload(d, store, sc):
    gmap={g["num"]:g for g in store["group_matches"]}
    det={x["num"]:x for x in sc["group_details"]}
    matches=[]
    for m in d["group_matches"]:
        g=gmap.get(m["num"])
        scored = g and g.get("played")
        matches.append({"num":m["num"],"date":m["date"],"home":m["home"],"away":m["away"],
            "pred":f"{m['hg']}-{m['ag']}",
            "actual":(f"{g['home_score']}-{g['away_score']}" if scored else None),
            "pts":det.get(m["num"],{}).get("pts",0) if scored else None,
            "live":bool(g and g.get("live")),
            "display_clock":(g.get("display_clock") if g else ""),
            "status_detail":(g.get("status_detail") if g else "")})
    def rnd(ms):
        out=[]
        for m in ms:
            t1,s1=m[0]["team"],m[0]["score"]; t2,s2=m[1]["team"],m[1]["score"]
            out.append({"t1":t1,"s1":s1,"t2":t2,"s2":s2,"win":t1 if (s1 or 0)>=(s2 or 0) else t2})
        return out
    bracket={"r32":rnd(d["r32"]),"r16":rnd(d["r16"]),"qf":rnd(d["qf"]),
             "sf":rnd(d["sf"]),"final":rnd([d["final"]]),"third":rnd([d["third"]])}
    standings=[{"group":g["group"],"rows":[{"place":x["place"],"team":x["team"],"W":x["W"],
        "D":x["D"],"L":x["L"],"GF":x["GF"],"GA":x["GA"],"Pts":x["Pts"]} for x in g["standings"]]}
        for g in d["groups"]]
    r32_teams = {side["team"] for m in d["r32"] for side in m if side.get("team")}
    return {"name":d["name"],"champion":d["champion"],"gb":d["golden_boot"],"winner":d["winner_bet"],
            "matches":matches,"bracket":bracket,"standings":standings,
            "third_places":third_place_table(standings, r32_teams),
            "totals":{"group":sc["group_points"],"knockout":sc["knockout_points"],
                      "progression":sc["progression_points"],"gb":sc["golden_boot_points"],
                      "gb_current":sc.get("golden_boot_current", False),
                      "total":sc["total"],"rank":sc["rank"]}}

def prediction_outcome_splits(preds):
    totals = {}
    for d in preds.values():
        for m in d.get("group_matches", []):
            row = totals.setdefault(m["num"], {"total": 0, "home": 0, "draw": 0, "away": 0})
            row["total"] += 1
            hg, ag = m.get("hg"), m.get("ag")
            if hg > ag:
                row["home"] += 1
            elif hg < ag:
                row["away"] += 1
            else:
                row["draw"] += 1
    for row in totals.values():
        total = row["total"] or 1
        row["pct"] = {
            "home": round(row["home"] * 100 / total),
            "draw": round(row["draw"] * 100 / total),
            "away": round(row["away"] * 100 / total),
        }
    return totals

def knockout_prediction_splits(preds):
    def valid_score(value):
        return isinstance(value, (int, float))
    totals = {}
    stages = {
        "r32": "Round of 32",
        "r16": "Round of 16",
        "qf": "Quarterfinals",
        "sf": "Semi-Finals",
        "third": "Third-Place",
        "final": "Final",
    }
    for name, d in preds.items():
        for stage_key, rnd in stages.items():
            matches = [d["final"]] if stage_key == "final" else [d["third"]] if stage_key == "third" else d.get(stage_key, [])
            for pm in matches:
                if not pm or len(pm) < 2:
                    continue
                t1, t2 = pm[0].get("team"), pm[1].get("team")
                if not t1 or not t2:
                    continue
                key = (rnd, frozenset((norm(t1), norm(t2))))
                row = totals.setdefault(key, {"total": 0, "home": 0, "draw": 0, "away": 0, "rows": []})
                row["total"] += 1
                s1, s2 = pm[0].get("score"), pm[1].get("score")
                if valid_score(s1) and valid_score(s2) and s1 > s2:
                    winner = t1
                elif valid_score(s1) and valid_score(s2) and s1 < s2:
                    winner = t2
                else:
                    winner = None
                row["rows"].append({"name": name, "t1": t1, "s1": s1, "t2": t2, "s2": s2, "winner": winner})
    return totals

ROUND_KEYS = {
    "Round of 32": "r32",
    "Round of 16": "r16",
    "Quarterfinals": "qf",
    "Semi-Finals": "sf",
    "Third-Place": "third",
    "Final": "final",
}

def knockout_payload(store, prediction_splits=None, rank_by_name=None):
    def valid_score(value):
        return isinstance(value, (int, float))
    def score_text(value):
        return str(value) if valid_score(value) else "—"
    prediction_splits = prediction_splits or {}
    rank_by_name = rank_by_name or {}
    rows = []
    for i, m in enumerate(store.get("knockout_matches", []), 1):
        rnd = m.get("round") or ""
        pred = prediction_splits.get((rnd, frozenset((norm(m.get("home")), norm(m.get("away"))))))
        prediction = None
        if pred:
            aligned_rows = []
            home_count = draw_count = away_count = 0
            for r in pred["rows"]:
                if norm(r["t1"]) == norm(m.get("home")):
                    hs, aas = r["s1"], r["s2"]
                else:
                    hs, aas = r["s2"], r["s1"]
                if valid_score(hs) and valid_score(aas) and hs > aas:
                    home_count += 1
                elif valid_score(hs) and valid_score(aas) and hs < aas:
                    away_count += 1
                elif valid_score(hs) and valid_score(aas):
                    draw_count += 1
                aligned_rows.append({"name": r["name"], "rank": rank_by_name.get(r["name"], 999),
                                     "pred": f"{score_text(hs)}-{score_text(aas)}",
                                     "winner": m.get("home") if valid_score(hs) and valid_score(aas) and hs > aas else m.get("away") if valid_score(hs) and valid_score(aas) and hs < aas else None})
            aligned_rows.sort(key=lambda r: (r["rank"], r["name"]))
            total = pred["total"] or 1
            prediction = {"total": pred["total"], "home": home_count, "draw": draw_count, "away": away_count,
                          "pct": {"home": round(home_count * 100 / total),
                                  "draw": round(draw_count * 100 / total),
                                  "away": round(away_count * 100 / total)},
                          "rows": aligned_rows}
        rows.append({"num": m.get("num") or 72 + i,
                     "stage": ROUND_KEYS.get(rnd, "knockout"),
                     "round": rnd,
                     "date": m.get("date") or "",
                     "home": m.get("home") or "",
                     "away": m.get("away") or "",
                     "hs": m.get("home_score"),
                     "as": m.get("away_score"),
                     "hs90": m.get("home_score_90"),
                     "as90": m.get("away_score_90"),
                     "winner": m.get("winner"),
                     "home_so": m.get("home_shootout_score"),
                     "away_so": m.get("away_shootout_score"),
                     "played": bool(m.get("played")),
                     "live": bool(m.get("live")),
                     "display_clock": m.get("display_clock") or "",
                     "status_detail": m.get("status_detail") or "",
                     "prediction_split": prediction})
    return rows

def predicted_r32_teams(d):
    return pred_sets(d)["r32"]

def build_stats(preds, scores, store):
    rank_by_name = {s["name"]: s["rank"] for s in scores}
    score_by_name = {s["name"]: s for s in scores}
    score_rows = []
    for s in scores:
        result_details = s["group_details"] + s["knockout_details"]
        exact = sum(1 for m in result_details if m["pts"] >= 2)
        big_exact = sum(1 for m in result_details if m["pts"] == 3)
        direction_only = sum(1 for m in result_details if m["pts"] == 1)
        score_rows.append({"name": s["name"], "rank": s["rank"],
                           "correct": exact + direction_only,
                           "exact": exact, "big_exact": big_exact,
                           "direction_only": direction_only})
    score_rows.sort(key=lambda r: (-r["correct"], -r["exact"], -r["big_exact"], r["rank"], r["name"]))

    guaranteed = {norm(t) for t in store.get("qualified_r32_teams", []) if t}
    pending = set()
    qual_rows = []
    qual_by_name = {}
    for nm, d in preds.items():
        picks = predicted_r32_teams(d)
        guaranteed_hits = sorted(picks & guaranteed)
        pending_matches = sorted(picks & pending)
        row = {"name": nm, "rank": rank_by_name[nm],
               "guaranteed_hits": len(guaranteed_hits),
               "guaranteed_teams": guaranteed_hits,
               "pending_matches": pending_matches}
        qual_by_name[nm] = row
        qual_rows.append(row)
    qual_rows.sort(key=lambda r: (-r["guaranteed_hits"], -len(r["pending_matches"]), r["rank"], r["name"]))

    combined_rows = []
    for row in score_rows:
        q = qual_by_name[row["name"]]
        progression = score_by_name[row["name"]].get("progression_breakdown", {})
        combined_rows.append({"name": row["name"], "rank": row["rank"],
                              "correct": row["correct"], "exact": row["exact"],
                              "big_exact": row["big_exact"],
                              "qualifiers": q["guaranteed_hits"],
                              "guaranteed_teams": q["guaranteed_teams"],
                              "r32_qualifiers": len(progression.get("r32", {}).get("hits", [])),
                              "qualified_r32_teams": progression.get("r32", {}).get("hits", []),
                              "r16_qualifiers": len(progression.get("r16", {}).get("hits", [])),
                              "qualified_r16_teams": progression.get("r16", {}).get("hits", []),
                              "qf_qualifiers": len(progression.get("qf", {}).get("hits", [])),
                              "qualified_qf_teams": progression.get("qf", {}).get("hits", []),
                              "sf_qualifiers": len(progression.get("sf", {}).get("hits", [])),
                              "qualified_sf_teams": progression.get("sf", {}).get("hits", []),
                              "final_qualifiers": len(progression.get("final", {}).get("hits", [])),
                              "qualified_final_teams": progression.get("final", {}).get("hits", []),
                              "champion_hits": len(progression.get("champion", {}).get("hits", [])),
                              "champion_teams": progression.get("champion", {}).get("hits", [])})

    reached = reached_sets(store)
    reached_by_stage = {st: sorted(teams) for st, teams in reached.items()}
    return {"rows": combined_rows,
            "score_accuracy": {"rows": score_rows},
            "qualification_accuracy": {
                "source": "espn_round_of_32_fixtures",
                "available": True,
                "guaranteed_teams": sorted(guaranteed),
                "reached_teams_by_stage": reached_by_stage,
                "reached_r16_teams": sorted(reached["r16"]),
                "pending_teams": sorted(pending),
                "rows": qual_rows}}

def build_all():
    store, changed = update_store()
    if not changed and os.environ.get("MUNDIAL_FORCE_REBUILD") != "1":
        return
    preds = json.load(open(P("predictions.json"), encoding="utf-8"))
    scores=[]
    for nm,d in preds.items():
        d["name"]=nm; scores.append(score_one(d, store))
    scores.sort(key=lambda r:(-r["total"],-r["group_hits"],r["name"]))
    for i,s in enumerate(scores,1): s["rank"]=i
    # ---- daily rank-history snapshot (upsert one entry per date) ----
    hist_path=P("history.json"); history={}
    if os.path.exists(hist_path):
        try: history=json.load(open(hist_path, encoding="utf-8"))
        except Exception: history={}
    history[store["last_updated"]]={s["name"]:[s["rank"],s["total"]] for s in scores}
    json.dump(history, open(hist_path,"w",encoding="utf-8"), ensure_ascii=False)
    json.dump(scores, open(P("scores.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=2)
    sc_by={s["name"]:s for s in scores}

    gb_tally=Counter(); champ_tally=Counter(); picks=[]
    participants={}
    for nm,d in preds.items():
        participants[nm]=participant_payload(d, store, sc_by[nm])
        for p in d["golden_boot"]:
            if p: gb_tally[canon_scorer(p)]+=1
        if d["champion"]: champ_tally[d["champion"]]+=1
        picks.append({"name":nm,"gb1":d["golden_boot"][0] or "","gb2":d["golden_boot"][1] or "",
                      "champion":d["champion"] or "","winner":d["winner_bet"] or ""})
    played=sum(1 for g in store["group_matches"] if g["played"])
    live=sum(1 for g in store["group_matches"] if g.get("live"))
    actual_standings = actual_group_standings(store, preds)
    outcome_splits = prediction_outcome_splits(preds)
    knockout_splits = knockout_prediction_splits(preds)
    rank_by_name = {s["name"]: s["rank"] for s in scores}
    gb_counts = dict(gb_tally)
    current_top_scorers = []
    for row in store.get("current_top_scorers", []):
        player = row.get("player")
        current_row = dict(row)
        current_row["guess_count"] = gb_counts.get(canon_scorer(player), 0)
        current_top_scorers.append(current_row)
    gb_finalized = golden_boot_finalized(store)
    site={"meta":{"tournament":store["tournament"],"updated":store["last_updated"],
                  "played":played,"live":live,"total_group":len(store["group_matches"]),"source":store["source"]},
          "leaderboard":[{"rank":s["rank"],"name":s["name"],
                          "results":s["group_points"] + s["knockout_points"],
                          "group":s["group_points"],"knockout":s["knockout_points"],
                          "progression":s["progression_points"],
                          "progression_r32":s["progression_breakdown"]["r32"]["points"],
                          "progression_r16":s["progression_breakdown"]["r16"]["points"],
                          "progression_qf":s["progression_breakdown"]["qf"]["points"],
                          "progression_sf":s["progression_breakdown"]["sf"]["points"],
                          "progression_final":s["progression_breakdown"]["final"]["points"],
                          "progression_champion":s["progression_breakdown"]["champion"]["points"],
                          "gb":s["golden_boot_points"],
                          "gb_current":s.get("golden_boot_current", False),
                          "total":s["total"],"hits":s["group_hits"]} for s in scores],
          "goldenboot":{"points":GB_POINTS,
                        "finalized":gb_finalized,
                        "current":current_top_scorers,
                        "current_note":store.get("current_top_scorers_note",""),
                        "tally":gb_tally.most_common(),"champions":champ_tally.most_common(),"picks":picks},
          "results":[{"num":g["num"],"date":g["date"],"home":g["home"],"away":g["away"],
                      "hs":g["home_score"],"as":g["away_score"],"played":g["played"],
                      "live":bool(g.get("live")),"display_clock":g.get("display_clock") or "",
                      "status_detail":g.get("status_detail") or "",
                      "outcome_split":outcome_splits.get(g["num"], {"total": 0, "home": 0, "draw": 0, "away": 0,
                                                                   "pct": {"home": 0, "draw": 0, "away": 0}})}
                     for g in store["group_matches"]],
          "knockout_results":knockout_payload(store, knockout_splits, rank_by_name),
          "standings":actual_standings,
          "third_places":third_place_table(actual_standings, store.get("qualified_r32_teams", [])),
          "participants":participants,
          "stats":build_stats(preds, scores, store)}
    # optional payments
    pay_path=P("payments.json")
    if os.path.exists(pay_path):
        site["payments"]=json.load(open(pay_path, encoding="utf-8"))
    # optional recap intro (Hebrew letter + YouTube links) shown as a popup on the recap tab
    intro_path=P("recap_intro.json")
    if os.path.exists(intro_path):
        site["recap_intro"]=json.load(open(intro_path, encoding="utf-8"))
    site["history"]=history
    json.dump(site, open(P("site_data.json"),"w",encoding="utf-8"), ensure_ascii=False)

    # build index.html
    tpl=open(P("site_template.html"), encoding="utf-8").read()
    data=json.dumps(site, ensure_ascii=False).replace("</","<\\/")
    pw=os.environ.get("GATE_PASSWORD","")
    gate=hashlib.sha256(pw.encode()).hexdigest() if pw else ""
    html=tpl.replace("/*DATA*/", data).replace("/*GATE*/", gate)
    open(P("index.html"),"w",encoding="utf-8").write(html)
    print(f"site rebuilt: {len(participants)} participants, leader={scores[0]['name']} ({scores[0]['total']})")

if __name__ == "__main__":
    build_all()
