"""
Submodel inference functions with hardcoded parameters.
No external model files needed — all key coefficients are embedded.

4th-down conversion uses empirical blending (v2) matching the updated
submodel from feature/4th-down-conversion branch.
"""

import math

# =============================================================================
# 4th-Down Conversion — Empirical Base Rates + EPA Blending (v2)
# Updated to match feature/4th-down-conversion empirical blending approach.
# =============================================================================
_EMPIRICAL_BASE_RATE = {
    1: 0.690,
    2: 0.620,
    3: 0.550,
    4: 0.500,
    5: 0.460,
    6: 0.430,
    7: 0.400,
    8: 0.370,
    9: 0.330,
    10: 0.280,
}

# EPA matchup adjustment: ~4 percentage points per EPA unit
_EPA_ADJUSTMENT = 0.04


def get_conversion_probability(
    yards_to_go: int,
    off_epa: float = 0.0,
    def_epa: float = 0.0,
) -> float:
    """
    Return the probability of converting a 4th down given yards to go,
    adjusted for offensive/defensive quality via EPA matchup.

    Uses empirical base rates blended with EPA matchup adjustment,
    matching the updated submodel's approach.
    """
    if yards_to_go <= 0:
        return 0.90
    ytg = max(1, min(10, yards_to_go))
    if yards_to_go > 10:
        # Extrapolate: drop ~3.5% per additional yard beyond 10
        base = _EMPIRICAL_BASE_RATE[10]
        extra_yards = yards_to_go - 10
        base = max(0.05, base - 0.035 * extra_yards)
    else:
        base = _EMPIRICAL_BASE_RATE[ytg]

    # Adjust for team quality matchup
    epa_matchup = off_epa - def_epa
    adjusted = base + epa_matchup * _EPA_ADJUSTMENT
    return max(0.01, min(0.99, adjusted))


# =============================================================================
# Field Goal Make Probability — Logistic Regression
# Coefficients from trained model in model_metadata.json
# =============================================================================
FG_INTERCEPT = 5.8947
FG_DISTANCE_COEFF = -0.1038


def fg_make_probability(yardline_100: int) -> float:
    """
    Predict FG make probability from field position.
    FG distance = yardline_100 + 17 (7-yard snap + 10-yard end zone).
    """
    distance = yardline_100 + 17
    if distance > 70:
        return 0.0
    if distance < 18:
        return 0.99
    logit = FG_INTERCEPT + FG_DISTANCE_COEFF * distance
    return 1.0 / (1.0 + math.exp(-logit))


# =============================================================================
# Punt Net Yards by Field Position Zone
# =============================================================================
PUNT_NET_YARDS = {
    "deep_own": 40,      # yardline_100 71-99
    "midfield": 38,      # yardline_100 41-70
    "opponent": 30,      # yardline_100 21-40
}
PUNT_STD = 10  # standard deviation for punt distance randomness


def get_punt_zone(yardline_100: int) -> str:
    if yardline_100 >= 71:
        return "deep_own"
    elif yardline_100 >= 41:
        return "midfield"
    else:
        return "opponent"


def expected_punt_net_yards(yardline_100: int) -> float:
    """Return the expected net punt yards for a given field position."""
    zone = get_punt_zone(yardline_100)
    return PUNT_NET_YARDS[zone]


def punt_landing_yardline(yardline_100: int, rng=None) -> int:
    """
    Simulate where a punt lands. Returns opponent's starting yardline_100.
    A touchback gives the opponent the ball at their 20 (yardline_100 = 80).
    """
    if rng is None:
        import numpy as np
        rng = np.random.default_rng()

    zone = get_punt_zone(yardline_100)
    net = PUNT_NET_YARDS[zone]
    actual_net = net + rng.normal(0, PUNT_STD)
    actual_net = max(10, actual_net)  # minimum punt distance

    raw_landing = yardline_100 - actual_net
    # Convert to opponent's perspective: opponent_yardline_100 = 100 - raw_landing
    opponent_start = 100 - raw_landing

    # Touchback
    if opponent_start > 80:
        opponent_start = 80
    # Can't start behind own end zone
    opponent_start = max(1, min(99, int(round(opponent_start))))
    return opponent_start
