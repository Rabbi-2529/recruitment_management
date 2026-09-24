/*
 * Interview Call pages.
 *  - placeholder chips insert {candidate_name} etc. into the SMS message
 *  - live preview of the SMS for one person (rendered on the server - no API key in the page)
 *  - select people and send: the server queues one SMS per person, then this page asks it to send
 *    them in small batches until nothing is pending, updating the counters as it goes
 */
(function () {
  // ------------------------------------------------------------ placeholders
  const template = document.querySelector("[data-sms-template]");
  document.querySelectorAll("[data-insert]").forEach((chip) => {
    chip.addEventListener("click", () => {
      if (!template) return;
      const start = template.selectionStart ?? template.value.length;
      const end = template.selectionEnd ?? template.value.length;
      template.value = template.value.slice(0, start) + chip.dataset.insert + template.value.slice(end);
      template.focus();
      template.selectionStart = template.selectionEnd = start + chip.dataset.insert.length;
      template.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });

  // ------------------------------------------------------------ character count
  const counter = document.querySelector("[data-sms-counter]");
  function smsParts(text) {
    const unicode = [...text].some((ch) => ch.charCodeAt(0) > 127); // Bangla etc. -> shorter SMS
    const [single, multi] = unicode ? [70, 67] : [160, 153];
    return text.length === 0 ? 0 : text.length <= single ? 1 : Math.ceil(text.length / multi);
  }
  function countChars() {
    if (!template || !counter) return;
    const text = template.value;
    const parts = smsParts(text);
    counter.textContent = `${text.length} characters · ${parts} SMS${parts === 1 ? "" : " parts"}`;
    counter.classList.toggle("is-long", parts > 1);
  }
  if (template) { template.addEventListener("input", countChars); countChars(); }

  const form = document.getElementById("send-form");
  if (!form) return; // upload page: only the chips and the counter

  const csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
  const toastBox = document.getElementById("toast");
  function toast(text, error) {
    toastBox.textContent = text;
    toastBox.className = "toast" + (error ? " error" : "");
    toastBox.hidden = false;
    clearTimeout(toastBox._t);
    toastBox._t = setTimeout(() => (toastBox.hidden = true), 4000);
  }
  async function post(url, body) {
    const res = await fetch(url, { method: "POST", body, headers: { "X-CSRFToken": csrf }, credentials: "same-origin" });
    let json = {};
    try { json = await res.json(); } catch (e) { json = { ok: false, error: `Server error (${res.status})` }; }
    return json;
  }

  // ------------------------------------------------------------ preview
  let previewPerson = "";
  let previewTimer = null;
  async function refreshPreview() {
    const body = new FormData();
    body.append("message_template", template.value);
    if (previewPerson) body.append("person", previewPerson);
    const json = await post(form.dataset.previewUrl, body);
    if (!json.ok) return;
    const bubble = document.getElementById("sms-preview");
    bubble.textContent = json.message;
    document.getElementById("sms-length").textContent =
      `${json.length} characters · ${json.parts} SMS part${json.parts > 1 ? "s" : ""}`;
    document.getElementById("preview-who").textContent = `For ${json.person}`;
  }
  template.addEventListener("input", () => { clearTimeout(previewTimer); previewTimer = setTimeout(refreshPreview, 400); });
  document.querySelectorAll("[data-preview]").forEach((btn) => btn.addEventListener("click", () => {
    previewPerson = btn.dataset.preview;
    refreshPreview();
    document.getElementById("sms-preview").scrollIntoView({ behavior: "smooth", block: "center" });
  }));
  refreshPreview();

  // ------------------------------------------------------------ selection + DataTable
  // keep a reference to every checkbox before DataTables pages the rows out of the page
  const checks = [...document.querySelectorAll(".person-check")];
  const selectedCount = document.getElementById("selected-count");
  const sync = () => (selectedCount.textContent = checks.filter((c) => c.checked).length);
  checks.forEach((c) => c.addEventListener("change", sync));

  let peopleTable = null;
  if (window.DataTable && document.getElementById("people")) {
    peopleTable = new DataTable("#people", {
      pageLength: 25,
      lengthMenu: [25, 50, 100, 500],
      order: [[1, "asc"]],
      columnDefs: [{ targets: [0, 7], orderable: false, searchable: false }],
      language: {
        search: "", searchPlaceholder: "Search name, phone, email, ID…",
        lengthMenu: "Show _MENU_ people", info: "_START_–_END_ of _TOTAL_ people",
        infoFiltered: "(filtered from _MAX_)", zeroRecords: "Nobody matches", emptyTable: "No people in this list",
      },
    });
    const statusFilter = document.getElementById("status-filter");
    if (statusFilter) statusFilter.addEventListener("change", () => {
      const value = statusFilter.value;
      peopleTable.column(6).search(value ? `^${value}$` : "", true, false).draw();
    });
  }

  // header checkbox: selects everyone matching the current search / status filter (on every page)
  const all = document.getElementById("check-all");
  if (all) all.addEventListener("change", () => {
    const rows = peopleTable ? peopleTable.rows({ search: "applied" }).nodes().toArray() : [document];
    rows.forEach((row) => row.querySelectorAll(".person-check").forEach((c) => (c.checked = all.checked)));
    sync();
  });

  // ------------------------------------------------------------ sending
  const errors = document.getElementById("send-errors");
  const progress = document.getElementById("send-progress");
  const bar = document.getElementById("progress-bar");
  const progressText = document.getElementById("progress-text");
  const buttons = [...form.querySelectorAll("[data-send]")];
  let busy = false;

  function showCounts(counts) {
    Object.entries(counts).forEach(([key, value]) => {
      document.querySelectorAll(`[data-count="${key}"]`).forEach((el) => (el.textContent = value));
    });
  }

  async function sendLoop(totalSelected) {
    progress.hidden = false;
    let sent = 0, failed = 0;
    for (;;) {
      const json = await post(form.dataset.sendUrl, new FormData());
      if (!json.ok) { toast(json.error || "Sending stopped.", true); break; }
      sent += json.sent; failed += json.failed;
      showCounts(json.counts);
      const pending = json.counts.pending;
      const total = Math.max(totalSelected, sent + failed + pending) || 1;
      bar.style.width = `${Math.round(((total - pending) / total) * 100)}%`;
      progressText.textContent =
        `Total selected: ${total} · Sent: ${sent} · Failed: ${failed} · Pending: ${pending}`;
      if (json.done) break;
    }
    toast(`Finished: ${sent} sent, ${failed} failed.`, failed > 0);
    setTimeout(() => location.reload(), 1800);
  }

  async function start(mode) {
    if (busy) return;
    errors.innerHTML = "";
    const body = new FormData(form);
    if (mode === "all") body.append("scope", "all");
    if (mode === "retry") { body.append("scope", "all"); body.append("retry", "1"); }
    if (mode === "selected") {
      const ids = checks.filter((c) => c.checked).map((c) => c.value);
      if (!ids.length) { toast("Select at least one candidate first.", true); return; }
      ids.forEach((id) => body.append("ids", id));
    }
    const label = mode === "retry" ? "retry the failed SMS" : mode === "all" ? "send to everyone not sent yet" : "send to the selected people";
    if (!confirm(`Do you want to ${label}? Each person gets one SMS.`)) return;

    busy = true;
    buttons.forEach((b) => (b.disabled = true));
    const json = await post(form.dataset.queueUrl, body);
    if (!json.ok) {
      Object.values(json.errors || { x: [json.error || "Could not start sending."] }).flat()
        .forEach((msg) => { const li = document.createElement("li"); li.textContent = msg; errors.appendChild(li); });
      busy = false;
      buttons.forEach((b) => (b.disabled = false));
      return;
    }
    showCounts(json.counts);
    if (!json.counts.pending) {
      toast("Nothing to send - everyone selected already received the SMS.");
      busy = false;
      buttons.forEach((b) => (b.disabled = false));
      return;
    }
    await sendLoop(json.counts.pending);
  }
  buttons.forEach((b) => b.addEventListener("click", () => start(b.dataset.send)));

  // an earlier run was interrupted (tab closed): offer to finish it
  const pendingBefore = parseInt(form.dataset.pending || "0", 10);
  if (pendingBefore > 0) {
    const resume = document.createElement("button");
    resume.type = "button";
    resume.className = "btn btn-red";
    resume.textContent = `▶ Resume sending (${pendingBefore} pending)`;
    resume.addEventListener("click", () => { resume.remove(); busy = true; buttons.forEach((b) => (b.disabled = true)); sendLoop(pendingBefore); });
    form.querySelector(".send-actions").prepend(resume);
  }
})();
