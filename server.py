"""
Flask server for the NFL OT 4th Down Decision Engine.

    python server.py                         # dev
    gunicorn -c gunicorn.conf.py server:app  # prod (see Dockerfile)
"""

import hashlib
import logging
import os
import sys
import time

from flask import Flask, jsonify, render_template, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decision_engine import analyze_many  # noqa: E402
from models import preload_models  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Static files are fingerprinted (?v=<hash>), so browsers can keep them for a year.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000

_t = time.perf_counter()
preload_models()  # fast now that the models are native XGBoost; under --preload this runs once
logger.info("Models loaded in %.2fs", time.perf_counter() - _t)

SWEEP_YARDLINES = list(range(1, 100, 3))


def _fingerprint(name: str) -> str:
    with open(os.path.join(app.static_folder, name), "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:10]


ASSET_VERSIONS = {name: _fingerprint(name) for name in ("style.css", "app.js", "favicon.svg")}


@app.context_processor
def _asset_versions():
    return {"v": ASSET_VERSIONS}


@app.route("/")
def index():
    resp = app.make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


def _parse_inputs(data: dict) -> dict:
    """Validate the request body into keyword arguments for analyze_many()."""
    possession_number = max(1, min(3, int(data.get("possession_number", 1))))
    opponent_result = data.get("opponent_result") if possession_number == 2 else None

    def opt(key, lo, hi):
        v = data.get(key)
        return None if v in (None, "") else max(lo, min(hi, float(v)))

    return dict(
        yards_to_go=max(1, min(15, int(data.get("yards_to_go", 5)))),
        score_differential=max(-21, min(21, int(data.get("score_differential", 0)))),
        possession_number=possession_number,
        opponent_result=opponent_result,
        is_playoffs=bool(data.get("is_playoffs", False)),
        off_epa=max(-0.3, min(0.3, float(data.get("off_epa", 0.0)))),
        def_epa=max(-0.3, min(0.3, float(data.get("def_epa", 0.0)))),
        off_success_rate=max(0.20, min(0.65, float(data.get("off_success_rate", 0.42)))),
        off_ppg=max(10.0, min(40.0, float(data.get("off_ppg", 23.0)))),
        def_success_rate=max(0.20, min(0.65, float(data.get("def_success_rate", 0.42)))),
        def_ppg=max(10.0, min(40.0, float(data.get("def_ppg", 23.0)))),
        shotgun=int(bool(data.get("shotgun", 1))),
        no_huddle=int(bool(data.get("no_huddle", 0))),
        punt_distance_roll6=opt("punt_distance_roll6", 30.0, 60.0),
        inside_twenty_rate_roll6=opt("inside_twenty_rate_roll6", 0.0, 1.0),
        fg_make_rate_roll6=opt("fg_make_rate_roll6", 0.0, 1.0),
        offense_timeouts=max(0, min(3, int(data.get("offense_timeouts", 2)))),
        defense_timeouts=max(0, min(3, int(data.get("defense_timeouts", 2)))),
        is_home=data.get("is_home"),
        posteam_spread=max(-17.0, min(17.0, float(data.get("posteam_spread", 0.0)))),
        is_dome=bool(data.get("is_dome", False)),
        wind=max(0.0, min(50.0, float(data.get("wind", 8.0)))),
        wind_gust=opt("wind_gust", 0.0, 60.0),
        temp=max(-10.0, min(120.0, float(data.get("temp", 65.0)))),
        is_precipitation=bool(data.get("is_precipitation", False)),
        surface_is_grass=bool(data.get("surface_is_grass", True)),
        altitude_ft=max(0.0, min(8000.0, float(data.get("altitude_ft", 0.0)))),
    )


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        kwargs = _parse_inputs(data)
        yardline = max(1, min(99, int(data.get("yardline_100", 50))))
    except (TypeError, ValueError) as e:
        return jsonify({"error": f"Bad input: {e}"}), 400

    try:
        # The situation itself plus the same call at every 3rd yard line,
        # all scored in one vectorised pass.
        sweep = SWEEP_YARDLINES if data.get("sweep", True) else []
        results = analyze_many([yardline] + sweep, **kwargs)
        result = results[0]
        if sweep:
            result["sweep"] = [
                {"yardline_100": yl, "recommendation": r["recommendation"], "margin": r["margin"]}
                for yl, r in zip(sweep, results[1:])
            ]
        return jsonify(result)
    except Exception as e:
        logger.exception("Error in /api/analyze")
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
