"""
Monte Carlo simulation engine for NFL OT 4th-down decisions.
Combines conversion, FG, and punt submodels to estimate win probability
for each of the three decisions: GO FOR IT, PUNT, FIELD GOAL.
"""

import numpy as np
from models import (
    get_conversion_probability,
    fg_make_probability,
    punt_landing_yardline,
    expected_punt_net_yards,
)

NUM_SIMULATIONS = 10_000

# Drive simulation constants
AVG_YARDS_PER_PLAY = 5.5
PASS_RATE = 0.55
TD_RATE_PER_PLAY = 0.04
RED_ZONE_TD_RATE = 0.08
TURNOVER_RATE_PER_PLAY = 0.03
INCOMPLETE_RATE = 0.25  # of pass plays
KICKOFF_START = 75  # opponent starts at their 25 (yardline_100 = 75)


def _simulate_play(yardline_100, down, distance, rng):
    """
    Simulate a single play. Returns (new_yardline_100, new_down, new_distance, result).
    result: 'play', 'td', 'turnover', 'safety'
    """
    # Turnover check
    if rng.random() < TURNOVER_RATE_PER_PLAY:
        return yardline_100, down, distance, "turnover"

    # TD check (higher in red zone)
    td_rate = RED_ZONE_TD_RATE if yardline_100 <= 20 else TD_RATE_PER_PLAY
    if rng.random() < td_rate:
        return 0, 0, 0, "td"

    # Determine yards gained
    is_pass = rng.random() < PASS_RATE
    if is_pass and rng.random() < INCOMPLETE_RATE:
        yards = 0
    else:
        yards = max(-5, int(rng.normal(AVG_YARDS_PER_PLAY, 4)))

    new_yardline = yardline_100 - yards
    if new_yardline <= 0:
        return 0, 0, 0, "td"
    if new_yardline >= 100:
        return 100, down, distance, "safety"

    new_distance = distance - yards
    if new_distance <= 0:
        # First down
        return new_yardline, 1, min(10, new_yardline), "play"
    else:
        return new_yardline, down + 1, new_distance, "play"


def _ai_coach_4th_down(yardline_100, distance):
    """Simple AI coach for 4th down decisions during simulation."""
    fg_distance = yardline_100 + 17
    if yardline_100 <= 3 and distance <= 3:
        return "go"
    if fg_distance <= 52 and yardline_100 > 3:
        return "fg"
    if yardline_100 >= 60:
        return "punt"
    if distance <= 2 and yardline_100 <= 10:
        return "go"
    return "punt"


def _simulate_drive(yardline_100, rng):
    """
    Simulate an offensive drive from a given field position.
    Returns (result, points):
        result: 'td', 'fg', 'punt', 'turnover', 'turnover_on_downs', 'safety'
        points: 7 for TD, 3 for FG, 0 otherwise
        opp_yardline: where the opponent gets the ball (if applicable)
    """
    down = 1
    distance = min(10, yardline_100)
    max_plays = 30

    for _ in range(max_plays):
        if down == 4:
            decision = _ai_coach_4th_down(yardline_100, distance)
            if decision == "fg":
                p_make = fg_make_probability(yardline_100)
                if rng.random() < p_make:
                    opp_start = KICKOFF_START
                    return "fg", 3, opp_start
                else:
                    opp_start = max(80, 100 - yardline_100)
                    return "missed_fg", 0, opp_start
            elif decision == "punt":
                opp_start = punt_landing_yardline(yardline_100, rng)
                return "punt", 0, opp_start
            else:
                # Go for it
                conv_prob = get_conversion_probability(distance)
                if rng.random() < conv_prob:
                    down = 1
                    distance = min(10, yardline_100)
                    continue
                else:
                    return "turnover_on_downs", 0, 100 - yardline_100

        yardline_100, down, distance, result = _simulate_play(
            yardline_100, down, distance, rng
        )

        if result == "td":
            return "td", 7, KICKOFF_START
        elif result == "turnover":
            opp_start = 100 - yardline_100
            opp_start = max(1, min(99, opp_start))
            return "turnover", 0, opp_start
        elif result == "safety":
            return "safety", -2, KICKOFF_START

    # If we hit max plays, treat as punt
    return "punt", 0, 80


