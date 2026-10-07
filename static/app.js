"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const NAMES = { go: "Go for it", fg: "Field goal", punt: "Punt" };
  const SHORT = { go: "going for it", fg: "kicking", punt: "punting" };

  const state = { yl: 50, ytg: 5 };
  let reqId = 0;
  let timer = null;
  let lastCall = null;

  // ---------- field drawing ----------
  const SVGNS = "http://www.w3.org/2000/svg";
  const xOf = (yl) => 100 + (100 - yl) * 10; // own goal on the left, driving right

  function drawField() {
    const g = $("yard-lines");
    const add = (tag, attrs, text) => {
      const el = document.createElementNS(SVGNS, tag);
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
      if (text) el.textContent = text;
      g.appendChild(el);
    };
    for (let y = 0; y <= 100; y += 5) {
      const x = 100 + y * 10;
      add("line", { x1: x, x2: x, y1: 0, y2: 260, class: y === 0 || y === 100 ? "yl goal" : "yl" });
    }
    for (let y = 1; y < 100; y++) {
      if (y % 5 === 0) continue;
      const x = 100 + y * 10;
      add("line", { x1: x, x2: x, y1: 4, y2: 16, class: "hash" });
      add("line", { x1: x, x2: x, y1: 244, y2: 256, class: "hash" });
      add("line", { x1: x, x2: x, y1: 96, y2: 106, class: "hash" });
      add("line", { x1: x, x2: x, y1: 154, y2: 164, class: "hash" });
    }
    for (let y = 10; y <= 90; y += 10) {
      const n = y <= 50 ? y : 100 - y;
      const x = 100 + y * 10;
      add("text", { x, y: 62, class: "ynum" }, String(n));
      add("text", { x, y: 222, class: "ynum", transform: `rotate(180 ${x} 212)` }, String(n));
    }
  }

  const spotText = (yl) => (yl === 50 ? "midfield" : yl > 50 ? `own ${100 - yl}` : `opp ${yl}`);
  const spotCaps = (yl) => { const s = spotText(yl); return s[0].toUpperCase() + s.slice(1); };

  function paintSituation() {
    const { yl } = state;
    const ytg = Math.min(state.ytg, yl);
    const x = xOf(yl);
    const xg = xOf(Math.max(0, yl - ytg));
    $("ball").setAttribute("transform", `translate(${x} 130)`);
    for (const id of ["los"]) { $(id).setAttribute("x1", x); $(id).setAttribute("x2", x); }
    $("ltg").setAttribute("x1", xg); $("ltg").setAttribute("x2", xg);
    $("gain-zone").setAttribute("x", x); $("gain-zone").setAttribute("width", Math.max(0, xg - x));
    $("yardline").value = yl;
    $("spot-label").textContent = spotText(yl);
    const goal = ytg >= yl;
    $("ytg-val").textContent = goal ? "goal" : ytg;
    $("ytg-minus").disabled = state.ytg <= 1;
    $("ytg-plus").disabled = state.ytg >= 15 || state.ytg >= yl;

    const poss = document.querySelector("input[name=possession]:checked").value;
    const opp = document.querySelector("input[name=opp-result]:checked").value;
    $("opponent-result-group").hidden = poss !== "2";
    $("bug-poss").textContent = poss === "1" ? "1st poss" : poss === "2" ? "2nd poss" : "Sudden death";
    $("bug-score").textContent = poss === "2" && opp === "td" ? "Down 7" : poss === "2" && opp === "fg" ? "Down 3" : "Tied";
    $("bug-dd").textContent = `4th & ${goal ? "goal" : ytg}`;
    $("bug-spot").textContent = spotCaps(yl);
  }

  // ---------- inputs ----------
  const num = (id, dflt) => { const v = parseFloat($(id).value); return Number.isFinite(v) ? v : dflt; };
  const optNum = (id) => { const v = $(id).value; return v === "" ? null : parseFloat(v); };

  function payload() {
    const poss = parseInt(document.querySelector("input[name=possession]:checked").value, 10);
    const home = $("adv-home").value;
    return {
      yardline_100: state.yl,
      yards_to_go: Math.min(state.ytg, state.yl),
      possession_number: poss,
      opponent_result: poss === 2 ? document.querySelector("input[name=opp-result]:checked").value : null,
      off_epa: num("adv-off-epa", 0), def_epa: num("adv-def-epa", 0),
      off_success_rate: num("adv-off-success-rate", 0.42), off_ppg: num("adv-off-ppg", 23),
      def_success_rate: num("adv-def-success-rate", 0.42), def_ppg: num("adv-def-ppg", 23),
      shotgun: $("adv-shotgun").checked ? 1 : 0, no_huddle: $("adv-no-huddle").checked ? 1 : 0,
      punt_distance_roll6: optNum("adv-punt-dist"), inside_twenty_rate_roll6: optNum("adv-inside20"),
      fg_make_rate_roll6: optNum("adv-fg-rate"),
      is_home: home === "neutral" ? null : home === "home",
      offense_timeouts: parseInt($("adv-off-timeouts").value, 10),
      defense_timeouts: parseInt($("adv-def-timeouts").value, 10),
      posteam_spread: num("adv-spread", 0),
      is_dome: $("adv-dome").checked, wind: num("adv-wind", 8), wind_gust: optNum("adv-wind-gust"),
      temp: num("adv-temp", 65), is_precipitation: $("adv-precip").checked,
      surface_is_grass: $("adv-surface").checked, altitude_ft: num("adv-altitude", 0),
    };
  }

  function schedule(delay = 140) {
    paintSituation();
    clearTimeout(timer);
    timer = setTimeout(analyze, delay);
  }

  async function analyze() {
    const ticket = ++reqId;
    const board = $("board");
    board.setAttribute("aria-busy", "true");
    const slow = setTimeout(() => {
      if (ticket === reqId && !lastCall) $("call-sub").textContent = "Waking up the free server. This only happens after it has been idle.";
    }, 2500);
    try {
      let resp;
      for (let attempt = 0; ; attempt++) {
        try {
          resp = await fetch("/api/analyze", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload()),
          });
          if (resp.status < 500 || attempt >= 2) break;
        } catch (e) {
          if (attempt >= 2) throw e;
        }
        await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
      }
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `server said ${resp.status}`);
      if (ticket !== reqId) return;
      $("error").hidden = true;
      render(data);
    } catch (err) {
      if (ticket !== reqId) return;
      $("error").textContent = `Couldn't get a call (${err.message}). Change any input to try again.`;
      $("error").hidden = false;
    } finally {
      clearTimeout(slow);
      if (ticket === reqId) board.setAttribute("aria-busy", "false");
    }
  }

  // ---------- rendering ----------
  const pct = (v) => (v == null ? "–" : (Math.round(v * 10) / 10).toFixed(1));
  const whole = (v) => `${Math.round(v)}%`;

  function render(d) {
    const wp = d.win_probabilities;
    const det = d.details;
    const sm = d.submodel_details;
    const rec = d.recommendation;
    const yl = d.inputs.yardline_100;

    // the call
    const word = $("call-word");
    word.textContent = NAMES[rec];
    if (rec !== lastCall) {
      const h = $("call-h");
      h.classList.remove("flip"); void h.offsetWidth; h.classList.add("flip");
      lastCall = rec;
    }
    const ranked = Object.entries(wp).filter(([, v]) => v != null).sort((a, b) => b[1] - a[1]);
    const runner = ranked[1];
    const strength = d.margin >= 3 ? "Clear call" : d.margin >= 1 ? "Lean" : "Coin flip";
    $("call-sub").innerHTML =
      `<b>${strength}.</b> ${whole(wp[rec])} to win, ${d.margin.toFixed(1)} points better than ${SHORT[runner[0]]}.`;

    // option rows
    const notes = {
      go: det.conversion_is_touchdown ? `${whole(det.conversion_probability)} to score` : `${whole(det.conversion_probability)} to convert`,
      fg: wp.fg == null ? `${det.fg_distance} yd: out of range` : `${det.fg_distance} yd · ${whole(det.fg_make_probability)} to make`,
      punt: `${Math.round(det.expected_punt_net)} yd net · they start at ${Math.round(det.punt_opponent_yardline_100) > 50 ? "their own " + (100 - Math.round(det.punt_opponent_yardline_100)) : "your " + Math.round(det.punt_opponent_yardline_100)}`,
    };
    for (const li of document.querySelectorAll(".opt")) {
      const k = li.dataset.opt;
      const v = wp[k];
      li.classList.toggle("best", k === rec);
      li.classList.toggle("off", v == null);
      li.querySelector("[data-wp]").textContent = v == null ? "–" : pct(v);
      li.querySelector("[data-bar]").style.transform = `scaleX(${v == null ? 0 : v / 100})`;
      li.querySelector("[data-note]").textContent = notes[k];
    }

    // why: the outcome tree
    const rows = [];
    const failSpot = 100 - yl;
    const turnover = (s) => {
      const r = Math.round(s);
      return r === 50 ? "they take over at midfield" : r > 50 ? `they take over at their own ${100 - r}` : `they take over at your ${r}`;
    };
    rows.push(["go", "Converts", det.conversion_probability, sm.wp_if_convert, det.conversion_is_touchdown ? "touchdown" : null]);
    rows.push(["go", `Stopped: ${turnover(failSpot)}`, 100 - det.conversion_probability, sm.wp_if_fail]);
    if (wp.fg != null) {
      rows.push(["fg", `Good from ${det.fg_distance}`, det.fg_make_probability, sm.wp_if_fg_make]);
      rows.push(["fg", "Missed", 100 - det.fg_make_probability, sm.wp_if_fg_miss]);
    }
    rows.push(["punt", turnover(det.punt_opponent_yardline_100).replace(/^they/, "They"), 100, sm.wp_if_punt]);
    const body = $("tree-body");
    body.innerHTML = "";
    let prev = null;
    for (const [k, outcome, chance, after] of rows) {
      const tr = document.createElement("tr");
      if (k !== prev) tr.className = "first" + (k === rec ? " best-row" : "");
      const c1 = document.createElement("td");
      c1.className = "choice";
      c1.textContent = k !== prev ? `${NAMES[k]} · ${whole(wp[k])}` : "";
      const c2 = document.createElement("td"); c2.textContent = outcome;
      const c3 = document.createElement("td"); c3.className = "n"; c3.textContent = k === "punt" ? "" : whole(chance);
      const c4 = document.createElement("td"); c4.className = "n wp"; c4.textContent = whole(after);
      tr.append(c1, c2, c3, c4);
      body.appendChild(tr);
      prev = k;
    }

    // sweep: own goal on the left, like the field
    const strip = $("sweep-strip");
    strip.innerHTML = "";
    const sweep = (d.sweep || []).slice().sort((a, b) => b.yardline_100 - a.yardline_100);
    for (const s of sweep) {
      const c = document.createElement("div");
      c.className = `seg-c seg-${s.recommendation}`;
      c.style.flex = "1";
      c.title = `${spotCaps(s.yardline_100)}: ${NAMES[s.recommendation]}`;
      strip.appendChild(c);
    }
    const here = document.createElement("div");
    here.className = "here";
    here.style.left = `${((100 - yl) / 99) * 100}%`;
    strip.appendChild(here);
  }

  // ---------- wiring ----------
  function setYl(v) { state.yl = Math.max(1, Math.min(99, Math.round(v))); schedule(); }

  function wire() {
    const field = $("field");
    const toYl = (ev) => {
      const r = field.getBoundingClientRect();
      const x = ((ev.clientX - r.left) / r.width) * 1200;
      return 100 - (x - 100) / 10;
    };
    field.addEventListener("pointerdown", (ev) => {
      field.setPointerCapture(ev.pointerId);
      field.classList.add("dragging");
      setYl(toYl(ev));
    });
    field.addEventListener("pointermove", (ev) => { if (field.hasPointerCapture(ev.pointerId)) setYl(toYl(ev)); });
    field.addEventListener("pointerup", () => field.classList.remove("dragging"));
    $("yardline").addEventListener("input", (e) => setYl(+e.target.value));
    $("ytg-minus").addEventListener("click", () => { state.ytg = Math.max(1, Math.min(state.ytg, state.yl) - 1); schedule(); });
    $("ytg-plus").addEventListener("click", () => { state.ytg = Math.min(15, state.ytg + 1); schedule(); });
    document.querySelectorAll("input[name=possession], input[name=opp-result]").forEach((el) => el.addEventListener("change", () => schedule(0)));
    $("adv").addEventListener("input", () => schedule(350));
    $("adv").addEventListener("change", () => schedule(0));
    window.__setSituation = (s) => {
      if (s.yardline_100) state.yl = s.yardline_100;
      if (s.yards_to_go) state.ytg = s.yards_to_go;
      if (s.possession) document.getElementById(`poss-${s.possession}`).checked = true;
      if (s.opp) document.getElementById(`opp-${s.opp}`).checked = true;
      schedule(0);
    };
  }

  drawField();
  wire();
  paintSituation();
  analyze();
})();
