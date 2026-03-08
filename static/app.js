// ==========================================================================
//  NFL OT 4th Down Decision Engine — Frontend
// ==========================================================================

document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("analysis-form");
    const yardSlider = document.getElementById("yardline");
    const yardDisplay = document.getElementById("yard-display");
    const fieldMarker = document.getElementById("field-marker");
    const possRadios = document.querySelectorAll('input[name="possession"]');
    const oppResult = document.getElementById("opponent-result-group");
    const advToggle = document.getElementById("advanced-toggle");
    const advSettings = document.getElementById("advanced-settings");
    const analyzeBtn = document.getElementById("analyze-btn");
    const loadingEl = document.getElementById("loading");
    const resultsContent = document.getElementById("results-content");
    const placeholder = document.getElementById("results-placeholder");

    // --- Yard line slider ---
    function updateYardDisplay() {
        const val = parseInt(yardSlider.value);
        let label;
        if (val === 50) {
            label = "Midfield (50)";
        } else if (val > 50) {
            label = `Your own ${100 - val}`;
        } else {
            label = `Opponent's ${val}`;
        }
        yardDisplay.textContent = label;

        // Update field marker
        // yardline_100: 1 = near opponent EZ (left side), 99 = near own EZ (right side)
        // Visual: left = opponent EZ, right = own EZ
        // So marker left% should equal yardline_100%
        const pct = val;
        fieldMarker.style.left = `${pct}%`;
    }

    yardSlider.addEventListener("input", updateYardDisplay);
    updateYardDisplay();

    // --- Possession toggle ---
    function checkPossession() {
        const selected = document.querySelector('input[name="possession"]:checked');
        if (selected && selected.value === "2") {
            oppResult.classList.add("visible");
        } else {
            oppResult.classList.remove("visible");
        }
    }

    possRadios.forEach((r) => r.addEventListener("change", checkPossession));
    checkPossession();

    // --- Advanced settings ---
    advToggle.addEventListener("click", () => {
        advToggle.classList.toggle("open");
        advSettings.classList.toggle("visible");
    });

    // --- Analyze ---
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        await runAnalysis();
    });

    async function runAnalysis() {
        const yardline_100 = parseInt(yardSlider.value);
        const yards_to_go = parseInt(document.getElementById("yards-to-go").value);
        const score_differential = parseInt(
            document.getElementById("score-diff").value
        );
        const possession_number = parseInt(
            document.querySelector('input[name="possession"]:checked').value
        );
        const gameTypeRadio = document.querySelector('input[name="game-type"]:checked');
        const is_playoffs = gameTypeRadio ? gameTypeRadio.value === "playoffs" : false;

        let opponent_result = null;
        if (possession_number === 2) {
            const oppRadio = document.querySelector(
                'input[name="opp-result"]:checked'
            );
            if (oppRadio) {
                opponent_result = oppRadio.value;
            }
        }

        const payload = {
            yardline_100,
            yards_to_go,
            score_differential,
            possession_number,
            opponent_result,
            is_playoffs,
        };

        // Show loading
        placeholder.style.display = "none";
        resultsContent.classList.remove("active");
        loadingEl.classList.add("active");
        analyzeBtn.disabled = true;
        analyzeBtn.textContent = "SIMULATING...";

        try {
            const resp = await fetch("/api/analyze", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.error || "Server error");
            }

            const data = await resp.json();
            renderResults(data);
        } catch (err) {
            alert("Analysis failed: " + err.message);
            placeholder.style.display = "flex";
        } finally {
            loadingEl.classList.remove("active");
            analyzeBtn.disabled = false;
            analyzeBtn.textContent = "ANALYZE";
        }
    }

    function renderResults(data) {
        const wp = data.win_probabilities;
        const rec = data.recommendation;
        const details = data.details;

        // Cards
        const cards = {
            go: document.getElementById("card-go"),
            punt: document.getElementById("card-punt"),
            fg: document.getElementById("card-fg"),
        };

        const probs = {
            go: document.getElementById("prob-go"),
            punt: document.getElementById("prob-punt"),
            fg: document.getElementById("prob-fg"),
        };

        Object.keys(cards).forEach((key) => {
            cards[key].classList.remove("recommended", "unavailable");
            if (key === rec) {
                cards[key].classList.add("recommended");
            }
            if (wp[key] === null) {
                probs[key].textContent = "N/A";
                cards[key].classList.add("unavailable");
            } else {
                probs[key].textContent = wp[key].toFixed(1) + "%";
            }
        });

        // Strength
        const strengthEl = document.getElementById("strength-value");
        strengthEl.textContent =
            data.recommendation_strength +
            ` (${data.margin.toFixed(1)}pp margin)`;
        strengthEl.className = "strength-value";
        if (data.recommendation_strength === "Strong") {
            strengthEl.classList.add("strong");
        } else if (data.recommendation_strength === "Moderate") {
            strengthEl.classList.add("moderate");
        } else {
            strengthEl.classList.add("marginal");
        }

        // Details
        document.getElementById("detail-conv").textContent =
            details.conversion_probability + "%";
        document.getElementById("detail-fg").textContent =
            details.fg_make_probability !== null
                ? details.fg_make_probability + "% (" + details.fg_distance + " yds)"
                : "Out of range (" + details.fg_distance + " yds)";
        document.getElementById("detail-punt").textContent =
            details.punt_landing_yardline;

        // Show
        resultsContent.classList.add("active");
    }
});
