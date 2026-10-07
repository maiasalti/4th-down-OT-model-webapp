"""
Retrain the punt outcome model: where does the receiving team start?

Same three inputs as the original notebook model (punt_outcome_model.ipynb in
the model repo), so the app's punter inputs keep their meaning:
  yardline_100, punt_distance_roll6, inside_twenty_rate_roll6

What changed: the old target was the *landing spot* of the punt,
100 - yardline_100 + kick_distance, which ignores the return. That made
every punt look ~4-8 yards better than it really is. The target here is the
real start of the next drive: landing spot minus return yards, touchbacks at
the 20, blocked punts at the line of scrimmage. Monotone constraints keep the
curve sensible (punting from deeper never helps you; a better punter never
hurts you).

Usage:
    python tools/train_punt_model.py --data ../nfl-ot-4th-down-model/data/processed/punt_attempts.parquet
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ["yardline_100", "punt_distance_roll6", "inside_twenty_rate_roll6"]


def build(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    blocked = df["blocked"].fillna(False).astype(bool)
    tb = df["touchback"].fillna(False).astype(bool)
    kd = df["kick_distance"].fillna(0.0)
    ret = df["return_yards"].fillna(0.0)

    gross = 100 - df["yardline_100"] + kd               # landing spot (old target)
    df["landing"] = np.where(tb, 80.0, gross)
    net = np.where(tb, 80.0, gross - ret)               # where the drive really starts
    net = np.where(blocked, 100 - df["yardline_100"], net)
    df["opponent_start"] = np.clip(net, 1, 99)

    # Rolling punter form, built exactly like the notebook: per game, then the
    # previous 6 games (shifted so a game never sees its own punts).
    ok = ~blocked & (kd > 0)
    g = (df[ok].assign(in20=lambda d: d["landing"] > 80)
         .groupby(["posteam", "season", "week"])
         .agg(avg_punt_distance=("kick_distance", "mean"), inside_twenty_rate=("in20", "mean"))
         .reset_index().sort_values(["posteam", "season", "week"]))
    g["punt_distance_roll6"] = g.groupby("posteam")["avg_punt_distance"].transform(
        lambda x: x.shift(1).rolling(6, min_periods=1).mean())
    g["inside_twenty_rate_roll6"] = g.groupby("posteam")["inside_twenty_rate"].transform(
        lambda x: x.shift(1).rolling(6, min_periods=1).mean())
    df = df.merge(g[["posteam", "season", "week", *FEATURES[1:]]], on=["posteam", "season", "week"], how="left")
    return df.dropna(subset=FEATURES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT.parent / "nfl-ot-4th-down-model/data/processed/punt_attempts.parquet"))
    ap.add_argument("--out", default=str(ROOT / "models/punt"))
    ap.add_argument("--old", help="old punt_outcome_xgb.json to compare against")
    args = ap.parse_args()

    df = build(pd.read_parquet(args.data))
    train, test = df[df["season"] <= 2022], df[df["season"] >= 2023]
    model = xgb.XGBRegressor(
        n_estimators=400, max_depth=4, learning_rate=0.03, subsample=0.8,
        min_child_weight=20, monotone_constraints="(-1,1,1)", random_state=42,
    )
    model.fit(train[FEATURES].values, train["opponent_start"].values)
    pred = model.predict(test[FEATURES].values)
    print(f"rows train={len(train)} test={len(test)}")
    print(f"new model  MAE vs real drive start (2023-24): {np.mean(np.abs(pred - test['opponent_start'])):.2f} yd, "
          f"bias {np.mean(pred - test['opponent_start']):+.2f} yd")
    if args.old:
        old = xgb.XGBRegressor()
        old.load_model(args.old)
        po = old.predict(test[FEATURES])
        print(f"old model  MAE vs real drive start (2023-24): {np.mean(np.abs(po - test['opponent_start'])):.2f} yd, "
              f"bias {np.mean(po - test['opponent_start']):+.2f} yd")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    model.get_booster().save_model(str(out / "model.ubj"))
    (out / "meta.json").write_text(json.dumps({
        "features": FEATURES,
        "target": "opponent yardline_100 at the start of their drive (net of returns)",
        "folds": [{"file": "model.ubj"}],
    }, indent=1))
    print("saved", out)


if __name__ == "__main__":
    main()