def _simulate_ot_from_state(
    team_score_diff,
    possession_num,
    ball_yardline_100,
    is_playoffs,
    rng,
    first_poss_result=None,
):
    """
    Simulate the rest of OT starting from a given state.
    team_score_diff: our score minus opponent's at the START of this possession.
    possession_num: which possession we're on (1, 2, 3+)
    ball_yardline_100: where the offense has the ball
    first_poss_result: points scored by first possession team (for poss 2 tracking)

    Returns: 1 for win, 0 for loss, 0.5 for tie.
    """
    # Current team is on offense
    drive_result, drive_points, opp_start = _simulate_drive(ball_yardline_100, rng)

    new_diff = team_score_diff + drive_points

    if possession_num == 1:
        # After first possession, opponent gets ball regardless of what happened
        # Opponent's perspective: their score_diff is -new_diff
        # Simulate opponent drive
        return _simulate_ot_opponent_response(
            new_diff, drive_points, opp_start, 2, is_playoffs, rng
        )

    elif possession_num == 2:
        # We're responding to first possession team's result
        if new_diff > 0:
            return 1.0  # We're ahead, we win
        elif new_diff < 0:
            return 0.0  # We're behind, we lose
        else:
            # Tied — go to sudden death (possession 3+)
            # Opponent gets ball next in sudden death
            return _simulate_sudden_death(0, opp_start, is_playoffs, rng, our_turn=False)

    else:
        # Sudden death (possession 3+)
        if drive_points > 0:
            return 1.0  # Any score wins
        elif drive_points < 0:
            # Safety scored against us
            return 0.0
        else:
            # No score, opponent gets ball in sudden death
            return _simulate_sudden_death(0, opp_start, is_playoffs, rng, our_turn=False)


def _simulate_ot_opponent_response(score_diff, first_poss_points, opp_yardline, poss_num, is_playoffs, rng):
    """
    Simulate the opponent's response drive and beyond.
    score_diff: our advantage after our drive.
    """
    opp_result, opp_points, next_start = _simulate_drive(opp_yardline, rng)

    opp_new_diff = -score_diff + opp_points  # from opponent's perspective
    our_new_diff = score_diff - opp_points   # from our perspective

    if poss_num == 2:
        # End of guaranteed possessions
        if our_new_diff > 0:
            return 1.0
        elif our_new_diff < 0:
            return 0.0
        else:
            # Tied after both possessions
            if not is_playoffs:
                # Regular season: check if we're past guaranteed possessions
                # If both had a possession and it's tied, more sudden death
                # But regular season can end in tie after OT period
                # Simplified: give each team 2 more sudden death possessions, then tie
                return _simulate_sudden_death(
                    0, next_start, is_playoffs, rng, our_turn=True, max_possessions=6
                )
            else:
                return _simulate_sudden_death(
                    0, next_start, is_playoffs, rng, our_turn=True
                )
    else:
        # Shouldn't get here normally
        if our_new_diff > 0:
            return 1.0
        elif our_new_diff < 0:
            return 0.0
        else:
            return _simulate_sudden_death(0, next_start, is_playoffs, rng, our_turn=True)


def _simulate_sudden_death(score_diff, ball_yardline, is_playoffs, rng, our_turn=True, max_possessions=20):
    """
    Simulate sudden death OT.
    Any score wins. In regular season, game can end in tie.
    """
    for i in range(max_possessions):
        drive_result, drive_points, opp_start = _simulate_drive(ball_yardline, rng)

        if drive_points > 0:
            return 1.0 if our_turn else 0.0
        if drive_points < 0:
            # Safety
            return 0.0 if our_turn else 1.0

        ball_yardline = opp_start
        our_turn = not our_turn

    # If we exhaust possessions
    if not is_playoffs:
        return 0.5  # Tie in regular season
    # Playoffs: keep going (but we cap at max_possessions for perf)
    return 0.5


def _simulate_go_for_it(yardline_100, yards_to_go, score_diff, possession_num,
                         opponent_result_points, is_playoffs, rng):
    """Simulate one trial of going for it on 4th down."""
    conv_prob = get_conversion_probability(yards_to_go)

    if rng.random() < conv_prob:
        # Converted! Continue drive from new position with 1st down
        new_yardline = yardline_100  # Stay at same spot (already past the marker)
        # Simulate rest of our drive from here
        drive_result, drive_points, opp_start = _simulate_drive(new_yardline, rng)
        our_total_diff = score_diff + drive_points
    else:
        # Failed! Opponent gets ball at the spot
        opp_start = max(1, min(99, 100 - yardline_100))
        our_total_diff = score_diff
        drive_points = 0

    # Now resolve the OT from here
    if possession_num == 1:
        return _simulate_ot_opponent_response(
            score_diff + drive_points, drive_points, opp_start if drive_points == 0 else KICKOFF_START,
            2, is_playoffs, rng
        )
    elif possession_num == 2:
        new_diff = score_diff + drive_points
        if new_diff > 0:
            return 1.0
        elif new_diff < 0:
            return 0.0
        else:
            opp_ball = opp_start if drive_points == 0 else KICKOFF_START
            return _simulate_sudden_death(0, opp_ball, is_playoffs, rng, our_turn=False)
    else:
        # Sudden death
        if drive_points > 0:
            return 1.0
        elif drive_points < 0:
            return 0.0
        else:
            return _simulate_sudden_death(0, opp_start, is_playoffs, rng, our_turn=False)


