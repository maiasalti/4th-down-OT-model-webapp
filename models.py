"""
Submodel inference functions using trained XGBoost models.

Loads pre-trained model artifacts from app/models/:
  - fourth_down_conversion.pkl  (XGBoost + isotonic calibration, 15 features)
  - fg_prob_model.pkl           (XGBoost + isotonic calibration, 15 features)
  - punt_outcome_xgb.json       (XGBoost regressor, 3 features)
  - win_probability_model.pkl   (XGBoost + isotonic calibration, 33 features)
"""

import logging
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_APP_DIR = Path(__file__).resolve().parent
_MODELS_DIR = _APP_DIR / "models"

# ---------------------------------------------------------------------------
# League-average fallbacks
# ---------------------------------------------------------------------------
_LEAGUE_AVG_CONVERSION = {
    "epa_per_game_roll15": 0.00,
    "success_rate_roll15": 0.42,
    "points_per_game_roll15": 23.0,
    "def_epa_per_game_roll15": 0.00,
    "def_success_rate_roll15": 0.42,
    "def_points_per_game_roll15": 23.0,
}

_LEAGUE_AVG_PUNT = {
    "punt_distance_roll6": 45.9,
    "inside_twenty_rate_roll6": 0.44,
}

_LEAGUE_AVG_FG_BY_BUCKET = {
    "0-30": 0.94, "31-35": 0.92, "36-40": 0.88, "41-45": 0.83,
    "46-50": 0.77, "51-55": 0.67, "56-58": 0.57, "59-61": 0.46,
    "62-64": 0.34, "65+": 0.22,
}

# Empirical base rates and model blending weights for conversion model
_EMPIRICAL_BASE_RATE = {
    1: 0.690, 2: 0.620, 3: 0.550, 4: 0.500, 5: 0.460,
    6: 0.430, 7: 0.400, 8: 0.370, 9: 0.330, 10: 0.280,
}
_MODEL_WEIGHT = {
    1: 0.85, 2: 0.80, 3: 0.55, 4: 0.50, 5: 0.50,
    6: 0.45, 7: 0.40, 8: 0.25, 9: 0.20, 10: 0.15,
}
_EPA_BLEND_FACTOR = 0.04


# ---------------------------------------------------------------------------
# Lazy model loading (load once, cache in module)
# ---------------------------------------------------------------------------
_cached_models = {}


def _load_conversion_model():
    if "conversion" not in _cached_models:
        path = _MODELS_DIR / "fourth_down_conversion.pkl"
        artifact = joblib.load(path)
        _cached_models["conversion"] = artifact
        logger.info("Loaded conversion model from %s", path)
    return _cached_models["conversion"]


def _load_fg_model():
    if "fg" not in _cached_models:
        path = _MODELS_DIR / "fg_prob_model.pkl"
        artifact = joblib.load(path)
        _cached_models["fg"] = artifact
        logger.info("Loaded FG model from %s", path)
    return _cached_models["fg"]


def _load_punt_model():
    if "punt" not in _cached_models:
        path = _MODELS_DIR / "punt_outcome_xgb.json"
        model = xgb.XGBRegressor()
        model.load_model(str(path))
        _cached_models["punt"] = model
        logger.info("Loaded punt model from %s", path)
    return _cached_models["punt"]


def _load_wp_model():
    if "wp" not in _cached_models:
        path = _MODELS_DIR / "win_probability_model.pkl"
        artifact = joblib.load(path)
        _cached_models["wp"] = artifact
        logger.info("Loaded WP model from %s", path)
    return _cached_models["wp"]


