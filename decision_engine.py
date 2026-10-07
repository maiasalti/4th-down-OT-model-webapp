"""
4th-down decision engine for NFL overtime (2025 rules: both teams get a
possession, then sudden death).

For each option the engine builds the situation that follows and asks how
likely the offense is to win from there:

    wp_go   = p_conv * V(converted) + (1 - p_conv) * V(turned over on downs)
    wp_fg   = p_make * V(kick good) + (1 - p_make) * V(kick missed)
    wp_punt = V(opponent starts where the punt model says)

    decision = argmax(wp_go, wp_fg, wp_punt)

V comes from one of two valuations (env ENGINE_VALUE_MODEL):

  "drive" (default)  An OT game tree over drive outcomes. Each drive ends in
      TD / FG / defensive score / nothing with probabilities from
      models/ot (fit on ~22k NFL drives, by start yard line, spread and home).
      Those roll up through the OT rules: first possession, second possession
      needing to match, then next score wins. Field position is worth what it
      is in OT: handing the ball over at your own 10 means the other team only
      needs a field goal from there.

  "wp"  The regulation win-probability model (models/wp), scoring each OT
      state as the equivalent late-4th-quarter state, plus the OT rules that
      end the game outright. Kept for comparison: even retrained it values
      field position like regulation, where a field goal doesn't end the
      game, so it is too willing to go for it deep in your own territory.

analyze_many() scores many yard lines at once (the field sweep) with one call
per submodel; analyze() is the single-situation wrapper.
"""

from __future__ import annotations

import numpy as np

import os

from models import (
    conversion_probability,
    fg_make_probability,
    ot_drive_model,
    punt_opponent_start,
    win_probability,
)

VALUE_MODEL = os.environ.get("ENGINE_VALUE_MODEL", "drive")

KICKOFF_TOUCHBACK_YARDLINE = 65.0  # 2025 rule: touchback to the receiving 35
OT_SECONDS = 480.0                 # assume ~8 min left in the 10-min OT period
PLAY_SECONDS = 8.0
MAX_FG_DISTANCE = 66


def _state(n, **kw) -> dict:
    return {k: np.broadcast_to(np.asarray(v, dtype=float), (n,)).astype(float) for k, v in kw.items()}


def _flip(s: dict) -> dict:
    """Same situation from the other team's point of view (they now have the ball)."""
    out = dict(s)
    out["score_differential"] = -s["score_differential"]
    out["offense_timeouts"], out["defense_timeouts"] = s["defense_timeouts"], s["offense_timeouts"]
    out["home"] = 1.0 - s["home"]
    out["posteam_spread"] = -s["posteam_spread"]
    out["overtime_possession_number"] = s["overtime_possession_number"] + 1
    return out


def _with(s: dict, **kw) -> dict:
    n = len(s["yardline_100"])
    out = dict(s)
    out.update(_state(n, **kw))
    return out


