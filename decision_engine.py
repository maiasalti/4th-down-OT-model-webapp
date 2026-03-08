"""
4th-down decision engine using analytical expected-value maximisation.

Replaces the Monte Carlo simulation with the FourthDownDecisionEngine approach
from the source repo (feature/win-probability-model branch):

    wp_go   = p_conv * wp(success_state) + (1-p_conv) * [1 - wp(failure_state)]
    wp_fg   = p_make * [1 - wp(make_state)] + (1-p_make) * [1 - wp(miss_state)]
    wp_punt = 1 - wp(punt_state)

    decision = argmax(wp_go, wp_fg, wp_punt)

Uses 4 trained ML submodels:
  1. 4th-down conversion probability (XGBoost + isotonic calibration)
  2. Field goal make probability (XGBoost + isotonic calibration)
  3. Punt outcome predictor (XGBoost regressor)
  4. Win probability model (XGBoost + isotonic calibration)
"""

import numpy as np
from models import (
    get_conversion_probability,
    fg_make_probability,
    predict_punt_opponent_start,
    expected_punt_net_yards,
    WinProbabilityModel,
)

# Kickoff touchback yardline by season (receiving team's yardline_100)
_KICKOFF_TOUCHBACK = {
    2025: 65.0,   # 35-yard line
    2024: 70.0,   # 30-yard line
    2016: 75.0,   # 25-yard line
}
PUNT_TOUCHBACK_YARDLINE = 80.0  # always 20-yard line

# Default season for kickoff rules
DEFAULT_SEASON = 2025


def _kickoff_touchback_yardline(season: int = DEFAULT_SEASON) -> float:
    for cutoff in sorted(_KICKOFF_TOUCHBACK.keys(), reverse=True):
        if season >= cutoff:
            return _KICKOFF_TOUCHBACK[cutoff]
    return 75.0


def _flip_possession(state: dict) -> dict:
    """Flip possession to the other team."""
    new = dict(state)
    new["score_differential"] = -state.get("score_differential", 0.0)
    new["offense_timeouts"] = state.get("defense_timeouts", 3)
    new["defense_timeouts"] = state.get("offense_timeouts", 3)
    new["home"] = 1.0 - float(state.get("home", 0.0))
    new["posteam_spread"] = -float(state.get("posteam_spread", 0.0))
    if state.get("is_overtime", 0):
        new["overtime_possession_number"] = (
            int(state.get("overtime_possession_number", 0)) + 1
        )
    return new