# ---------------------------------------------------------------------------
# 4th-Down Conversion Probability (XGBoost + empirical blending)
# ---------------------------------------------------------------------------
def get_conversion_probability(
    yards_to_go: int,
    yardline_100: float = 50.0,
    score_differential: float = 0.0,
    game_seconds_remaining: float = 300.0,
    qtr: int = 5,
    wp: float = 0.5,
    temp: float = 65.0,
    shotgun: int = 1,
    no_huddle: int = 0,
    off_epa: float = 0.0,
    def_epa: float = 0.0,
    off_success_rate: float = 0.42,
    off_ppg: float = 23.0,
    def_success_rate: float = 0.42,
    def_ppg: float = 23.0,
) -> float:
    """Predict 4th-down conversion probability using trained XGBoost model
    with empirical blending, matching the feature/4th-down-conversion branch."""

    if yards_to_go <= 0:
        return 0.90

    artifact = _load_conversion_model()
    model = artifact["model"]
    feature_cols = artifact["features"]

    # Build feature vector
    vals = {
        "ydstogo": float(yards_to_go),
        "qtr": float(qtr),
        "yardline_100": float(yardline_100),
        "score_differential": float(score_differential),
        "game_seconds_remaining": float(game_seconds_remaining),
        "wp": float(wp),
        "temp": float(temp),
        "shotgun": float(shotgun),
        "no_huddle": float(no_huddle),
        "epa_per_game_roll15": float(off_epa) if off_epa != 0.0 else _LEAGUE_AVG_CONVERSION["epa_per_game_roll15"],
        "success_rate_roll15": float(off_success_rate),
        "points_per_game_roll15": float(off_ppg),
        "def_epa_per_game_roll15": float(def_epa) if def_epa != 0.0 else _LEAGUE_AVG_CONVERSION["def_epa_per_game_roll15"],
        "def_success_rate_roll15": float(def_success_rate),
        "def_points_per_game_roll15": float(def_ppg),
    }

    row = pd.DataFrame([{f: vals.get(f, 0.0) for f in feature_cols}])
    raw_prob = float(model.predict_proba(row)[0][1])

    # Empirical blending (matching source repo approach)
    ytg = max(1, min(10, int(round(yards_to_go))))
    if yards_to_go > 10:
        # Extrapolate beyond 10 yards
        base = _EMPIRICAL_BASE_RATE[10]
        base = max(0.05, base - 0.035 * (yards_to_go - 10))
        w = 0.15  # minimal model weight for long distances
    else:
        base = _EMPIRICAL_BASE_RATE[ytg]
        w = _MODEL_WEIGHT[ytg]

    epa_matchup = off_epa - def_epa
    adj_base = float(np.clip(base + epa_matchup * _EPA_BLEND_FACTOR, 0.05, 0.95))
    blended = float(np.clip(w * raw_prob + (1 - w) * adj_base, 0.01, 0.99))

    return blended


# ---------------------------------------------------------------------------
# Field Goal Make Probability (XGBoost + isotonic calibration)
# ---------------------------------------------------------------------------
def _distance_to_bucket(yards: float) -> str:
    buckets = [
        (0, 30, "0-30"), (31, 35, "31-35"), (36, 40, "36-40"),
        (41, 45, "41-45"), (46, 50, "46-50"), (51, 55, "51-55"),
        (56, 58, "56-58"), (59, 61, "59-61"), (62, 64, "62-64"), (65, 999, "65+"),
    ]
    for lo, hi, label in buckets:
        if lo <= yards <= hi:
            return label
    return "65+"


