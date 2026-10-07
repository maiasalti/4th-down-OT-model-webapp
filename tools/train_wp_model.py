"""
Retrain the win probability model.

Why: the old model (win_probability_model.pkl) was flat inside a team's own
30: 1st & 10 at your own 1 and at your own 30 scored the exact same 55.7% in a
tied OT game. The engine's punt (and failed 4th down) outcomes almost always
hand the opponent the ball inside their own 30, so every punt landed on the
same ~44% no matter where you punted from.

Root cause: field position only enters the old model as raw yardline_100 plus
three red-zone style flags. In close, late games (exactly what OT maps onto)
the trees never split on yardline past the ~own 30, because that region is
sparse there and score/time dominate the loss. Early stopping at 179 trees
(lr 0.02) and an isotonic calibrator that turns the raw score into a 216-step
function flattened what little slope there was. Simply retraining with more
trees and monotone constraints was tried and stays flat (own 1-30 = 52.7%).

Fix: give the model field position in the units WP actually cares about.
  * ep: expected points of the snap (yardline, down, distance), from a small
    monotone XGBoost regressor fit to nflfastR's EP, shipped with the model
    so serving computes it exactly like training.
  * ep_score_diff = score_differential + ep
  * ep_diff_time_ratio = (score_differential + ep) * exp(4 * elapsed): the
    same time scaling as nflfastR's Diff_Time_Ratio, so the value of field
    position grows as the clock runs down.
Plus monotone constraints on every feature with an obvious direction, 3x
weight on late close-game plays (the slice OT maps onto), and no
isotonic step calibration: the logistic objective is already calibrated on
held-out seasons (a Platt fit on 2022 made 2023-24 worse, so none is applied).

Splits: fit 2016-2021, early stopping on 2022, test on 2023-2024.
Features are built by models.wp_features, shared with serving.

Usage:
    python tools/train_wp_model.py \
        --pbp ../nfl-ot-4th-down-model/data/raw/pbp_2016_2024.parquet \
        [--compare path/to/old/wp]   # a models/<name> folder in the same format
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import brier_score_loss, log_loss

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import models  # noqa: E402

EP_FEATURES = ["yardline_100", "down", "ydstogo"]

# feature -> monotone direction (+1 raises WP, -1 lowers it, 0 free)
FEATURES = {
    "score_differential": 1, "quarter": 0, "seconds_remaining": 0, "yardline_100": -1,
    "down": -1, "ydstogo": -1, "offense_timeouts": 1, "defense_timeouts": -1,
    "seconds_remaining_sqrt": 0, "seconds_remaining_log1p": 0, "half_seconds_remaining": 0,
    "elapsed_share": 0, "diff_time_ratio": 1, "spread_time": -1, "score_x_time": 0,
    "urgency": 0, "clock_leverage": 0, "abs_score_diff": 0, "score_differential_sq": 0,
    "timeout_diff": 1, "total_timeouts": 0, "home": 0, "posteam_spread": -1,
    "ep": 1, "ep_score_diff": 1, "ep_diff_time_ratio": 1,
}


# OT is scored as the last minutes of a close 4th quarter, so that slice is
# what the engine actually queries. Weighting it 3x improves held-out logloss
# both there and overall (see the printout).
LATE_WEIGHT = 3.0


def late_close(d: pd.DataFrame) -> np.ndarray:
    return ((d["quarter"] == 4) & (d["seconds_remaining"] <= 480)
            & (d["score_differential"].abs() <= 8)).values


def load(pbp: str) -> pd.DataFrame:
    cols = ["game_id", "season", "posteam", "home_team", "qtr", "game_seconds_remaining",
            "half_seconds_remaining", "score_differential", "down", "ydstogo", "yardline_100",
            "posteam_timeouts_remaining", "defteam_timeouts_remaining", "result", "spread_line",
            "ep", "vegas_wp"]
    df = pd.read_parquet(pbp, columns=cols).rename(columns={"ep": "nflfastr_ep",
                                                            "half_seconds_remaining": "nflfastr_half_sec"})
    df = df.dropna(subset=["down", "ydstogo", "yardline_100", "game_seconds_remaining",
                           "score_differential", "result", "posteam"])
    df = df[df["down"].between(1, 4) & (df["qtr"] <= 4) & (df["result"] != 0)].copy()  # regulation, no ties
    home = df["posteam"] == df["home_team"]
    df["won"] = ((home & (df["result"] > 0)) | (~home & (df["result"] < 0))).astype(int)
    df["quarter"] = df["qtr"].astype(int)
    df["seconds_remaining"] = df["game_seconds_remaining"].clip(lower=0).astype(float)
    df["yardline_100"] = df["yardline_100"].clip(1, 99).astype(float)
    df["ydstogo"] = df["ydstogo"].clip(1, 99).astype(float)
    df["offense_timeouts"] = df["posteam_timeouts_remaining"].fillna(3).clip(0, 3)
    df["defense_timeouts"] = df["defteam_timeouts_remaining"].fillna(3).clip(0, 3)
    df["home"] = home.astype(float)
    # nflfastR spread_line is the home margin (home favoured > 0). The app's
    # input is the posteam's spread with negative = favoured, so flip it.
    df["posteam_spread"] = np.where(home, -df["spread_line"].fillna(0.0), df["spread_line"].fillna(0.0))
    df["is_overtime"] = 0
    df["overtime_possession_number"] = 0
    return df


def fit_ep(df: pd.DataFrame) -> xgb.Booster:
    """Drive value of a snap. Fit away from the end of halves so it means
    'what is this field position worth', not 'is the clock about to expire'."""
    d = df[(df["nflfastr_half_sec"] > 120) & df["nflfastr_ep"].notna() & (df.season <= 2021)]
    X = d[EP_FEATURES].assign(ydstogo=d["ydstogo"].clip(upper=30)).values
    m = xgb.XGBRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, min_child_weight=50,
                         monotone_constraints="(-1,-1,-1)", random_state=42)
    m.fit(X, d["nflfastr_ep"].values)
    b = m.get_booster()
    b.feature_names = None
    return b


def matrix(df: pd.DataFrame, feats, ep_booster=None) -> np.ndarray:
    s = {c: df[c].values for c in df.columns}
    if ep_booster is not None:
        s["ep"] = models.expected_points(ep_booster, s)
    f = models.wp_features(s)
    return np.column_stack([np.asarray(f[c], dtype=float) for c in feats])


def metrics(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return f"logloss {log_loss(y, p):.4f}  brier {brier_score_loss(y, p):.4f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbp", default=str(ROOT.parent / "nfl-ot-4th-down-model/data/raw/pbp_2016_2024.parquet"))
    ap.add_argument("--out", default=str(ROOT / "models/wp"))
    ap.add_argument("--compare", help="old WP model folder (models/<name> format) to benchmark")
    args = ap.parse_args()

    df = load(args.pbp)
    feats = list(FEATURES)
    fit, val, test = (df[df.season <= 2021], df[df.season == 2022], df[df.season >= 2023])
    print(f"plays fit={len(fit)} val={len(val)} test={len(test)}")

    ep = fit_ep(df)
    Xfit, Xval, Xtest = (matrix(d, feats, ep) for d in (fit, val, test))

    model = xgb.XGBClassifier(
        n_estimators=4000, learning_rate=0.05, max_depth=6, min_child_weight=50,
        subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0,
        monotone_constraints="(" + ",".join(str(v) for v in FEATURES.values()) + ")",
        objective="binary:logistic", eval_metric="logloss", early_stopping_rounds=150,
        tree_method="hist", random_state=42, n_jobs=-1,
    )
    model.fit(Xfit, fit["won"].values, sample_weight=np.where(late_close(fit), LATE_WEIGHT, 1.0),
              eval_set=[(Xval, val["won"].values)],
              sample_weight_eval_set=[np.where(late_close(val), LATE_WEIGHT, 1.0)], verbose=False)
    print("best iteration", model.best_iteration)
    booster = model.get_booster()[: model.best_iteration + 1]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    booster.feature_names = None
    booster.save_model(str(out / "model.ubj"))
    ep.save_model(str(out / "ep.ubj"))
    (out / "meta.json").write_text(json.dumps({
        "features": feats,
        "monotone": FEATURES,
        "ep_model": {"file": "ep.ubj", "features": EP_FEATURES, "ydstogo_cap": 30},
        "train": "regulation plays 2016-2021 (no ties; Q4 last 8 min one-score plays weighted 3x), early stopping on 2022, test 2023-24",
        "folds": [{"file": "model.ubj", "calibrator": {"type": "identity"}}],
    }, indent=1))

    models._cache.pop("wp", None)
    new = models.CalibratedEnsemble(out)
    yt = test["won"].values
    preds = {"new model": new.predict(Xtest)}
    if args.compare:
        old = models.CalibratedEnsemble(args.compare)
        # the old model was trained with the opposite spread sign (+ = favoured)
        t = test.assign(guaranteed_possession=0.0, posteam_spread=-test["posteam_spread"])
        preds["old model"] = old.predict(matrix(t, old.features))
    preds["nflfastR vegas_wp"] = test["vegas_wp"].values
    # The slice OT maps onto: 4th quarter, last 8 minutes, one-score game.
    late = late_close(test)
    for label, mask in (("all 2023-24 regulation plays", np.ones(len(test), bool)),
                        ("2023-24, Q4 last 8 min, one-score game", late)):
        print(f"test: {label} (n={mask.sum()})")
        for name, p in preds.items():
            ok = mask & ~np.isnan(p)
            print(f"  {name:18s}", metrics(yt[ok], p[ok]))


if __name__ == "__main__":
    main()
