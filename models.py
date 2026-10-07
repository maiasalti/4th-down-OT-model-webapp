"""
Submodel inference: numpy + native XGBoost only.

Every function here is vectorised: pass numpy arrays (one row per situation)
and get an array back. The decision engine scores a whole field sweep (33 yard
lines x 5 post-play states) with one call per model instead of ~270 single-row
predictions, which is what made /api/analyze take ~19 s on Render's 0.1 CPU.

Artifacts (models/<name>/meta.json + native .ubj/.json boosters):
  conversion/  4th-down conversion: 5-fold XGBoost + isotonic calibration
  fg/          field goal make prob: 5-fold XGBoost + isotonic calibration
  punt/        opponent start yardline after a punt (XGBoost regressor)
  wp/          win probability (monotone XGBoost, no step calibration),
               plus ep.ubj, the expected-points model it uses as a feature

The conversion and FG folds were exported 1:1 from the old sklearn pickles
(tools/export_legacy_models.py checks they match). punt/ and wp/ are retrained
by tools/train_punt_model.py and tools/train_wp_model.py.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import numpy as np
import xgboost as xgb

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parent / "models"

# ---------------------------------------------------------------------------
# League-average fallbacks
# ---------------------------------------------------------------------------
LEAGUE_AVG_PUNT = {"punt_distance_roll6": 45.9, "inside_twenty_rate_roll6": 0.44}

_FG_BUCKETS = [  # (max distance, league make rate)
    (30, 0.94), (35, 0.92), (40, 0.88), (45, 0.83), (50, 0.77),
    (55, 0.67), (58, 0.57), (61, 0.46), (64, 0.34), (999, 0.22),
]

# Empirical base rates and model blending weights for the conversion model
_EMPIRICAL_BASE_RATE = np.array([np.nan, .690, .620, .550, .500, .460, .430, .400, .370, .330, .280])
_MODEL_WEIGHT = np.array([np.nan, .85, .80, .55, .50, .50, .45, .40, .25, .20, .15])
_EPA_BLEND_FACTOR = 0.04


# ---------------------------------------------------------------------------
# Generic loaders
# ---------------------------------------------------------------------------
def _calibrate(raw: np.ndarray, cal: dict | None) -> np.ndarray:
    if not cal or cal["type"] == "identity":
        return raw
    if cal["type"] == "isotonic":  # sklearn IsotonicRegression(out_of_bounds="clip")
        x, y = cal["_x"], cal["_y"]
        return np.interp(np.clip(raw, x[0], x[-1]), x, y)
    if cal["type"] == "platt_logit":  # sigmoid(a * logit(p) + b): smooth and monotone
        p = np.clip(raw, 1e-6, 1 - 1e-6)
        return 1.0 / (1.0 + np.exp(-(cal["a"] * np.log(p / (1 - p)) + cal["b"])))
    raise ValueError(f"unknown calibrator {cal['type']}")


def _load_booster(path: Path) -> xgb.Booster:
    b = xgb.Booster()
    b.load_model(str(path))
    # Render free is 0.1 CPU: extra threads only add contention.
    b.set_param({"nthread": 1})
    return b


class CalibratedEnsemble:
    """Mean over folds of calibrator(booster(X)), same math as sklearn's
    CalibratedClassifierCV(ensemble=True).predict_proba(X)[:, 1]."""

    def __init__(self, folder: Path | str):
        folder = Path(folder)
        self.meta = json.loads((folder / "meta.json").read_text())
        self.features: list[str] = self.meta["features"]
        self.folds = []
        for f in self.meta["folds"]:
            cal = dict(f.get("calibrator") or {"type": "identity"})
            if cal["type"] == "isotonic":
                cal["_x"], cal["_y"] = np.asarray(cal["x"]), np.asarray(cal["y"])
            self.folds.append((_load_booster(folder / f["file"]), cal))
        ep = self.meta.get("ep_model")
        self.ep_booster = _load_booster(folder / ep["file"]) if ep else None

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.ascontiguousarray(X, dtype=np.float32)
        out = np.zeros(len(X))
        for b, cal in self.folds:
            out += _calibrate(b.inplace_predict(X, validate_features=False), cal)
        return out / len(self.folds)


_cache: dict[str, CalibratedEnsemble] = {}
_lock = threading.Lock()


def _model(name: str) -> CalibratedEnsemble:
    m = _cache.get(name)
    if m is None:
        with _lock:
            m = _cache.get(name)
            if m is None:
                m = CalibratedEnsemble(MODELS_DIR / name)
                _cache[name] = m
                logger.info("Loaded %s model", name)
    return m


def preload_models() -> None:
    """Load every model (called once by gunicorn --preload, before forking)."""
    for name in ("conversion", "fg", "punt", "wp"):
        _model(name)
    ot_drive_model()


def _col(v, n) -> np.ndarray:
    return np.broadcast_to(np.asarray(v, dtype=float), (n,)).astype(float)


# ---------------------------------------------------------------------------
# 4th-down conversion probability (XGBoost + empirical blending)
# ---------------------------------------------------------------------------
def conversion_probability(
    yards_to_go, yardline_100, score_differential=0.0, game_seconds_remaining=300.0,
    qtr=5, wp=0.5, temp=65.0, shotgun=1, no_huddle=0, off_epa=0.0, def_epa=0.0,
    off_success_rate=0.42, off_ppg=23.0, def_success_rate=0.42, def_ppg=23.0,
) -> np.ndarray:
    ytg = np.atleast_1d(np.asarray(yards_to_go, dtype=float))
    yl = np.atleast_1d(np.asarray(yardline_100, dtype=float))
    n = max(len(ytg), len(yl))
    ytg, yl = _col(ytg, n), _col(yl, n)
    m = _model("conversion")
    vals = {
        "ydstogo": ytg, "qtr": qtr, "yardline_100": yl, "score_differential": score_differential,
        "game_seconds_remaining": game_seconds_remaining, "wp": wp, "temp": temp,
        "shotgun": shotgun, "no_huddle": no_huddle,
        "epa_per_game_roll15": off_epa, "success_rate_roll15": off_success_rate,
        "points_per_game_roll15": off_ppg, "def_epa_per_game_roll15": def_epa,
        "def_success_rate_roll15": def_success_rate, "def_points_per_game_roll15": def_ppg,
    }
    X = np.column_stack([_col(vals.get(f, 0.0), n) for f in m.features])
    raw = m.predict(X)

    # Blend with empirical base rates; trust the model less as distance grows.
    k = np.clip(np.round(ytg), 1, 10).astype(int)
    base = np.where(ytg > 10, np.maximum(0.05, _EMPIRICAL_BASE_RATE[10] - 0.035 * (ytg - 10)),
                    _EMPIRICAL_BASE_RATE[k])
    w = np.where(ytg > 10, 0.15, _MODEL_WEIGHT[k])
    adj_base = np.clip(base + (off_epa - def_epa) * _EPA_BLEND_FACTOR, 0.05, 0.95)
    out = np.clip(w * raw + (1 - w) * adj_base, 0.01, 0.99)
    return np.where(ytg <= 0, 0.90, out)


# ---------------------------------------------------------------------------
# Field goal make probability (XGBoost + isotonic calibration)
# ---------------------------------------------------------------------------
def _league_fg_rate(distance: np.ndarray) -> np.ndarray:
    out = np.full(distance.shape, _FG_BUCKETS[-1][1])
    for hi, rate in reversed(_FG_BUCKETS):
        out = np.where(distance <= hi, rate, out)
    return out


def fg_make_probability(
    yardline_100, is_dome=False, wind=8.0, temp=65.0, wind_gust=None,
    is_precipitation=False, fg_make_rate_roll6=None, surface_is_grass=True,
    altitude_ft=0.0, game_seconds_remaining=300.0, score_differential=0.0, is_overtime=True,
) -> np.ndarray:
    yl = np.atleast_1d(np.asarray(yardline_100, dtype=float))
    n = len(yl)
    distance = yl + 17.0
    m = _model("fg")

    temp_adj = 72.0 if is_dome else float(temp)
    gust = 0.0 if is_dome else float(wind_gust if wind_gust is not None else wind)
    league = _league_fg_rate(distance)
    recent = league if fg_make_rate_roll6 is None else _col(fg_make_rate_roll6, n)

    # Past ~57 yd the trees have almost no data and go flat. Hold the features at
    # the anchor distance and taper with the logit slope fitted at training time.
    tail_slope = m.meta.get("tail_slope")
    anchor = 57.0
    model_dist = np.minimum(distance, anchor) if tail_slope else distance
    over = np.maximum(0.0, distance - anchor) if tail_slope else np.zeros(n)

    X = np.column_stack([
        model_dist, _col(int(is_dome), n), _col(gust, n), gust * model_dist,
        _col(temp_adj, n), temp_adj * model_dist,
        _col(int(is_precipitation and not is_dome), n), recent,
        league,             # career rate: league average for this distance
        _col(60, n),        # career attempts: typical veteran sample
        _col(int(surface_is_grass), n), _col(altitude_ft, n),
        _col(game_seconds_remaining, n), _col(score_differential, n), _col(int(is_overtime), n),
    ])
    p = np.clip(m.predict(X), 1e-4, 1 - 1e-4)
    if tail_slope:
        logit = np.log(p / (1 - p)) + float(tail_slope) * over
        p = 1.0 / (1.0 + np.exp(-logit))
    p = np.where(distance > 70, 0.0, p)
    return np.where(distance < 18, 0.99, p)


# ---------------------------------------------------------------------------
# Punt outcome: where the receiving team starts (their yardline_100)
# ---------------------------------------------------------------------------
def punt_opponent_start(yardline_100, punt_distance_roll6=None, inside_twenty_rate_roll6=None) -> np.ndarray:
    yl = np.atleast_1d(np.asarray(yardline_100, dtype=float))
    n = len(yl)
    pd6 = LEAGUE_AVG_PUNT["punt_distance_roll6"] if punt_distance_roll6 is None else punt_distance_roll6
    i20 = LEAGUE_AVG_PUNT["inside_twenty_rate_roll6"] if inside_twenty_rate_roll6 is None else inside_twenty_rate_roll6
    X = np.column_stack([yl, _col(pd6, n), _col(i20, n)])
    return np.clip(_model("punt").predict(X), 1, 99)


# ---------------------------------------------------------------------------
# Win probability
# ---------------------------------------------------------------------------
WP_STATE_KEYS = [
    "score_differential", "quarter", "seconds_remaining", "yardline_100", "down", "ydstogo",
    "offense_timeouts", "defense_timeouts", "is_overtime", "overtime_possession_number",
    "home", "posteam_spread", "guaranteed_possession",
]


def expected_points(ep_booster: xgb.Booster, s: dict) -> np.ndarray:
    """Expected points of a snap from (yardline_100, down, ydstogo<=30)."""
    X = np.column_stack([
        np.asarray(s["yardline_100"], dtype=float),
        np.asarray(s["down"], dtype=float),
        np.minimum(np.asarray(s["ydstogo"], dtype=float), 30.0),
    ]).astype(np.float32)
    return ep_booster.inplace_predict(X, validate_features=False).astype(float)


def wp_features(s: dict) -> dict:
    """Engineered WP features. Shared by inference and tools/train_wp_model.py so
    training and serving can't drift apart."""
    sec = np.asarray(s["seconds_remaining"], dtype=float)
    sd = np.asarray(s["score_differential"], dtype=float)
    yl = np.asarray(s["yardline_100"], dtype=float)
    ytg = np.asarray(s["ydstogo"], dtype=float)
    ot = np.asarray(s["is_overtime"]).astype(int)
    pn = np.asarray(s["overtime_possession_number"]).astype(int)
    spread = np.nan_to_num(np.asarray(s.get("posteam_spread", 0.0), dtype=float) * np.ones_like(sec))
    elapsed = np.clip((3600.0 - sec) / 3600.0, 0.0, 1.0)
    f = dict(s)
    f.update({
        "elapsed_share": elapsed,
        "half_seconds_remaining": np.where(sec > 1800.0, sec - 1800.0, sec),
        "diff_time_ratio": sd * np.exp(4.0 * elapsed),
        "spread_time": spread * np.exp(-4.0 * elapsed),
        "seconds_remaining_sqrt": np.sqrt(sec),
        "seconds_remaining_log1p": np.log1p(sec),
        "score_x_time": sd * sec / 3600.0,
        "urgency": np.abs(sd) / (sec + 1.0),
        "clock_leverage": 1.0 / (np.abs(sd) + 1.0) / (sec + 60.0),
        "abs_score_diff": np.abs(sd),
        "score_differential_sq": sd ** 2,
        "timeout_diff": np.asarray(s["offense_timeouts"]) - np.asarray(s["defense_timeouts"]),
        "total_timeouts": np.asarray(s["offense_timeouts"]) + np.asarray(s["defense_timeouts"]),
        "ydstogo_log1p": np.log1p(ytg),
        "short_yardage": (ytg <= 2).astype(int),
        "fg_range": (yl <= 35).astype(int),
        "red_zone": (yl <= 20).astype(int),
        "scoring_position": (yl <= 10).astype(int),
        "ot_first_poss": (ot & (pn == 0)).astype(int),
        "ot_second_poss": (ot & (pn == 1)).astype(int),
        "ot_sudden_death": (ot & (pn >= 2)).astype(int),
        "ot_must_score": ((ot == 1) & (pn >= 1) & (sd < 0)).astype(int),
        "ot_leading_first_poss": ((ot == 1) & (pn == 0) & (sd > 0)).astype(int),
    })
    if "ep" in s:  # field position in points, scaled like diff_time_ratio
        ep_sd = sd + np.asarray(s["ep"], dtype=float)
        f["ep_score_diff"] = ep_sd
        f["ep_diff_time_ratio"] = ep_sd * np.exp(4.0 * elapsed)
    return f


