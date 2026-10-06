"""
Flask server for the NFL OT 4th Down Decision Engine.
Run with: python server.py
"""

import os
import sys
import logging

from flask import Flask, render_template, request, jsonify

# Ensure the app directory is on the path so imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decision_engine import analyze

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Models are lazy-loaded on first /api/analyze request to keep startup fast.
# This lets Render's health check pass immediately on deploy.


@app.route("/")
def index():
    return render_template("index.html")


def _parse_inputs(data: dict) -> dict:
    """Validate the request body into keyword arguments for analyze()."""
    possession_number = max(1, min(3, int(data.get("possession_number", 1))))
    opponent_result = data.get("opponent_result") if possession_number == 2 else None

    def opt(key, lo, hi):
        v = data.get(key)
        return None if v in (None, "") else max(lo, min(hi, float(v)))

    return dict(
        yardline_100=max(1, min(99, int(data.get("yardline_100", 50)))),
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
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        kwargs = _parse_inputs(data)
        result = analyze(**kwargs)

        # Same situation at every 3rd yard line: the call for the whole field.
        if data.get("sweep", True):
            sweep = []
            for yl in range(1, 100, 3):
                r = analyze(**{**kwargs, "yardline_100": yl})
                sweep.append({"yardline_100": yl, "recommendation": r["recommendation"],
                              "margin": r["margin"]})
            result["sweep"] = sweep

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