def analyze_many(
    yardlines,
    yards_to_go: int,
    score_differential: int,
    possession_number: int,
    opponent_result=None,
    is_playoffs: bool = False,
    off_epa: float = 0.0,
    def_epa: float = 0.0,
    off_success_rate: float = 0.42,
    off_ppg: float = 23.0,
    def_success_rate: float = 0.42,
    def_ppg: float = 23.0,
    shotgun: int = 1,
    no_huddle: int = 0,
    punt_distance_roll6: float = None,
    inside_twenty_rate_roll6: float = None,
    is_dome: bool = False,
    wind: float = 8.0,
    wind_gust: float = None,
    temp: float = 65.0,
    is_precipitation: bool = False,
    surface_is_grass: bool = True,
    altitude_ft: float = 0.0,
    fg_make_rate_roll6: float = None,
    offense_timeouts: int = 2,
    defense_timeouts: int = 2,
    is_home: bool = None,
    posteam_spread: float = 0.0,
) -> list[dict]:
    yl = np.atleast_1d(np.asarray(yardlines, dtype=float))
    n = len(yl)
    ytg = np.minimum(float(yards_to_go), yl)  # can't need more yards than the goal line

    # OT starts tied. Only the 2nd possession can be behind, by what the first
    # team scored: nothing, a field goal or a touchdown.
    if possession_number == 2:
        if opponent_result:
            sd = {"td": -7.0, "fg": -3.0}.get(opponent_result, 0.0)
        else:
            sd = -7.0 if score_differential <= -4 else (-3.0 if score_differential < 0 else 0.0)
    else:
        sd = 0.0

    # ---- submodels -------------------------------------------------------
    p_conv = conversion_probability(
        ytg, yl, score_differential=sd, game_seconds_remaining=OT_SECONDS, qtr=5, wp=0.5,
        temp=temp, shotgun=shotgun, no_huddle=no_huddle, off_epa=off_epa, def_epa=def_epa,
        off_success_rate=off_success_rate, off_ppg=off_ppg,
        def_success_rate=def_success_rate, def_ppg=def_ppg,
    )
    fg_dist = yl + 17
    fg_ok = fg_dist <= MAX_FG_DISTANCE
    p_fg = np.where(fg_ok, fg_make_probability(
        yl, is_dome=is_dome, wind=wind, temp=temp, wind_gust=wind_gust,
        is_precipitation=is_precipitation, fg_make_rate_roll6=fg_make_rate_roll6,
        surface_is_grass=surface_is_grass, altitude_ft=altitude_ft,
        game_seconds_remaining=OT_SECONDS, score_differential=sd, is_overtime=True,
    ), 0.0)
    punt_start = punt_opponent_start(yl, punt_distance_roll6, inside_twenty_rate_roll6)

    # ---- where the ball ends up after each outcome ------------------------
    td = ytg >= yl                                  # goal to go: converting is a touchdown
    spots = {
        "convert": np.maximum(1.0, yl - ytg),       # our new 1st down
        "fail": np.maximum(1.0, 100.0 - yl),        # their ball at the spot
        "miss": np.minimum(80.0, 100.0 - (yl + 7)), # spot of the kick (~7 yd back) or the 20
        "punt": punt_start,                          # their start, net of the return
    }
    home = 0.5 if is_home is None else float(bool(is_home))
    common = dict(n=n, sd=sd, possession_number=possession_number, spots=spots, home=home,
                  posteam_spread=float(posteam_spread))
    if VALUE_MODEL == "wp":
        v = _values_wp(**common, offense_timeouts=offense_timeouts, defense_timeouts=defense_timeouts)
    else:
        v = _values_drive(**common)

    wp_convert = np.where(td, v["td"], v["convert"])
    wp_fail, wp_make, wp_miss, wp_punt = v["fail"], v["make"], v["miss"], v["punt"]
    go = p_conv * wp_convert + (1 - p_conv) * wp_fail
    fg = np.where(fg_ok, p_fg * wp_make + (1 - p_fg) * wp_miss, -1.0)
    punt = wp_punt

    results = []
    for i in range(n):
        opts = {"go": go[i], "punt": punt[i]}
        if fg_ok[i]:
            opts["fg"] = fg[i]
        ranked = sorted(opts.items(), key=lambda kv: kv[1], reverse=True)
        margin = (ranked[0][1] - ranked[1][1]) * 100
        strength = "Strong" if margin >= 3 else "Moderate" if margin >= 1 else "Marginal"
        land = int(round(punt_start[i]))
        land_label = f"opponent's {land}" if land <= 50 else f"their own {100 - land}"
        results.append({
            "win_probabilities": {
                "go": round(float(go[i]) * 100, 1),
                "punt": round(float(punt[i]) * 100, 1),
                "fg": round(float(fg[i]) * 100, 1) if fg_ok[i] else None,
            },
            "fg_available": bool(fg_ok[i]),
            "recommendation": ranked[0][0],
            "recommendation_strength": strength,
            "margin": round(float(margin), 1),
            "details": {
                "conversion_probability": round(float(p_conv[i]) * 100, 1),
                "conversion_is_touchdown": bool(td[i]),
                "fg_make_probability": round(float(p_fg[i]) * 100, 1) if fg_ok[i] else None,
                "fg_distance": int(fg_dist[i]),
                "expected_punt_net": round(float(max(0.0, yl[i] - (100 - punt_start[i]))), 1),
                "punt_opponent_yardline_100": round(float(punt_start[i]), 1),
                "punt_landing_yardline": land_label,
            },
            "submodel_details": {
                "wp_if_convert": round(float(wp_convert[i]) * 100, 1),
                "wp_if_fail": round(float(wp_fail[i]) * 100, 1),
                "wp_if_fg_make": round(float(wp_make[i]) * 100, 1),
                "wp_if_fg_miss": round(float(wp_miss[i]) * 100, 1),
                "wp_if_punt": round(float(wp_punt[i]) * 100, 1),
            },
            "inputs": {
                "yardline_100": int(yl[i]),
                "yards_to_go": int(ytg[i]),
                "score_differential": int(sd),
                "possession_number": possession_number,
                "is_playoffs": is_playoffs,
            },
        })
    return results


# ---------------------------------------------------------------------------
# Valuation 1 (default): OT game tree over drive outcomes
# ---------------------------------------------------------------------------
_GRID = np.arange(1.0, 100.0)


