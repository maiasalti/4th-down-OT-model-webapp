"""
Print the engine's call across field positions and distances as a Markdown
table: a quick sanity check after touching any model.

    python tools/recommendation_table.py [--possession 1] [--opponent none|fg|td]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decision_engine import analyze_many  # noqa: E402

YARDLINES = [95, 90, 85, 80, 75, 70, 65, 60, 55, 50, 45, 40, 35, 30, 25, 20, 10, 5]
DISTANCES = [1, 2, 4, 7, 10, 15]


def label(yl: int) -> str:
    return f"own {100 - yl}" if yl > 50 else ("midfield" if yl == 50 else f"opp {yl}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--possession", type=int, default=1)
    ap.add_argument("--opponent", default=None, help="2nd possession: what the first team did")
    args = ap.parse_args()

    cells = {}
    for d in DISTANCES:
        yls = [y for y in YARDLINES if y >= d]
        for y, r in zip(yls, analyze_many(yls, yards_to_go=d, score_differential=0,
                                          possession_number=args.possession,
                                          opponent_result=args.opponent)):
            w = r["win_probabilities"]
            fg = f"{w['fg']:.0f}" if w["fg"] is not None else "–"
            cells[y, d] = f"**{r['recommendation'].upper()}** {w['go']:.0f}/{fg}/{w['punt']:.0f}"

    print("Cell = call, then WP% for go / FG / punt.\n")
    print("| Ball on | " + " | ".join(f"4th & {d}" for d in DISTANCES) + " |")
    print("|---|" + "---|" * len(DISTANCES))
    for y in YARDLINES:
        print(f"| {label(y)} | " + " | ".join(cells.get((y, d), "") for d in DISTANCES) + " |")


if __name__ == "__main__":
    main()
