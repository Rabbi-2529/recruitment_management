/*
 * Written exam page.
 *  - Exam Rules popup (also on the start page)
 *  - countdown: the sheet is handed in automatically when the time is over
 *  - answers are saved every few seconds, so a refresh does not lose them
 *  - Submit button with an in-page confirmation (native dialogs would count as leaving the page)
 *  - strict mode: switching tab, minimising, another window or Print Screen hands the sheet in at once;
 *    copy, paste, right-click, printing and developer shortcuts are blocked
 */
(function () {
  // ------------------------------------------------------------ rules popup
  const rules = document.getElementById("rules-dialog");
  if (rules) {
    document.querySelectorAll("[data-open-rules]").forEach((b) => b.addEventListener("click", () => rules.showModal()));
    rules.querySelectorAll("[data-close-rules]").forEach((b) => b.addEventListener("click", () => rules.close()));
    rules.addEventListener("click", (e) => { if (e.target === rules) rules.close(); });
  }

  const form = document.getElementById("exam-form");
  if (!form) return; // start page: only the rules popup

  const STRICT = form.dataset.strict === "1";
  const saveUrl = form.dataset.saveUrl;
  const leaveUrl = form.dataset.leaveUrl;
  const doneUrl = form.dataset.doneUrl;
  const saveState = document.getElementById("save-state");

  let finished = false;   // sheet handed in (submit, time over or rule break)
  let submitting = false; // our own form submit is navigating away
  let unloading = false;  // refresh / close in progress (not a tab switch)

  // ------------------------------------------------------------ autosave
  let saveTimer = null;
  async function save() {
    if (finished || submitting) return;
    try {
      const res = await fetch(saveUrl, { method: "POST", body: new FormData(form), credentials: "same-origin" });
      const json = await res.json();
      if (json.closed) { finished = true; location.replace(json.redirect || doneUrl); return; }
      if (typeof json.seconds_left === "number") secondsLeft = json.seconds_left; // keep the clock in step
      if (saveState) saveState.textContent = "Saved " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (err) {
      if (saveState) saveState.textContent = "Not saved - check your internet connection.";
    }
  }
  const saveSoon = () => { clearTimeout(saveTimer); saveTimer = setTimeout(save, 1000); };
  form.addEventListener("input", saveSoon);
  form.addEventListener("change", saveSoon);
  setInterval(save, 15000);

  // ------------------------------------------------------------ submit
  function submitNow() {
    if (finished) return;
    finished = submitting = true;
    form.submit();
  }

  const confirmDialog = document.getElementById("confirm-dialog");
  document.getElementById("submit-exam").addEventListener("click", () => {
    const cards = [...form.querySelectorAll(".question-card")];
    const open = cards.filter((card) => {
      const text = card.querySelector("textarea");
      return text ? !text.value.trim() : !card.querySelector("input[type=radio]:checked");
    }).length;
    document.getElementById("unanswered-note").textContent =
      open ? `${open} question${open > 1 ? "s are" : " is"} still unanswered.` : "All questions are answered.";
    confirmDialog.showModal();
  });
  confirmDialog.querySelector("[data-cancel]").addEventListener("click", () => confirmDialog.close());
  confirmDialog.querySelector("[data-confirm]").addEventListener("click", () => { confirmDialog.close(); submitNow(); });

  // ------------------------------------------------------------ timer
  const timer = document.getElementById("exam-timer");
  let secondsLeft = timer ? parseInt(timer.dataset.seconds, 10) : null;
  if (timer) {
    const label = document.getElementById("time-left");
    const tick = () => {
      if (finished) return;
      if (secondsLeft <= 0) {
        label.textContent = "00:00";
        showClosed("Time is over. Your answers are being submitted.");
        submitNow(); // hand in whatever is answered
        return;
      }
      const m = String(Math.floor(secondsLeft / 60)).padStart(2, "0");
      const s = String(secondsLeft % 60).padStart(2, "0");
      label.textContent = `${m}:${s}`;
      timer.classList.toggle("is-low", secondsLeft <= 60);
      secondsLeft -= 1;
      setTimeout(tick, 1000);
    };
    tick();
  }

  function showClosed(message) {
    const overlay = document.getElementById("closed-overlay");
    document.getElementById("closed-reason").textContent = message;
    overlay.hidden = false;
  }

  // ------------------------------------------------------------ leaving the page
  const REASON_TEXT = {
    tab_switch: "You left the exam page, so your answers were submitted.",
    window_blur: "You opened another window, so your answers were submitted.",
    screenshot: "A screenshot key was pressed, so your answers were submitted.",
  };

  function reportLeave(reason) {
    if (finished || submitting || unloading) return;
    const data = new FormData(form);
    data.append("reason", reason);
    if (!STRICT) { navigator.sendBeacon(leaveUrl, data); return; } // only noted for HR
    finished = true;
    navigator.sendBeacon(leaveUrl, data); // still delivered while the tab is hidden
    showClosed(REASON_TEXT[reason] || REASON_TEXT.tab_switch);
    setTimeout(() => location.replace(doneUrl), 800);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") reportLeave("tab_switch");
  });
  window.addEventListener("blur", () => {
    // another window / browser / app took the focus (our own popups never do)
    setTimeout(() => { if (!document.hasFocus()) reportLeave("window_blur"); }, 300);
  });

  window.addEventListener("beforeunload", (e) => {
    if (finished || submitting) return;
    unloading = true; // a refresh or close is not a tab switch - answers are already saved
    save();
    setTimeout(() => { unloading = false; }, 2000); // the candidate pressed "Stay on page"
    if (STRICT) { e.preventDefault(); e.returnValue = ""; }
  });

  if (!STRICT) return;

  // ------------------------------------------------------------ strict mode: block copying & screenshots
  const block = (e) => e.preventDefault();
  ["copy", "cut", "paste", "contextmenu", "dragstart", "drop"].forEach((type) => document.addEventListener(type, block));
  document.addEventListener("selectstart", (e) => { if (!e.target.closest || !e.target.closest("textarea")) e.preventDefault(); });

  document.addEventListener("keydown", (e) => {
    const key = (e.key || "").toLowerCase();
    const ctrl = e.ctrlKey || e.metaKey;
    const blocked =
      (ctrl && ["c", "v", "x", "a", "p", "s", "u", "r"].includes(key)) ||
      (ctrl && e.shiftKey && ["i", "j", "c", "k", "s"].includes(key)) ||
      key === "f12" || key === "f5";
    if (blocked) { e.preventDefault(); e.stopPropagation(); }
  }, true);

  document.addEventListener("keyup", (e) => {
    if (e.key === "PrintScreen") {
      try { navigator.clipboard.writeText(""); } catch (err) { /* not allowed on every browser */ }
      reportLeave("screenshot");
    }
  });
})();
