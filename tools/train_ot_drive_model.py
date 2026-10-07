"""
Fit the overtime drive model: how does a drive that starts at a given yard
line end?  Classes: touchdown, field goal, defensive score (pick six / fumble
return / safety), no score. Plus where the other team starts next when the
drive ends without a score.

Why OT needs this instead of a regulation WP model: the old engine scored every
OT state as "tied, ~4 minutes left in the 4th quarter". In regulation a field
goal there doesn't end the game, so the opponent getting the ball at your 10
looks only a bit worse than them getting it at their own 35. In OT it is the
difference between ~97% and ~73% that they win, because any score ends it.
decision_engine.ot_values() rolls these drive outcomes up through the 2025
OT rules (both teams get a possession, then sudden death).

Model: multinomial logistic regression on a smooth basis of the start yard
line plus the pregame spread and home field (so the app's team-strength
inputs still move the answer). Coefficients go to models/ot/meta.json; serving
needs only numpy.

Data: nflfastR drives 2016-2022 that start on 1st down with >5 min left in the
half in a one-score game (so clock management doesn't distort them). Tested
on 2023-24 and on every real overtime drive in the data.

Usage:
    python tools/train_ot_drive_model.py --pbp ../nfl-ot-4th-down-model/data/raw/pbp_2016_2024.parquet
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from models import ot_drive_basis  # noqa: E402

CLASSES = ["none", "fg", "td", "def_score"]


def drives(pbp: str) -> pd.DataFrame:
    cols = ["game_id", "play_id", "season", "fixed_drive", "fixed_drive_result", "posteam", "home_team",
            "yardline_100", "down", "qtr", "half_seconds_remaining", "score_differential", "spread_line"]
    d = pd.read_parquet(pbp, columns=cols).dropna(subset=["posteam", "yardline_100", "down"])
    d = d.sort_values(["game_id", "play_id"])
    first = d.groupby(["game_id", "fixed_drive"], sort=True).first().reset_index()
    first = first.sort_values(["game_id", "fixed_drive"])
    first["next_start"] = first.groupby("game_id")["yardline_100"].shift(-1)
    first["next_posteam"] = first.groupby("game_id")["posteam"].shift(-1)
    r = first["fixed_drive_result"]
    first["cls"] = np.select(
        [r == "Touchdown", r == "Field goal", r.isin(["Opp touchdown", "Safety"])], [2, 1, 3], 0)
    home = first["posteam"] == first["home_team"]
    first["home"] = home.astype(float)
    first["posteam_spread"] = np.where(home, -first["spread_line"].fillna(0), first["spread_line"].fillna(0))
    return first[~r.isin(["End of half", "End of game"]) & (first["down"] == 1)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbp", default=str(ROOT.parent / "nfl-ot-4th-down-model/data/raw/pbp_2016_2024.parquet"))
    ap.add_argument("--out", default=str(ROOT / "models/ot"))
    args = ap.parse_args()

    allx = drives(args.pbp)
    ot = allx[allx["qtr"] >= 5]
    reg = allx[(allx["qtr"] <= 4) & (allx["half_seconds_remaining"] > 300)
               & (allx["score_differential"].abs() <= 8)]
    fit, test = reg[reg.season <= 2022], reg[reg.season >= 2023]
    print(f"drives fit={len(fit)} test={len(test)} overtime={len(ot)}")

    def X(df):
        return ot_drive_basis(df["yardline_100"].values, df["posteam_spread"].values, df["home"].values)

    clf = LogisticRegression(C=10.0, max_iter=5000).fit(X(fit), fit["cls"].values)
    base = LogisticRegression(C=10.0, max_iter=5000).fit(X(fit)[:, :4], fit["cls"].values)
    print("test 2023-24 logloss  model:", round(log_loss(test["cls"], clf.predict_proba(X(test)), labels=[0, 1, 2, 3]), 4),
          " yard line only:", round(log_loss(test["cls"], base.predict_proba(X(test)[:, :4]), labels=[0, 1, 2, 3]), 4),
          " class rates only:", round(log_loss(test["cls"], np.tile(np.bincount(fit["cls"], minlength=4) / len(fit), (len(test), 1))), 4))

    p_ot = clf.predict_proba(X(ot))
    print(f"real OT drives: scored {np.isin(ot['cls'], [1, 2]).mean():.3f}, model says {p_ot[:, 1:3].sum(1).mean():.3f}  "
          f"(TD {(ot['cls'] == 2).mean():.3f} vs {p_ot[:, 2].mean():.3f}, FG {(ot['cls'] == 1).mean():.3f} vs {p_ot[:, 1].mean():.3f})")

    ns = fit[(fit["cls"] == 0) & (fit["next_posteam"] != fit["posteam"]) & fit["next_start"].notna()]
    slope, intercept = np.polyfit(ns["yardline_100"], ns["next_start"], 1)
    print(f"next start after a scoreless drive = {intercept:.1f} {slope:+.3f} * start yard line")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "meta.json").write_text(json.dumps({
        "classes": [CLASSES[c] for c in clf.classes_],
        "coef": clf.coef_.tolist(),
        "intercept": clf.intercept_.tolist(),
        "next_start": {"intercept": float(intercept), "slope": float(slope)},
        "train": "nflfastR drives 2016-2022, 1st-down starts, >5 min left in half, one-score game",
    }, indent=1))
    print("saved", out)


if __name__ == "__main__":
    main()
