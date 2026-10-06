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
    const settingsTabs = document.querySelectorAll(".settings-tab");
    const tabStandard = document.getElementById("tab-standard");
    const tabAdvanced = document.getElementById("tab-advanced");
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

    // --- Settings tabs ---
    settingsTabs.forEach(tab => {
        tab.addEventListener("click", () => {
            settingsTabs.forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            const target = tab.dataset.tab;
            tabStandard.style.display = target === "standard" ? "block" : "none";
            tabAdvanced.style.display = target === "advanced" ? "block" : "none";
        });
    });

    // --- Transparency toggle ---
    const transToggle = document.getElementById("transparency-toggle");
    const transPanel = document.getElementById("transparency-panel");
    transToggle.addEventListener("click", () => {
        transToggle.classList.toggle("open");
        transPanel.classList.toggle("visible");
    });

    // --- Analyze: live as inputs change ---
    let requestId = 0;
    let debounceTimer;
    const scheduleAnalysis = () => { clearTimeout(debounceTimer); debounceTimer = setTimeout(runAnalysis, 280); };
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        await runAnalysis();
    });
    form.addEventListener("input", scheduleAnalysis);
    form.addEventListener("change", scheduleAnalysis);

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

        // Offensive & defensive rolling stats
        const off_success_rate = parseFloat(document.getElementById("adv-off-success-rate").value) || 0.42;
        const off_ppg = parseFloat(document.getElementById("adv-off-ppg").value) || 23.0;
        const def_success_rate = parseFloat(document.getElementById("adv-def-success-rate").value) || 0.42;
        const def_ppg = parseFloat(document.getElementById("adv-def-ppg").value) || 23.0;

        // Play type
        const shotgun = document.getElementById("adv-shotgun")?.checked ? 1 : 0;
        const no_huddle = document.getElementById("adv-no-huddle")?.checked ? 1 : 0;

        // Punt quality settings
        const puntDistEl = document.getElementById("adv-punt-dist");
        const inside20El = document.getElementById("adv-inside20");
        const punt_distance_roll6 = puntDistEl ? parseFloat(puntDistEl.value) || null : null;
        const inside_twenty_rate_roll6 = inside20El ? parseFloat(inside20El.value) || null : null;

        // Kicker quality
        const fgRateEl = document.getElementById("adv-fg-rate");
        const fg_make_rate_roll6 = fgRateEl && fgRateEl.value !== "" ? parseFloat(fgRateEl.value) : null;

        // Game context
        const homeVal = document.getElementById("adv-home")?.value || "neutral";
        const is_home = homeVal === "neutral" ? null : homeVal === "home";
        const offense_timeouts = parseInt(document.getElementById("adv-off-timeouts")?.value) ?? 2;
        const defense_timeouts = parseInt(document.getElementById("adv-def-timeouts")?.value) ?? 2;
        const posteam_spread = parseFloat(document.getElementById("adv-spread")?.value) || 0.0;

        // Weather & venue settings (used by FG model)
        const is_dome = document.getElementById("adv-dome")?.checked || false;
        const wind = parseFloat(document.getElementById("adv-wind")?.value) || 0;
        const windGustEl = document.getElementById("adv-wind-gust");
        const wind_gust = windGustEl && windGustEl.value !== "" ? parseFloat(windGustEl.value) : null;
        const temp = parseFloat(document.getElementById("adv-temp")?.value) || 65;
        const is_precipitation = document.getElementById("adv-precip")?.checked || false;
        const surface_is_grass = document.getElementById("adv-surface")?.checked ?? true;
        const altitude_ft = parseFloat(document.getElementById("adv-altitude")?.value) || 0;

        const payload = {
            yardline_100,
            yards_to_go,
            score_differential,
            possession_number,
            opponent_result,
            is_playoffs,
            off_epa,
            def_epa,
            off_success_rate,
            off_ppg,
            def_success_rate,
            def_ppg,
            shotgun,
            no_huddle,
            punt_distance_roll6,
            inside_twenty_rate_roll6,
            fg_make_rate_roll6,
            is_home,
            offense_timeouts,
            defense_timeouts,
            posteam_spread,
            is_dome,
            wind,
            wind_gust,
            temp,
            is_precipitation,
            surface_is_grass,
            altitude_ft,
        };

        // Keep the last result on screen while updating
        resultsContent.classList.add("updating");
        const ticket = ++requestId;

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
            if (ticket !== requestId) return; // a newer request is on its way
            placeholder.style.display = "none";
            renderResults(data);
        } catch (err) {
            placeholder.style.display = "flex";
            placeholder.innerHTML = "<p>Couldn’t reach the models (" + err.message + "). The free server may be waking up; try again in a few seconds.</p>";
        } finally {
            if (ticket === requestId) resultsContent.classList.remove("updating");
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
            const bar = cards[key].querySelector(".win-bar i");
            if (bar) bar.style.width = (wp[key] === null ? 0 : Math.max(0, Math.min(100, wp[key]))) + "%";
        });

        // Verdict in plain words
        const callText = { go: "Go for it", punt: "Punt", fg: "Kick the field goal" }[rec];
        const others = Object.entries(wp).filter(([k, v]) => k !== rec && v !== null).sort((x, y) => y[1] - x[1]);
        const otherName = { go: "going for it", punt: "punting", fg: "kicking" }[others[0][0]];
        document.getElementById("verdict-call").textContent = callText;
        document.getElementById("verdict-sub").textContent =
            `${wp[rec].toFixed(0)}% chance to win · ${data.margin.toFixed(1)} points better than ${otherName}` +
            (data.recommendation_strength === "Marginal" ? " (close call)" : "");
        document.querySelector(".verdict").dataset.call = rec;

        // Strength
        const strengthEl = document.getElementById("strength-value");
        strengthEl.textContent =
            `${data.recommendation_strength} · +${data.margin.toFixed(1)} pts over the next best`;
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

        renderSweep(data);

        // Show
        resultsContent.classList.add("active");
    }

    function renderDecisionContext(data) {
        // Only numbers the submodels actually produced; no rules of thumb.
        const c = document.getElementById("decision-context");
        const d = data.details, sd = data.submodel_details, wp = data.win_probabilities, rec = data.recommendation;
        const pct = (v) => (v === null || v === undefined ? "–" : v.toFixed(1) + "%");
        const row = (key, name, chance, a, b) => `
            <div class="bd-row ${key === rec ? "is-rec" : ""}">
                <div class="bd-name">${name}</div>
                <div class="bd-cell" data-label="Chance">${chance}</div>
                <div class="bd-cell" data-label="Win % if it works">${pct(a)}</div>
                <div class="bd-cell" data-label="Win % if not">${b === null ? "–" : pct(b)}</div>
            </div>`;
        let html = '<div class="bd-row bd-head"><div>Choice</div><div>Chance it works</div><div>Win % if it works</div><div>Win % if not</div></div>';
        html += row("go", "Go for it", pct(d.conversion_probability), sd.wp_if_convert, sd.wp_if_fail);
        html += data.fg_available
            ? row("fg", `Field goal <small>${d.fg_distance} yd</small>`, pct(d.fg_make_probability), sd.wp_if_fg_make, sd.wp_if_fg_miss)
            : `<div class="bd-row off"><div class="bd-name">Field goal <small>${d.fg_distance} yd</small></div><div class="bd-cell" data-label="">Out of range</div></div>`;
        html += row("punt", "Punt", `lands at ${d.punt_landing_yardline}`, sd.wp_if_punt, null);
        c.innerHTML = html;
    }

    function renderSweep(data) {
        const strip = document.getElementById("sweep-strip");
        if (!data.sweep) return;
        const here = data.inputs.yardline_100;
        strip.innerHTML = data.sweep.map((s) => {
            const label = s.yardline_100 === 50 ? "midfield" : s.yardline_100 < 50 ? `opp ${s.yardline_100}` : `own ${100 - s.yardline_100}`;
            const name = { go: "Go for it", fg: "Field goal", punt: "Punt" }[s.recommendation];
            return `<button type="button" class="cell ${s.recommendation} ${Math.abs(s.yardline_100 - here) <= 1 ? "here" : ""}" data-yl="${s.yardline_100}" title="${label}: ${name}" aria-label="${label}: ${name}"></button>`;
        }).join("");
        strip.querySelectorAll(".cell").forEach((b) => b.addEventListener("click", () => {
            yardSlider.value = b.dataset.yl;
            updateYardDisplay();
            runAnalysis();
        }));
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
                <div class="scenario-item"><span class="scenario-key">Method</span><span class="scenario-val">4 ML submodels</span></div>
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
                    <div class="step-line"><span class="step-num">1</span> Conversion model estimates <strong>${convProb}%</strong> chance of converting (XGBoost + empirical blending)</div>
                    <div class="step-line"><span class="step-num">2</span> If converted (${convProb}%): WP model evaluates state with 1st down at current spot</div>
                    <div class="step-line"><span class="step-num">3</span> If failed (${(100 - convProb).toFixed(1)}%): WP model evaluates opponent getting ball at their ${oppStart > 50 ? "own " + (100 - oppStart) : oppStart}</div>
                    <div class="step-line"><span class="step-num">4</span> Expected WP = weighted combination of both outcomes</div>
                    <div class="step-result">Result: <strong>${wp.go !== null ? wp.go.toFixed(1) : "--"}%</strong> expected win probability</div>
                </div>
            </div>

            <div class="step-option ${rec === 'punt' ? 'is-rec' : ''}">
                <div class="step-header">
                    <span class="step-icon">\uD83D\uDC4B</span>
                    <span class="step-title">Punt</span>
                    <span class="step-wp ${rec === 'punt' ? 'best' : ''}">${wp.punt !== null ? wp.punt.toFixed(1) + "%" : "N/A"}</span>
                </div>
                <div class="step-logic">
                    <div class="step-line"><span class="step-num">1</span> Punt model (XGBoost) predicts opponent starts at <strong>${puntLand}</strong></div>
                    <div class="step-line"><span class="step-num">2</span> WP model evaluates opponent's state from that field position</div>
                    <div class="step-line"><span class="step-num">3</span> Team's WP = 1 minus opponent's WP from that state</div>
                    <div class="step-result">Result: <strong>${wp.punt !== null ? wp.punt.toFixed(1) : "--"}%</strong> expected win probability</div>
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
                    <div class="step-line"><span class="step-num">2</span> FG model (XGBoost) estimates <strong>${fgProb}%</strong> make probability</div>
                    <div class="step-line"><span class="step-num">3</span> If made (${fgProb}%): WP model evaluates state with +3 pts, opponent receives kickoff</div>
                    <div class="step-line"><span class="step-num">4</span> If missed (${(100 - fgProb).toFixed(1)}%): WP model evaluates opponent at their 20 or spot of kick</div>
                    <div class="step-line"><span class="step-num">5</span> Expected WP = weighted combination of both outcomes</div>
                    <div class="step-result">Result: <strong>${wp.fg.toFixed(1)}%</strong> expected win probability</div>
            `;
        } else {
            stepsHTML += `
                    <div class="step-line"><span class="step-num">!</span> Kick distance of <strong>${fgDist} yards</strong> exceeds NFL record (66 yds) — not evaluated</div>
            `;
        }

        stepsHTML += `
                </div>
            </div>
        `;

        stepsEl.innerHTML = stepsHTML;
    }
    runAnalysis();
});
