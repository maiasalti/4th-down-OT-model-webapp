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

    // --- Transparency toggle ---
    const transToggle = document.getElementById("transparency-toggle");
    const transPanel = document.getElementById("transparency-panel");
    transToggle.addEventListener("click", () => {
        transToggle.classList.toggle("open");
        transPanel.classList.toggle("visible");
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

        // Advanced settings: EPA values
        const off_epa = parseFloat(document.getElementById("adv-off-epa").value) || 0.0;
        const def_epa = parseFloat(document.getElementById("adv-def-epa").value) || 0.0;

        const payload = {
            yardline_100,
            yards_to_go,
            score_differential,
            possession_number,
            opponent_result,
            is_playoffs,
            off_epa,
            def_epa,
        };

        // Show loading
        placeholder.style.display = "none";
        resultsContent.classList.remove("active");
        loadingEl.classList.add("active");
        analyzeBtn.disabled = true;
        analyzeBtn.textContent = "SIMULATING...";

        // Reset transparency
        transToggle.classList.remove("open");
        transPanel.classList.remove("visible");

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
        const inputs = data.inputs;

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

        // --- Decision Context ---
        renderDecisionContext(data);

        // --- Transparency Panel ---
        renderTransparency(data);

        // Show
        resultsContent.classList.add("active");
    }

    function renderDecisionContext(data) {
        const container = document.getElementById("decision-context");
        const wp = data.win_probabilities;
        const rec = data.recommendation;
        const details = data.details;
        const inputs = data.inputs;
        const margin = data.margin;
        const fgAvail = data.fg_available;

        const recLabels = { go: "Go For It", punt: "Punt", fg: "Field Goal" };
        let html = '<div class="context-title">Decision Context</div>';

        // 1. Break-even analysis
        const convProb = details.conversion_probability;
        if (rec === "go") {
            const secondBest = wp.punt >= (wp.fg || 0) ? "punting" : "kicking a FG";
            const secondWP = wp.punt >= (wp.fg || 0) ? wp.punt : wp.fg;
            html += `<div class="context-item">
                <div class="context-label">Why go for it wins</div>
                <div class="context-value">With a <strong>${convProb}%</strong> conversion rate at this distance, the upside of keeping the drive alive outweighs the risk of a turnover on downs. ${secondBest === "punting" ? "Punting" : "A field goal"} yields ${secondWP.toFixed(1)}% — <strong>${margin.toFixed(1)}pp lower</strong>.</div>
            </div>`;
        } else if (rec === "punt") {
            html += `<div class="context-item">
                <div class="context-label">Why punt wins</div>
                <div class="context-value">From this deep, pinning the opponent at <strong>${details.punt_landing_yardline}</strong> creates more value than a ${convProb}% conversion gamble. Failing on 4th down here would give the opponent excellent field position.</div>
            </div>`;
        } else if (rec === "fg") {
            html += `<div class="context-item">
                <div class="context-label">Why field goal wins</div>
                <div class="context-value">A <strong>${details.fg_make_probability}%</strong> make rate on a ${details.fg_distance}-yard kick gives better expected value than a ${convProb}% conversion attempt. Even a miss leaves the opponent at their 20 — similar to a punt.</div>
            </div>`;
        }

        // 2. What would flip the decision
        html += '<div class="context-item"><div class="context-label">What would change this</div><div class="context-value">';
        if (rec === "go") {
            if (fgAvail) {
                html += `If the conversion rate dropped below ~${Math.max(1, Math.round(convProb - margin/1.5))}%, <span class="warn">field goal</span> would become optimal. `;
            }
            html += `Moving ${Math.round(margin * 1.5 + 5)} yards deeper into your own territory would likely flip this to <span class="warn">punt</span>.`;
        } else if (rec === "punt") {
            const goGap = (wp.punt - wp.go).toFixed(1);
            html += `Moving ~${Math.round(parseFloat(goGap) * 2 + 8)} yards closer to the opponent's end zone would make <span class="warn">go for it</span> more attractive. `;
            if (inputs.yards_to_go > 2) {
                html += `Shorter yardage (4th & 1 or 2) would also shift toward going for it.`;
            }
        } else if (rec === "fg") {
            html += `If the kick distance were ${details.fg_distance + 10}+ yards, the make probability would drop enough to favor <span class="warn">${wp.go > wp.punt ? "going for it" : "punting"}</span>. `;
            html += `A shorter distance to go (4th & 1) would also favor going for it.`;
        }
        html += '</div></div>';

        // 3. NFL coaching tendency
        html += '<div class="context-item"><div class="context-label">How NFL coaches typically decide here</div><div class="context-value">';
        const ytg = inputs.yards_to_go;
        const yl = inputs.yardline_100;
        if (yl <= 5) {
            html += `This close to the end zone, most NFL coaches go for it regardless of distance — the model <strong>${rec === "go" ? "agrees" : "disagrees, favoring " + recLabels[rec].toLowerCase()}</strong>.`;
        } else if (yl <= 35 && fgAvail) {
            html += `In FG range, most coaches kick here. The model <strong>${rec === "fg" ? "agrees" : "disagrees — it sees more value in " + recLabels[rec].toLowerCase()}</strong>.`;
        } else if (yl >= 60) {
            html += `Deep in their own territory, NFL coaches almost always punt. The model <strong>${rec === "punt" ? "agrees" : "disagrees — the analytics favor " + recLabels[rec].toLowerCase()}</strong>.`;
        } else if (ytg <= 2) {
            html += `On 4th & short near midfield, analytics-minded coaches increasingly go for it. The model <strong>${rec === "go" ? "agrees — the conversion rate justifies the risk" : "sees more value in " + recLabels[rec].toLowerCase() + " here"}</strong>.`;
        } else {
            html += `Most NFL coaches would punt here. The model <strong>${rec === "punt" ? "agrees with conventional wisdom" : "disagrees — it favors " + recLabels[rec].toLowerCase() + " over the conventional punt"}</strong>.`;
        }
        html += '</div></div>';

        container.innerHTML = html;
    }

    function renderTransparency(data) {
        const inputs = data.inputs;
        const details = data.details;
        const wp = data.win_probabilities;
        const rec = data.recommendation;

        // Scenario summary
        let yardLabel;
        if (inputs.yardline_100 === 50) {
            yardLabel = "Midfield";
        } else if (inputs.yardline_100 > 50) {
            yardLabel = "own " + (100 - inputs.yardline_100);
        } else {
            yardLabel = "opponent's " + inputs.yardline_100;
        }

        const possLabels = { 1: "1st possession", 2: "2nd possession", 3: "sudden death" };
        const possLabel = possLabels[inputs.possession_number] || "possession " + inputs.possession_number;

        const scenarioEl = document.getElementById("transparency-scenario");
        scenarioEl.innerHTML = `
            <div class="scenario-grid">
                <div class="scenario-item"><span class="scenario-key">Field position</span><span class="scenario-val">${yardLabel}</span></div>
                <div class="scenario-item"><span class="scenario-key">Yards to go</span><span class="scenario-val">${inputs.yards_to_go}</span></div>
                <div class="scenario-item"><span class="scenario-key">Score diff</span><span class="scenario-val">${inputs.score_differential >= 0 ? "+" : ""}${inputs.score_differential}</span></div>
                <div class="scenario-item"><span class="scenario-key">OT phase</span><span class="scenario-val">${possLabel}</span></div>
                <div class="scenario-item"><span class="scenario-key">Game type</span><span class="scenario-val">${inputs.is_playoffs ? "playoffs" : "regular season"}</span></div>
                <div class="scenario-item"><span class="scenario-key">Simulations</span><span class="scenario-val">10,000 per option</span></div>
            </div>
        `;

        // Step-by-step for each option
        const stepsEl = document.getElementById("transparency-steps");
        const convProb = details.conversion_probability;
        const fgProb = details.fg_make_probability;
        const fgDist = details.fg_distance;
        const puntLand = details.punt_landing_yardline;
        const fgAvail = data.fg_available;

        const oppStart = 100 - inputs.yardline_100;

        let stepsHTML = `
            <div class="step-option ${rec === 'go' ? 'is-rec' : ''}">
                <div class="step-header">
                    <span class="step-icon">\u26A1</span>
                    <span class="step-title">Go For It</span>
                    <span class="step-wp ${rec === 'go' ? 'best' : ''}">${wp.go !== null ? wp.go.toFixed(1) + "%" : "N/A"}</span>
                </div>
                <div class="step-logic">
                    <div class="step-line"><span class="step-num">1</span> 4th down conversion model estimates <strong>${convProb}%</strong> chance of converting</div>
                    <div class="step-line"><span class="step-num">2</span> If converted (${convProb}% of sims): team continues drive with 1st down at current spot</div>
                    <div class="step-line"><span class="step-num">3</span> If failed (${(100 - convProb).toFixed(1)}% of sims): opponent gets ball at their ${oppStart > 50 ? "own " + (100 - oppStart) : oppStart}</div>
                    <div class="step-line"><span class="step-num">4</span> Remaining OT is simulated play-by-play for all 10,000 trials</div>
                    <div class="step-result">Result: team wins in <strong>${wp.go !== null ? wp.go.toFixed(1) : "--"}%</strong> of simulations</div>
                </div>
            </div>

            <div class="step-option ${rec === 'punt' ? 'is-rec' : ''}">
                <div class="step-header">
                    <span class="step-icon">\uD83D\uDC4B</span>
                    <span class="step-title">Punt</span>
                    <span class="step-wp ${rec === 'punt' ? 'best' : ''}">${wp.punt !== null ? wp.punt.toFixed(1) + "%" : "N/A"}</span>
                </div>
                <div class="step-logic">
                    <div class="step-line"><span class="step-num">1</span> Punt model predicts opponent starts at <strong>${puntLand}</strong></div>
                    <div class="step-line"><span class="step-num">2</span> Opponent gets ball at predicted position in all 10,000 sims</div>
                    <div class="step-line"><span class="step-num">3</span> Remaining OT is simulated play-by-play from opponent's drive onward</div>
                    <div class="step-result">Result: team wins in <strong>${wp.punt !== null ? wp.punt.toFixed(1) : "--"}%</strong> of simulations</div>
                </div>
            </div>

            <div class="step-option ${rec === 'fg' ? 'is-rec' : ''} ${!fgAvail ? 'unavailable' : ''}">
                <div class="step-header">
                    <span class="step-icon">\uD83C\uDFC8</span>
                    <span class="step-title">Field Goal</span>
                    <span class="step-wp ${rec === 'fg' ? 'best' : ''}">${wp.fg !== null ? wp.fg.toFixed(1) + "%" : "N/A"}</span>
                </div>
                <div class="step-logic">
        `;

        if (fgAvail) {
            stepsHTML += `
                    <div class="step-line"><span class="step-num">1</span> FG distance: <strong>${fgDist} yards</strong> (yardline + 17 for snap/endzone)</div>
                    <div class="step-line"><span class="step-num">2</span> FG model estimates <strong>${fgProb}%</strong> make probability at this distance</div>
                    <div class="step-line"><span class="step-num">3</span> If made (${fgProb}% of sims): team scores +3, opponent receives kickoff at their 25</div>
                    <div class="step-line"><span class="step-num">4</span> If missed (${(100 - fgProb).toFixed(1)}% of sims): opponent gets ball at their 20 or spot of kick</div>
                    <div class="step-line"><span class="step-num">5</span> Remaining OT simulated play-by-play for all 10,000 trials</div>
                    <div class="step-result">Result: team wins in <strong>${wp.fg.toFixed(1)}%</strong> of simulations</div>
            `;
        } else {
            stepsHTML += `
                    <div class="step-line"><span class="step-num">!</span> Kick distance of <strong>${fgDist} yards</strong> exceeds NFL record (66 yds) — not simulated</div>
            `;
        }

        stepsHTML += `
                </div>
            </div>
        `;

        stepsEl.innerHTML = stepsHTML;
    }
});