def fg_make_probability(
    yardline_100: int,
    is_dome: bool = False,
    wind: float = 8.0,
    temp: float = 65.0,
    wind_gust: float = None,
    is_precipitation: bool = False,
    fg_make_rate_roll6: float = None,
    surface_is_grass: bool = True,
    altitude_ft: float = 0.0,
    game_seconds_remaining: float = 300.0,
    score_differential: float = 0.0,
    is_overtime: bool = True,
) -> float:
    """Predict FG make probability using trained XGBoost model with 15 features."""
    distance = yardline_100 + 17
    if distance > 70:
        return 0.0
    if distance < 18:
        return 0.99

    artifact = _load_fg_model()
    model = artifact["model"]

    temp_adj = 72.0 if is_dome else float(temp)
    wind_gust_val = 0.0 if is_dome else float(wind_gust if wind_gust is not None else wind)
    wind_gust_x_distance = wind_gust_val * float(distance)
    temp_x_distance = temp_adj * float(distance)

    bucket = _distance_to_bucket(float(distance))
    if fg_make_rate_roll6 is None:
        fg_make_rate_roll6 = _LEAGUE_AVG_FG_BY_BUCKET.get(bucket, 0.80)

    # Feature order must match FEATURE_COLS exactly (15 features)
    X = np.array([[
        float(distance),
        int(is_dome),
        wind_gust_val,
        wind_gust_x_distance,
        temp_adj,
        temp_x_distance,
        int(is_precipitation and not is_dome),
        float(fg_make_rate_roll6),
        float(fg_make_rate_roll6),  # career rate fallback = rolling rate
        30,  # career attempts fallback
        int(surface_is_grass),
        float(altitude_ft),
        float(game_seconds_remaining),
        float(score_differential),
        int(is_overtime),
    ]])

    return float(model.predict_proba(X)[0, 1])


# ---------------------------------------------------------------------------
# Punt Outcome (XGBoost regressor)
# ---------------------------------------------------------------------------
def predict_punt_opponent_start(
    yardline_100: float,
    punt_distance_roll6: float = None,
    inside_twenty_rate_roll6: float = None,
) -> float:
    """Predict opponent's starting yardline_100 after a punt using trained XGBoost."""
    model = _load_punt_model()

    if punt_distance_roll6 is None:
        punt_distance_roll6 = _LEAGUE_AVG_PUNT["punt_distance_roll6"]
    if inside_twenty_rate_roll6 is None:
        inside_twenty_rate_roll6 = _LEAGUE_AVG_PUNT["inside_twenty_rate_roll6"]

    X = pd.DataFrame([{
        "yardline_100": yardline_100,
        "punt_distance_roll6": punt_distance_roll6,
        "inside_twenty_rate_roll6": inside_twenty_rate_roll6,
    }])

    pred = float(model.predict(X)[0])
    # Clamp to valid yardline range (1-99)
    return float(np.clip(pred, 1, 99))


def expected_punt_net_yards(yardline_100: int, punt_distance_roll6=None, inside_twenty_rate_roll6=None) -> float:
    """Return the expected net punt yards using the trained model."""
    opp_start = predict_punt_opponent_start(yardline_100, punt_distance_roll6, inside_twenty_rate_roll6)
    net = yardline_100 - (100 - opp_start)
    return max(0, net)