def ot_to_regulation(s: dict) -> dict:
    """The WP model is trained on regulation plays. An OT state is scored as the
    equivalent late-4th-quarter state: 10 min of OT -> last 5 min of Q4."""
    out = dict(s)
    ot = np.asarray(s["is_overtime"]).astype(bool)
    sec = np.asarray(s["seconds_remaining"], dtype=float)
    out["seconds_remaining"] = np.where(ot, np.maximum(10.0, sec * 0.5), sec)
    out["quarter"] = np.where(ot, 4, s["quarter"])
    out["overtime_possession_number"] = np.where(ot, 0, s["overtime_possession_number"])
    out["is_overtime"] = np.zeros_like(sec, dtype=int)
    return out


def win_probability(states: dict) -> np.ndarray:
    """P(team with the ball wins) for each row of a dict-of-arrays game state."""
    n = len(np.atleast_1d(states["yardline_100"]))
    s = {k: _col(states.get(k, 0.0), n) for k in WP_STATE_KEYS}
    m = _model("wp")
    s = ot_to_regulation(s)
    if m.ep_booster is not None:
        s["ep"] = expected_points(m.ep_booster, s)
    f = wp_features(s)
    X = np.column_stack([_col(f[c], n) for c in m.features])
    return m.predict(X)


# ---------------------------------------------------------------------------
# Overtime drive model: how a drive starting at a yard line ends
# ---------------------------------------------------------------------------
def ot_drive_basis(yardline_100, posteam_spread, home) -> np.ndarray:
    """Smooth yard-line basis + pregame spread (negative = favoured) + home."""
    x = np.asarray(yardline_100, dtype=float) / 100.0
    n = len(x)
    return np.column_stack([x, x ** 2, x ** 3, np.log(x + 0.01), _col(posteam_spread, n), _col(home, n)])


class OTDriveModel:
    def __init__(self, folder: Path):
        meta = json.loads((folder / "meta.json").read_text())
        self.classes = meta["classes"]
        self.coef = np.asarray(meta["coef"])
        self.intercept = np.asarray(meta["intercept"])
        self.next_a = meta["next_start"]["intercept"]
        self.next_b = meta["next_start"]["slope"]

    def probs(self, yardline_100, posteam_spread=0.0, home=0.5) -> dict:
        z = ot_drive_basis(yardline_100, posteam_spread, home) @ self.coef.T + self.intercept
        z = np.exp(z - z.max(axis=1, keepdims=True))
        p = z / z.sum(axis=1, keepdims=True)
        return {c: p[:, i] for i, c in enumerate(self.classes)}

    def next_start(self, yardline_100) -> np.ndarray:
        """Where the other team starts after a scoreless drive from here."""
        return np.clip(self.next_a + self.next_b * np.asarray(yardline_100, dtype=float), 1, 99)


_ot_model: OTDriveModel | None = None


def ot_drive_model() -> OTDriveModel:
    global _ot_model
    if _ot_model is None:
        _ot_model = OTDriveModel(MODELS_DIR / "ot")
    return _ot_model
