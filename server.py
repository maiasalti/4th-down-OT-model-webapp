"""
Flask server for the NFL OT 4th Down Decision Engine.
Run with: python server.py
"""

import os
import sys

from flask import Flask, render_template, request, jsonify

# Ensure the app directory is on the path so imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decision_engine import analyze

app = Flask(__name__)


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

        result = analyze(
            yardline_100=yardline_100,
            yards_to_go=yards_to_go,
            score_differential=score_differential,
            possession_number=possession_number,
            opponent_result=opponent_result,
            is_playoffs=is_playoffs,
        )

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