def _simulate_punt(yardline_100, score_diff, possession_num,
                    opponent_result_points, is_playoffs, rng):
    """Simulate one trial of punting on 4th down."""
    opp_start = punt_landing_yardline(yardline_100, rng)

    if possession_num == 1:
        # Opponent gets ball, we scored 0 on this drive
        return _simulate_ot_opponent_response(score_diff, 0, opp_start, 2, is_playoffs, rng)
    elif possession_num == 2:
        # We punted on 2nd possession
        if score_diff > 0:
            # We're already ahead (shouldn't normally happen but handle it)
            return 1.0
        elif score_diff < 0:
            return 0.0
        else:
            return _simulate_sudden_death(0, opp_start, is_playoffs, rng, our_turn=False)
    else:
        # Sudden death — punting means opponent gets ball
        return _simulate_sudden_death(0, opp_start, is_playoffs, rng, our_turn=False)


def _simulate_field_goal(yardline_100, score_diff, possession_num,
                          opponent_result_points, is_playoffs, rng):
    """Simulate one trial of kicking a FG on 4th down."""
    p_make = fg_make_probability(yardline_100)

    if rng.random() < p_make:
        # FG is good
        new_diff = score_diff + 3
        opp_start = KICKOFF_START

        if possession_num == 1:
            return _simulate_ot_opponent_response(new_diff, 3, opp_start, 2, is_playoffs, rng)
        elif possession_num == 2:
            if new_diff > 0:
                return 1.0
            elif new_diff < 0:
                return 0.0
            else:
                return _simulate_sudden_death(0, opp_start, is_playoffs, rng, our_turn=False)
        else:
            # Sudden death, FG wins
            return 1.0
    else:
        # Missed FG — opponent gets ball
        opp_start = max(1, min(99, max(80, 100 - yardline_100)))

        if possession_num == 1:
            return _simulate_ot_opponent_response(score_diff, 0, opp_start, 2, is_playoffs, rng)
        elif possession_num == 2:
            if score_diff > 0:
                return 1.0
            elif score_diff < 0:
                return 0.0
            else:
                return _simulate_sudden_death(0, opp_start, is_playoffs, rng, our_turn=False)
        else:
            return _simulate_sudden_death(0, opp_start, is_playoffs, rng, our_turn=False)


def analyze(
    yardline_100: int,
    yards_to_go: int,
    score_differential: int,
    possession_number: int,
    opponent_result,
    is_playoffs: bool,
) -> dict:
    """
    Run the full decision analysis.

    Parameters
    ----------
    yardline_100 : int
        Yards from opponent end zone (1-99). E.g. own 20 = 80.
    yards_to_go : int
        Yards needed for first down (1-15).
    score_differential : int
        Your score minus opponent's score.
    possession_number : int
        1, 2, or 3 (3 = sudden death).
    opponent_result : str or None
        For 2nd possession: 'td', 'fg', or 'no_score'. None otherwise.
    is_playoffs : bool
        True for playoff rules, False for regular season.

    Returns
    -------
    dict with win probabilities and recommendation details.
    """
    rng = np.random.default_rng()

    # Derive score_diff based on possession and opponent result
    if possession_number == 2 and opponent_result:
        if opponent_result == "td":
            score_differential = -7
        elif opponent_result == "fg":
            score_differential = -3
        else:
            score_differential = 0

    # Run simulations
    go_wins = 0.0
    punt_wins = 0.0
    fg_wins = 0.0

    for _ in range(NUM_SIMULATIONS):
        go_wins += _simulate_go_for_it(
            yardline_100, yards_to_go, score_differential,
            possession_number, opponent_result, is_playoffs, rng
        )

    for _ in range(NUM_SIMULATIONS):
        punt_wins += _simulate_punt(
            yardline_100, score_differential,
            possession_number, opponent_result, is_playoffs, rng
        )

    # Only simulate FG if kick distance is realistic (NFL record: 66 yards)
    fg_distance_check = yardline_100 + 17
    if fg_distance_check <= 66:
        for _ in range(NUM_SIMULATIONS):
            fg_wins += _simulate_field_goal(
                yardline_100, score_differential,
                possession_number, opponent_result, is_playoffs, rng
            )
        fg_wp = fg_wins / NUM_SIMULATIONS
    else:
        fg_wp = -1.0  # Mark as unavailable

    go_wp = go_wins / NUM_SIMULATIONS
    punt_wp = punt_wins / NUM_SIMULATIONS

    # Determine recommendation (exclude FG if out of range)
    fg_available = fg_wp >= 0
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

    # Calculate display details
    conversion_prob = get_conversion_probability(yards_to_go)
    fg_distance = yardline_100 + 17
    fg_prob = fg_make_probability(yardline_100) if fg_available else 0.0
    punt_net = expected_punt_net_yards(yardline_100)
    punt_landing = yardline_100 - punt_net
    if punt_landing < 0:
        punt_landing_display = 20  # touchback, opponent's 20
    else:
        punt_landing_display = 100 - (yardline_100 - punt_net)
        punt_landing_display = max(1, min(99, int(punt_landing_display)))

    # Convert punt landing to readable format
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
        "inputs": {
            "yardline_100": yardline_100,
            "yards_to_go": yards_to_go,
            "score_differential": score_differential,
            "possession_number": possession_number,
            "is_playoffs": is_playoffs,
        },
    }