# ---------------------------------------------------------------------------
# Win Probability Model (XGBoost + isotonic calibration)
# ---------------------------------------------------------------------------
class WinProbabilityModel:
    """Wrapper around the trained WP model for inference."""

    def __init__(self):
        artifact = _load_wp_model()
        self._base_model = artifact["base_model"]
        self._calibrator = artifact["calibrator"]
        self._feature_cols = artifact["feature_cols"]

    def _engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Derive engineered features matching the training pipeline."""
        out = df.copy()
        sec = out["seconds_remaining"].astype(float)
        sd = out["score_differential"].astype(float)
        ot = out["is_overtime"].astype(int)
        pn = out["overtime_possession_number"].astype(int)

        elapsed = ((3600.0 - sec) / 3600.0).clip(0.0, 1.0)
        out["elapsed_share"] = elapsed
        out["half_seconds_remaining"] = np.where(sec > 1800.0, sec - 1800.0, sec)
        out["diff_time_ratio"] = sd * np.exp(4.0 * elapsed)

        spread = out.get("posteam_spread", pd.Series(0.0, index=out.index))
        if "posteam_spread" in out.columns:
            spread = out["posteam_spread"].fillna(0.0)
        out["spread_time"] = spread * np.exp(-4.0 * elapsed)

        out["seconds_remaining_sqrt"] = np.sqrt(sec)
        out["seconds_remaining_log1p"] = np.log1p(sec)
        out["score_x_time"] = sd * sec / 3600.0
        out["urgency"] = np.abs(sd) / (sec + 1.0)
        out["clock_leverage"] = 1.0 / (np.abs(sd) + 1.0) / (sec + 60.0)
        out["abs_score_diff"] = np.abs(sd)
        out["score_differential_sq"] = sd ** 2
        out["timeout_diff"] = out["offense_timeouts"] - out["defense_timeouts"]
        out["total_timeouts"] = out["offense_timeouts"] + out["defense_timeouts"]
        out["ydstogo_log1p"] = np.log1p(out["ydstogo"])
        out["short_yardage"] = (out["ydstogo"] <= 2).astype(int)
        out["fg_range"] = (out["yardline_100"] <= 35).astype(int)
        out["red_zone"] = (out["yardline_100"] <= 20).astype(int)
        out["scoring_position"] = (out["yardline_100"] <= 10).astype(int)
        out["ot_first_poss"] = (ot & (pn == 0)).astype(int)
        out["ot_second_poss"] = (ot & (pn == 1)).astype(int)
        out["ot_sudden_death"] = (ot & (pn >= 2)).astype(int)
        out["ot_must_score"] = ((ot == 1) & (pn >= 1) & (sd < 0)).astype(int)
        out["ot_leading_first_poss"] = ((ot == 1) & (pn == 0) & (sd > 0)).astype(int)

        return out

    def predict_proba(self, state_dict: dict) -> float:
        """Return P(offensive team wins | current game state)."""
        # Handle OT states via OT transformer
        if state_dict.get("is_overtime", 0):
            state_dict = _ot_transform(state_dict)

        row = {k: state_dict.get(k, 0) for k in [
            "score_differential", "quarter", "seconds_remaining", "yardline_100",
            "down", "ydstogo", "offense_timeouts", "defense_timeouts",
            "is_overtime", "overtime_possession_number",
        ]}
        for k in ["home", "posteam_spread", "guaranteed_possession"]:
            row[k] = float(state_dict.get(k, 0.0))

        df_row = pd.DataFrame([row])
        df_row = self._engineer_features(df_row)
        X = df_row[self._feature_cols].fillna(0.0).values

        raw = self._base_model.predict_proba(X)[0, 1]
        if isinstance(self._calibrator, IsotonicRegression):
            return float(self._calibrator.predict(np.array([raw]))[0])
        else:
            return float(self._calibrator.predict_proba(np.array([[raw]]))[0, 1])

    def simulate_state(self, state_dict: dict) -> float:
        """Alias for predict_proba for counterfactual states."""
        return self.predict_proba(state_dict)


def _ot_transform(state: dict) -> dict:
    """Map OT state to regulation-equivalent for model inference."""
    OT_DURATION = 600.0
    REG_EQUIV = 300.0

    ot_seconds = float(state.get("seconds_remaining", OT_DURATION))
    scale = REG_EQUIV / OT_DURATION
    reg_seconds = max(10.0, ot_seconds * scale)

    new = dict(state)
    new["is_overtime"] = 0
    new["overtime_possession_number"] = 0
    new["quarter"] = 4
    new["seconds_remaining"] = reg_seconds
    return new


# ---------------------------------------------------------------------------
# Preload all models at import time for faster first request
# ---------------------------------------------------------------------------
def preload_models():
    """Load all models into cache. Call at server startup."""
    try:
        _load_conversion_model()
        _load_fg_model()
        _load_punt_model()
        _load_wp_model()
        logger.info("All 4 submodels loaded successfully.")
    except Exception as e:
        logger.error("Failed to preload models: %s", e)
        raise
