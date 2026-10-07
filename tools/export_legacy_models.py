"""
One-time conversion of the sklearn/joblib model pickles into native XGBoost
files plus a small JSON of calibration points.

Why: the pickles need pandas + scikit-learn + scipy + joblib at runtime just
to unpickle a CalibratedClassifierCV wrapper. The math it does at predict time
is tiny (average of 5 boosters, each passed through an isotonic step curve),
so we store the boosters natively and redo that math with numpy in models.py.
The server image drops ~170 MB of dependencies and imports start much faster.

Usage (dev deps only, see requirements-dev.txt):
    python tools/export_legacy_models.py --src path/to/old/models --dst models

It then checks that the exported files reproduce the pickles' predictions.
"""

import argparse
import json
import warnings
from pathlib import Path

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression

warnings.filterwarnings("ignore")


def export_calibrated_cv(pkl: Path, out_dir: Path, extra_keys=()):
    art = joblib.load(pkl)
    model = art["model"]
    out_dir.mkdir(parents=True, exist_ok=True)
    folds = []
    for i, cc in enumerate(model.calibrated_classifiers_):
        cal = cc.calibrators[0]
        assert isinstance(cal, IsotonicRegression), type(cal)
        booster = cc.estimator.get_booster()
        booster.feature_names = None  # we pass plain arrays in feature order
        fname = f"fold{i}.ubj"
        booster.save_model(str(out_dir / fname))
        folds.append({
            "file": fname,
            "calibrator": {
                "type": "isotonic",
                "x": [float(v) for v in cal.X_thresholds_],
                "y": [float(v) for v in cal.y_thresholds_],
            },
        })
    features = list(art.get("features") or [])
    meta = {"source": pkl.name, "features": features, "folds": folds}
    for k in extra_keys:
        if k in art:
            meta[k] = art[k]
    (out_dir / "meta.json").write_text(json.dumps(meta))
    return art


def check(art, out_dir: Path, X: np.ndarray):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from models import CalibratedEnsemble  # noqa: E402

    import pandas as pd
    feats = art.get("features")
    Xin = pd.DataFrame(X, columns=feats) if art["model"].__dict__.get("feature_names_in_") is not None else X
    ref = art["model"].predict_proba(Xin)[:, 1]
    new = CalibratedEnsemble(out_dir).predict(X)
    err = float(np.max(np.abs(ref - new)))
    print(f"{out_dir.name}: max |pickle - native| = {err:.2e} over {len(X)} rows")
    assert err < 1e-5, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="folder with the legacy .pkl files")
    ap.add_argument("--dst", default="models")
    args = ap.parse_args()
    src, dst = Path(args.src), Path(args.dst)
    rng = np.random.default_rng(0)

    conv = export_calibrated_cv(src / "fourth_down_conversion.pkl", dst / "conversion")
    n = 2000
    Xc = np.column_stack([
        rng.integers(1, 16, n), np.full(n, 5), rng.integers(1, 100, n), rng.integers(-8, 9, n),
        rng.uniform(10, 600, n), rng.uniform(0, 1, n), rng.uniform(10, 95, n), rng.integers(0, 2, n),
        rng.integers(0, 2, n), rng.uniform(-.3, .3, n), rng.uniform(.3, .55, n), rng.uniform(14, 34, n),
        rng.uniform(-.3, .3, n), rng.uniform(.3, .55, n), rng.uniform(14, 34, n),
    ]).astype(float)
    check(conv, dst / "conversion", Xc)

    fg = export_calibrated_cv(src / "fg_prob_model.pkl", dst / "fg", extra_keys=("tail_slope",))
    dist = rng.uniform(18, 57, n)
    gust = rng.uniform(0, 30, n)
    temp = rng.uniform(10, 95, n)
    Xf = np.column_stack([
        dist, rng.integers(0, 2, n), gust, gust * dist, temp, temp * dist, rng.integers(0, 2, n),
        rng.uniform(.5, 1, n), rng.uniform(.5, 1, n), np.full(n, 60), rng.integers(0, 2, n),
        rng.uniform(0, 5300, n), rng.uniform(10, 600, n), rng.integers(-8, 9, n), np.ones(n),
    ]).astype(float)
    check(fg, dst / "fg", Xf)


if __name__ == "__main__":
    main()