def analyze(
    yardline_100: int,
    yards_to_go: int,
    score_differential: int,
    possession_number: int,
    opponent_result,
    is_playoffs: bool,
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
) -> dict:
    """
    Run the full decision analysis using the analytical expected-value approach.

    Uses 4 trained ML submodels to compute expected win probability for
    each of the three 4th-down options (go/punt/fg).
    """
    # Load the WP model
    wp_model = WinProbabilityModel()

    # Derive score diff based on possession and opponent result
    if possession_number == 2 and opponent_result:
        if opponent_result == "td":
            score_differential = -7
        elif opponent_result == "fg":
            score_differential = -3
        else:
            score_differential = 0

    # Map possession_number to OT possession number (0-indexed for model)
    ot_poss_num = max(0, possession_number - 1)

    # Build current game state
    # OT with ~8 minutes assumed (480 seconds)
    home_val = 0.5 if is_home is None else (1.0 if is_home else 0.0)
    current_state = {
        "score_differential": float(score_differential),
        "quarter": 5,
        "seconds_remaining": 480.0,
        "yardline_100": float(yardline_100),
        "down": 4,
        "ydstogo": float(yards_to_go),
        "offense_timeouts": int(offense_timeouts),
        "defense_timeouts": int(defense_timeouts),
        "is_overtime": 1,
        "overtime_possession_number": ot_poss_num,
        "home": home_val,
        "posteam_spread": float(posteam_spread),
        "guaranteed_possession": 1.0,  # 2025 rules: both teams get a possession
    }

    # Clock estimate after play (~8 seconds per play)
    clock_after = max(10.0, current_state["seconds_remaining"] - 8.0)

    # === SUBMODEL 1: 4th-Down Conversion Probability ===
    conversion_prob = get_conversion_probability(
        yards_to_go=yards_to_go,
        yardline_100=yardline_100,
        score_differential=score_differential,
        game_seconds_remaining=current_state["seconds_remaining"],
        qtr=5,
        wp=0.5,
        temp=temp,
        shotgun=shotgun,
        no_huddle=no_huddle,
        off_epa=off_epa,
        def_epa=def_epa,
        off_success_rate=off_success_rate,
        off_ppg=off_ppg,
        def_success_rate=def_success_rate,
        def_ppg=def_ppg,
    )

    # === SUBMODEL 2: Field Goal Make Probability ===
    fg_distance = yardline_100 + 17
    fg_available = fg_distance <= 66
    fg_prob = fg_make_probability(
        yardline_100=yardline_100,
        is_dome=is_dome,
        wind=wind,
        temp=temp,
        wind_gust=wind_gust,
        is_precipitation=is_precipitation,
        fg_make_rate_roll6=fg_make_rate_roll6,
        surface_is_grass=surface_is_grass,
        altitude_ft=altitude_ft,
        game_seconds_remaining=current_state["seconds_remaining"],
        score_differential=score_differential,
        is_overtime=True,
    ) if fg_available else 0.0

    # === SUBMODEL 3: Punt Outcome ===
    punt_opp_start = predict_punt_opponent_start(
        yardline_100=yardline_100,
        punt_distance_roll6=punt_distance_roll6,
        inside_twenty_rate_roll6=inside_twenty_rate_roll6,
    )
    punt_net = expected_punt_net_yards(
        yardline_100, punt_distance_roll6, inside_twenty_rate_roll6,
    )

    # === BUILD POST-PLAY STATES ===

    # GO: success — same team keeps ball, new first down
    success_state = dict(current_state)
    new_yl = max(1, yardline_100 - yards_to_go)
    success_state.update({
        "yardline_100": float(new_yl),
        "down": 1,
        "ydstogo": min(10.0, float(new_yl)),
        "seconds_remaining": clock_after,
    })

    # GO: failure — opponent takes over at the spot
    failure_state = _flip_possession(current_state)
    failure_state.update({
        "yardline_100": max(1.0, 100.0 - yardline_100),
        "down": 1,
        "ydstogo": 10.0,
        "seconds_remaining": clock_after,
    })

    # FG: make — opponent receives kickoff, score adjusted
    kickoff_yl = _kickoff_touchback_yardline()
    fg_make_state = _flip_possession(current_state)
    fg_make_state["score_differential"] = -(score_differential + 3)
    fg_make_state.update({
        "yardline_100": kickoff_yl,
        "down": 1,
        "ydstogo": 10.0,
        "seconds_remaining": clock_after,
    })

    # FG: miss — opponent takes over at line of scrimmage (min own 20)
    fg_miss_state = _flip_possession(current_state)
    fg_miss_state.update({
        "yardline_100": max(80.0, 100.0 - yardline_100),
        "down": 1,
        "ydstogo": 10.0,
        "seconds_remaining": clock_after,
    })

    # PUNT: opponent receives at predicted landing spot
    punt_state = _flip_possession(current_state)
    punt_state.update({
        "yardline_100": float(punt_opp_start),
        "down": 1,
        "ydstogo": 10.0,
        "seconds_remaining": clock_after,
    })

    # === SUBMODEL 4: Win Probability — Expected Value Calculation ===
    # wp_go   = p_conv * wp(success) + (1-p_conv) * [1 - wp(failure)]
    # wp_fg   = p_make * [1 - wp(make)] + (1-p_make) * [1 - wp(miss)]
    # wp_punt = 1 - wp(punt)

    wp_success = wp_model.simulate_state(success_state)
    wp_failure = wp_model.simulate_state(failure_state)
    wp_fg_make = wp_model.simulate_state(fg_make_state)
    wp_fg_miss = wp_model.simulate_state(fg_miss_state)
    wp_punt_opp = wp_model.simulate_state(punt_state)

    go_wp = conversion_prob * wp_success + (1.0 - conversion_prob) * (1.0 - wp_failure)
    punt_wp = 1.0 - wp_punt_opp

    if fg_available:
        fg_wp = fg_prob * (1.0 - wp_fg_make) + (1.0 - fg_prob) * (1.0 - wp_fg_miss)
    else:
        fg_wp = -1.0

    # === RECOMMENDATION ===
    options = {"go": go_wp, "punt": punt_wp}
    if fg_available:
        options["fg"] = fg_wp
    sorted_opts = sorted(options.items(), key=lambda x: x[1], reverse=True)
    best = sorted_opts[0]
    second = sorted_opts[1]
    margin = (best[1] - second[1]) * 100

    if margin >= 3:
        strength = "Strong"
    elif margin >= 1:
        strength = "Moderate"
    else:
        strength = "Marginal"

    # Display details
    punt_landing_display = int(round(punt_opp_start))
    if punt_landing_display <= 50:
        punt_landing_label = f"opponent's {punt_landing_display}"
    else:
        punt_landing_label = f"their own {100 - punt_landing_display}"

    return {
        "win_probabilities": {
            "go": round(go_wp * 100, 1),
            "punt": round(punt_wp * 100, 1),
            "fg": round(fg_wp * 100, 1) if fg_available else None,
        },
        "fg_available": fg_available,
        "recommendation": best[0],
        "recommendation_strength": strength,
        "margin": round(margin, 1),
        "details": {
            "conversion_probability": round(conversion_prob * 100, 1),
            "fg_make_probability": round(fg_prob * 100, 1) if fg_available else None,
            "fg_distance": fg_distance,
            "expected_punt_net": round(punt_net, 1),
            "punt_landing_yardline": punt_landing_label,
        },
        "submodel_details": {
            "wp_if_convert": round(wp_success * 100, 1),
            "wp_if_fail": round((1.0 - wp_failure) * 100, 1),
            "wp_if_fg_make": round((1.0 - wp_fg_make) * 100, 1),
            "wp_if_fg_miss": round((1.0 - wp_fg_miss) * 100, 1),
            "wp_if_punt": round((1.0 - wp_punt_opp) * 100, 1),
        },
        "inputs": {
            "yardline_100": yardline_100,
            "yards_to_go": yards_to_go,
            "score_differential": score_differential,
            "possession_number": possession_number,
            "is_playoffs": is_playoffs,
        },
    }