def _ot_tables(posteam_spread: float, home: float) -> dict:
    """Win probability for the team with the ball at the start of a drive, on a
    1..99 yard-line grid, for each OT phase. 'us' = the offense making this
    4th-down call, 'them' = the other team."""
    m = ot_drive_model()
    P = {"us": m.probs(_GRID, posteam_spread, home), "them": m.probs(_GRID, -posteam_spread, 1.0 - home)}
    nxt = m.next_start(_GRID)
    other = {"us": "them", "them": "us"}

    # Next score wins (sudden death, or 2nd possession after a scoreless 1st):
    # S(x) = P(TD) + P(FG) + P(no score) * (1 - S_other(where they start next))
    S = {"us": np.full(99, 0.5), "them": np.full(99, 0.5)}
    for _ in range(200):
        S = {k: P[k]["td"] + P[k]["fg"] + P[k]["none"] * (1.0 - np.interp(nxt, _GRID, S[other[k]]))
             for k in S}
    ko = KICKOFF_TOUCHBACK_YARDLINE
    T = {}
    for k in ("us", "them"):
        o = other[k]
        s_o_ko = float(np.interp(ko, _GRID, S[o]))
        T[k] = {
            "S": S[k],
            # 2nd possession, down 3: TD wins, FG ties (they then receive in sudden death)
            "down3": P[k]["td"] + P[k]["fg"] * (1.0 - s_o_ko),
            # 2nd possession, down 7: TD + PAT ties (they then receive), anything else loses
            "down7": P[k]["td"] * (1.0 - s_o_ko),
        }
    for k in ("us", "them"):
        o = other[k]
        # 1st possession, tied: TD -> they need a TD, FG -> they need a FG, else they need any score
        T[k]["first"] = (P[k]["td"] * (1.0 - float(np.interp(ko, _GRID, T[o]["down7"])))
                         + P[k]["fg"] * (1.0 - float(np.interp(ko, _GRID, T[o]["down3"])))
                         + P[k]["none"] * (1.0 - np.interp(nxt, _GRID, S[o])))
    return T


def _values_drive(n, sd, possession_number, spots, home, posteam_spread) -> dict:
    T = _ot_tables(posteam_spread, home)
    us, them = T["us"], T["them"]
    at = lambda table, x: np.interp(x, _GRID, table)  # noqa: E731
    ko = KICKOFF_TOUCHBACK_YARDLINE
    one, zero = np.ones(n), np.zeros(n)
    their_turn_tied = {k: 1.0 - at(them["S"], spots[k]) for k in ("fail", "miss", "punt")}

    if possession_number == 1:
        return {"convert": at(us["first"], spots["convert"]),
                "td": one - float(at(them["down7"], ko)),
                "make": one - float(at(them["down3"], ko)),
                **their_turn_tied}
    if possession_number == 2 and sd < 0:
        need = "down7" if sd <= -7 else "down3"
        tie_then_they_receive = one - float(at(them["S"], ko))
        return {"convert": at(us[need], spots["convert"]),
                "td": one if sd > -7 else tie_then_they_receive,
                "make": tie_then_they_receive if sd == -3 else (one if sd > -3 else zero),
                "fail": zero, "miss": zero, "punt": zero}
    # 2nd possession after a scoreless 1st, or sudden death: next score wins.
    return {"convert": at(us["S"], spots["convert"]), "td": one, "make": one, **their_turn_tied}


# ---------------------------------------------------------------------------
# Valuation 2: regulation WP model on the equivalent late-4th-quarter state
# ---------------------------------------------------------------------------
def _values_wp(n, sd, possession_number, spots, home, posteam_spread,
               offense_timeouts, defense_timeouts) -> dict:
    clock_after = max(10.0, OT_SECONDS - PLAY_SECONDS)
    cur = _state(
        n, score_differential=sd, quarter=5, seconds_remaining=clock_after, yardline_100=50.0,
        down=1, ydstogo=10.0, offense_timeouts=offense_timeouts, defense_timeouts=defense_timeouts,
        is_overtime=1, overtime_possession_number=max(0, possession_number - 1),
        home=home, posteam_spread=posteam_spread, guaranteed_possession=1.0,
    )
    opp = _flip(cur)
    ko = KICKOFF_TOUCHBACK_YARDLINE
    states = [
        _with(cur, yardline_100=spots["convert"], ydstogo=np.minimum(10.0, spots["convert"])),
        _with(opp, yardline_100=spots["fail"]),
        _with(opp, score_differential=-(sd + 7), yardline_100=ko),
        _with(opp, score_differential=-(sd + 3), yardline_100=ko),
        _with(opp, yardline_100=spots["miss"]),
        _with(opp, yardline_100=spots["punt"]),
    ]
    stacked = {k: np.concatenate([b[k] for b in states]) for k in states[0]}
    w = win_probability(stacked).reshape(len(states), n)
    v = {"convert": w[0], "fail": 1 - w[1], "td": 1 - w[2], "make": 1 - w[3], "miss": 1 - w[4], "punt": 1 - w[5]}

    # OT rules that end the game outright
    if possession_number >= 3:
        v["td"] = v["make"] = np.ones(n)
    elif possession_number == 2:
        def settle(new_sd, if_tied):
            return np.ones(n) if new_sd > 0 else (np.zeros(n) if new_sd < 0 else if_tied)
        v["td"] = settle(sd + 7, v["td"])
        v["make"] = settle(sd + 3, v["make"])
        if sd < 0:  # trailing: giving the ball back without scoring ends it
            v["fail"] = v["miss"] = v["punt"] = np.zeros(n)
    return v


def analyze(yardline_100: int, **kwargs) -> dict:
    return analyze_many([yardline_100], **kwargs)[0]
