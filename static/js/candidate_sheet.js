/*
 * Candidate sheet (Interview Sheet + all marks).
 * Every graph is drawn from the JSON the server puts in #chart-data - real database values only.
 */
(function () {
  const data = JSON.parse(document.getElementById("chart-data").textContent);
  // Fixed order, never cycled: validated for colour-blind separation on white.
  // A 6th interviewer (very rare) falls back to neutral slate - the legend and the
  // marks table below the graphs still name every sheet.
  const PALETTE = ["#1d4ed8", "#d97706", "#0d9488", "#be123c", "#a855f7"];
  const OTHER = "#475569";
  const colour = (i) => PALETTE[i] || OTHER;
  const INK = "#0f172a", GRID = "#e2e8f0", MUTED = "#64748b";

  if (window.Chart) {
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.color = MUTED;
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
  }
  const axis = (max, title) => ({
    beginAtZero: true, max, grid: { color: GRID },
    ticks: { stepSize: max === 5 ? 1 : undefined, precision: 0 },
    title: title ? { display: true, text: title } : undefined,
  });

  function make(id, config) {
    const el = document.getElementById(id);
    if (!el || !window.Chart) return;
    new Chart(el, Object.assign({ options: {} }, config, {
      options: Object.assign({ responsive: true, maintainAspectRatio: false }, config.options || {}),
    }));
  }

  // 1. question-wise marks: one bar per interviewer, plus the average line
  if (data.interviewers.length) {
    make("chart-questions", {
      type: "bar",
      data: {
        labels: data.criteria,
        datasets: [
          ...data.interviewers.map((p, i) => ({
            label: p.name, data: p.marks, backgroundColor: colour(i) + "e6",
            borderRadius: 4, maxBarThickness: 26,
          })),
          { type: "line", label: "Average", data: data.averages, borderColor: INK, backgroundColor: INK,
            pointRadius: 4, tension: 0.25, spanGaps: true },
        ],
      },
      options: { scales: { y: axis(data.max_mark, "Mark (0-5)") }, plugins: { legend: { position: "bottom" } } },
    });

    // 2. each interviewer's total as a percentage
    make("chart-interviewers", {
      type: "bar",
      data: {
        labels: data.interviewers.map((p) => p.name),
        datasets: [{
          label: "Total %", data: data.interviewers.map((p) => p.percent),
          backgroundColor: data.interviewers.map((_, i) => colour(i)), borderRadius: 6,
        }],
      },
      options: {
        indexAxis: "y", scales: { x: axis(100, "%") }, plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (c) => {
            const p = data.interviewers[c.dataIndex];
            return ` ${p.total} / ${p.out_of}  (${p.percent}%)`;
          } } },
        },
      },
    });
  }

  // 3. written, interview and final result side by side
  const s = data.summary;
  if (s.interview !== null || s.written !== null) {
    const parts = [["Written exam", s.written, "#1d4ed8"], ["Interview", s.interview, "#0d9488"], ["Final combined", s.combined, "#d97706"]]
      .filter((p) => p[1] !== null);
    make("chart-summary", {
      type: "bar",
      data: { labels: parts.map((p) => p[0]), datasets: [{ data: parts.map((p) => p[1]), backgroundColor: parts.map((p) => p[2]), borderRadius: 6, maxBarThickness: 60 }] },
      options: { scales: { y: axis(100, "%") }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${c.raw}%` } } } },
    });
  }

  // 4. where the candidate stands in the department (their own band highlighted)
  const d = data.distribution;
  if (d.of) {
    make("chart-distribution", {
      type: "bar",
      data: {
        labels: d.labels,
        datasets: [{
          label: "Candidates", data: d.counts, borderRadius: 4,
          backgroundColor: d.counts.map((_, i) => (i === d.own_bin ? "#1d4ed8" : "#cbd5e1")),
        }],
      },
      options: {
        scales: { y: Object.assign(axis(undefined, "Candidates"), { ticks: { precision: 0 } }), x: { grid: { display: false } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          afterLabel: (c) => (c.dataIndex === d.own_bin ? "← this candidate" : ""),
        } } },
      },
    });
  }

  // ---------------------------------------------------------------- the marks table
  const table = document.getElementById("marks-table");
  if (table && window.DataTable && table.tBodies[0].rows.length) {
    new DataTable(table, {
      paging: table.tBodies[0].rows.length > 10, pageLength: 10, info: false, order: [],
      scrollX: true, columnDefs: [{ targets: "no-sort", orderable: false }],
      language: { search: "", searchPlaceholder: "Search interviewer…" },
    });
  }

  // ---------------------------------------------------------------- the Admin's own sheet: live total, whole numbers only
  const sheet = document.getElementById("my-evaluation");
  if (sheet) {
    const inputs = [...sheet.querySelectorAll(".eval-mark")];
    const total = sheet.querySelector("[data-eval-total]");
    sheet.querySelector("[data-eval-max]").textContent = inputs.reduce((sum, i) => sum + parseInt(i.dataset.max || 0, 10), 0);
    const sync = () => {
      let sum = 0;
      inputs.forEach((i) => {
        const v = i.value.trim();
        const bad = v !== "" && !/^\d+$/.test(v) || (v !== "" && +v > +i.dataset.max);
        i.classList.toggle("is-invalid", bad);
        if (!bad && v !== "") sum += parseInt(v, 10);
      });
      total.textContent = sum;
    };
    inputs.forEach((i) => {
      i.addEventListener("input", sync);
      i.addEventListener("keydown", (e) => { if ([".", ",", "e", "E", "-", "+"].includes(e.key)) e.preventDefault(); });
    });
    sync();
  }

  // ---------------------------------------------------------------- waiting reason only for "Waiting"
  // the status select lives in the bar at the top of the page (it posts with #interview-info)
  const status = document.querySelector(".status-bar select[name=status], #interview-info select[name=status]");
  const reason = document.getElementById("waiting-reason-field");
  if (status && reason) {
    const block = status.closest(".status-field") || status.closest(".status-bar");
    const toggle = () => {
      reason.hidden = status.value !== "waiting";
      const row = document.querySelector(".status-bar__main");
      if (row) row.classList.toggle("no-reason", reason.hidden);  // status takes the whole row
      if (block) {
        // the highlight follows the chosen status straight away
        block.className = block.className.replace(/\bs-\w+\b/g, "").trim() + " s-" + status.value;
        const now = block.querySelector(".status-field__now");
        if (now) now.textContent = "now: " + status.options[status.selectedIndex].text;
      }
    };
    status.addEventListener("change", toggle);
    if (window.jQuery) jQuery(status).on("change", toggle);
    toggle();
  }
})();
