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


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        yardline_100 = int(data.get("yardline_100", 50))
        yards_to_go = int(data.get("yards_to_go", 5))
        score_differential = int(data.get("score_differential", 0))
        possession_number = int(data.get("possession_number", 1))
        opponent_result = data.get("opponent_result", None)
        is_playoffs = bool(data.get("is_playoffs", False))

        # Validation
        yardline_100 = max(1, min(99, yardline_100))
        yards_to_go = max(1, min(15, yards_to_go))
        score_differential = max(-21, min(21, score_differential))
        possession_number = max(1, min(3, possession_number))

        if possession_number != 2:
            opponent_result = None

        # Advanced settings: team quality EPA
        off_epa = float(data.get("off_epa", 0.0))
        def_epa = float(data.get("def_epa", 0.0))
        off_epa = max(-0.3, min(0.3, off_epa))
        def_epa = max(-0.3, min(0.3, def_epa))

        # Rolling offensive/defensive stats
        off_success_rate = max(0.20, min(0.65, float(data.get("off_success_rate", 0.42))))
        off_ppg = max(10.0, min(40.0, float(data.get("off_ppg", 23.0))))
        def_success_rate = max(0.20, min(0.65, float(data.get("def_success_rate", 0.42))))
        def_ppg = max(10.0, min(40.0, float(data.get("def_ppg", 23.0))))

        # Play type
        shotgun = int(bool(data.get("shotgun", 1)))
        no_huddle = int(bool(data.get("no_huddle", 0)))

        # Punt quality settings
        punt_distance_roll6 = data.get("punt_distance_roll6")
        inside_twenty_rate_roll6 = data.get("inside_twenty_rate_roll6")
        if punt_distance_roll6 is not None:
            punt_distance_roll6 = max(30.0, min(60.0, float(punt_distance_roll6)))
        if inside_twenty_rate_roll6 is not None:
            inside_twenty_rate_roll6 = max(0.0, min(1.0, float(inside_twenty_rate_roll6)))

        # Kicker quality
        fg_make_rate_roll6 = data.get("fg_make_rate_roll6")
        if fg_make_rate_roll6 is not None:
            fg_make_rate_roll6 = max(0.0, min(1.0, float(fg_make_rate_roll6)))

        # Game context
        is_home = data.get("is_home")  # None = neutral, True = home, False = away
        offense_timeouts = max(0, min(3, int(data.get("offense_timeouts", 2))))
        defense_timeouts = max(0, min(3, int(data.get("defense_timeouts", 2))))
        posteam_spread = max(-17.0, min(17.0, float(data.get("posteam_spread", 0.0))))

        # Weather & venue settings (used by FG model)
        is_dome = bool(data.get("is_dome", False))
        wind = max(0.0, min(50.0, float(data.get("wind", 8.0))))
        wind_gust = data.get("wind_gust")
        if wind_gust is not None:
            wind_gust = max(0.0, min(60.0, float(wind_gust)))
        temp = max(-10.0, min(120.0, float(data.get("temp", 65.0))))
        is_precipitation = bool(data.get("is_precipitation", False))
        surface_is_grass = bool(data.get("surface_is_grass", True))
        altitude_ft = max(0.0, min(8000.0, float(data.get("altitude_ft", 0.0))))

        result = analyze(
            yardline_100=yardline_100,
            yards_to_go=yards_to_go,
            score_differential=score_differential,
            possession_number=possession_number,
            opponent_result=opponent_result,
            is_playoffs=is_playoffs,
            off_epa=off_epa,
            def_epa=def_epa,
            off_success_rate=off_success_rate,
            off_ppg=off_ppg,
            def_success_rate=def_success_rate,
            def_ppg=def_ppg,
            shotgun=shotgun,
            no_huddle=no_huddle,
            punt_distance_roll6=punt_distance_roll6,
            inside_twenty_rate_roll6=inside_twenty_rate_roll6,
            fg_make_rate_roll6=fg_make_rate_roll6,
            offense_timeouts=offense_timeouts,
            defense_timeouts=defense_timeouts,
            is_home=is_home,
            posteam_spread=posteam_spread,
            is_dome=is_dome,
            wind=wind,
            wind_gust=wind_gust,
            temp=temp,
            is_precipitation=is_precipitation,
            surface_is_grass=surface_is_grass,
            altitude_ft=altitude_ft,
        )

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
