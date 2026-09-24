/*
 * Lightweight custom date picker (no dependencies).
 * Usage: <input type="text" data-datepicker name="..." value="YYYY-MM-DD">
 * The real input keeps the ISO value (YYYY-MM-DD) for Django; the user sees
 * a friendly value like "17 Sep 2026" and a styled calendar popup.
 */
(function () {
  const MONTHS = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];
  const DAYS = ["Sa", "Su", "Mo", "Tu", "We", "Th", "Fr"]; // week starts Saturday (Bangladesh)
  const WEEK_START = 6; // JS getDay(): 6 = Saturday

  const pad = (n) => String(n).padStart(2, "0");
  const toISO = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const pretty = (d) => `${pad(d.getDate())} ${MONTHS[d.getMonth()].slice(0, 3)} ${d.getFullYear()}`;
  const sameDay = (a, b) => a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

  function parseISO(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((value || "").trim());
    if (!m) return null;
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    return d.getMonth() === +m[2] - 1 ? d : null;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function attach(input) {
    let selected = parseISO(input.value);
    let view = selected ? new Date(selected) : new Date();
    view.setDate(1);

    // visible field
    const wrap = el("div", "dp-wrap");
    input.parentNode.insertBefore(wrap, input);
    const display = el("button", "input dp-display");
    display.type = "button";
    display.setAttribute("aria-haspopup", "dialog");
    if (input.id) { display.id = input.id; input.removeAttribute("id"); }
    input.type = "hidden";
    wrap.appendChild(display);
    wrap.appendChild(input);

    const label = el("span", "dp-label");
    const icon = el("span", "dp-icon");
    icon.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3 10h18M8 3v4M16 3v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
    display.append(label, icon);

    // popup
    const pop = el("div", "dp-pop");
    pop.setAttribute("role", "dialog");
    pop.hidden = true;
    wrap.appendChild(pop);

    function renderLabel() {
      label.textContent = selected ? pretty(selected) : (input.getAttribute("placeholder") || "Select date");
      label.classList.toggle("dp-placeholder", !selected);
    }

    function setDate(d) {
      selected = d;
      input.value = d ? toISO(d) : "";
      input.dispatchEvent(new Event("change", { bubbles: true }));
      renderLabel();
    }

    function render() {
      pop.innerHTML = "";

      // header: prev | month select + year select | next
      const head = el("div", "dp-head");
      const prev = el("button", "dp-nav", "‹");
      const next = el("button", "dp-nav", "›");
      prev.type = next.type = "button";
      prev.setAttribute("aria-label", "Previous month");
      next.setAttribute("aria-label", "Next month");
      prev.onclick = () => { view.setMonth(view.getMonth() - 1); render(); };
      next.onclick = () => { view.setMonth(view.getMonth() + 1); render(); };

      const monthSel = el("select", "dp-select");
      MONTHS.forEach((m, i) => { const o = el("option", "", m); o.value = i; monthSel.appendChild(o); });
      monthSel.value = view.getMonth();
      monthSel.onchange = () => { view.setMonth(+monthSel.value); render(); };

      const yearSel = el("select", "dp-select");
      const thisYear = new Date().getFullYear();
      for (let y = thisYear + 5; y >= thisYear - 30; y--) {
        const o = el("option", "", y); o.value = y; yearSel.appendChild(o);
      }
      if (![...yearSel.options].some((o) => +o.value === view.getFullYear())) {
        const o = el("option", "", view.getFullYear()); o.value = view.getFullYear(); yearSel.appendChild(o);
      }
      yearSel.value = view.getFullYear();
      yearSel.onchange = () => { view.setFullYear(+yearSel.value); render(); };

      const selects = el("div", "dp-selects");
      selects.append(monthSel, yearSel);
      head.append(prev, selects, next);
      pop.appendChild(head);

      // grid
      const grid = el("div", "dp-grid");
      DAYS.forEach((d) => grid.appendChild(el("div", "dp-dow", d)));

      const first = new Date(view.getFullYear(), view.getMonth(), 1);
      const offset = (first.getDay() - WEEK_START + 7) % 7;
      const start = new Date(first);
      start.setDate(1 - offset);
      const today = new Date();

      for (let i = 0; i < 42; i++) {
        const day = new Date(start);
        day.setDate(start.getDate() + i);
        const btn = el("button", "dp-day", day.getDate());
        btn.type = "button";
        if (day.getMonth() !== view.getMonth()) btn.classList.add("dp-out");
        if (day.getDay() === 5) btn.classList.add("dp-weekend"); // Friday
        if (sameDay(day, today)) btn.classList.add("dp-today");
        if (sameDay(day, selected)) btn.classList.add("dp-selected");
        btn.onclick = () => { setDate(day); close(); };
        grid.appendChild(btn);
      }
      pop.appendChild(grid);

      // footer
      const foot = el("div", "dp-foot");
      const clear = el("button", "dp-link dp-clear", "Clear");
      const todayBtn = el("button", "dp-link", "Today");
      clear.type = todayBtn.type = "button";
      clear.onclick = () => { setDate(null); close(); };
      todayBtn.onclick = () => { setDate(new Date()); close(); };
      foot.append(clear, todayBtn);
      pop.appendChild(foot);
    }

    function open() {
      view = selected ? new Date(selected) : new Date();
      view.setDate(1);
      render();
      pop.hidden = false;
      wrap.classList.add("dp-open");
      const rect = display.getBoundingClientRect();
      pop.classList.toggle("dp-up", window.innerHeight - rect.bottom < 360 && rect.top > 360);
    }

    function close() {
      pop.hidden = true;
      wrap.classList.remove("dp-open");
    }

    display.addEventListener("click", () => (pop.hidden ? open() : close()));
    document.addEventListener("mousedown", (e) => { if (!wrap.contains(e.target)) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) { close(); display.focus(); } });

    // let other scripts set the date, e.g. input.setDate("2026-10-01") or input.setDate("")
    input.setDate = (iso) => {
      selected = parseISO(iso);
      input.value = selected ? toISO(selected) : "";
      renderLabel();
    };

    renderLabel();
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("input[data-datepicker]").forEach(attach);
  });
})();
