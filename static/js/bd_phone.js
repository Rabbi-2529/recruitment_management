/*
 * Live Bangladesh phone number check for inputs with [data-bd-phone].
 * Accepts only 01XXXXXXXXX (11 digits) or 8801XXXXXXXXX (13 digits), operator 013-019.
 */
(function () {
  const MSG = "Invalid phone number. Use 01XXXXXXXXX (11 digits) or 8801XXXXXXXXX (13 digits).";

  // Is `v` a valid beginning of a BD number? (so we can warn while typing)
  function partialOk(v) {
    const tpl = v.startsWith("8") ? "8801" : "01";
    const head = v.slice(0, tpl.length);
    if (!tpl.startsWith(head)) return false;
    if (v.length > tpl.length && !/[3-9]/.test(v[tpl.length])) return false;
    return true;
  }

  const maxLen = (v) => (v.startsWith("8") ? 13 : 11);
  const complete = (v) => /^(?:88)?01[3-9]\d{8}$/.test(v) && v.length === maxLen(v);

  function attach(input) {
    const msg = document.createElement("div");
    msg.className = "phone-msg";
    msg.setAttribute("aria-live", "polite");
    input.insertAdjacentElement("afterend", msg);

    function check(final) {
      let v = input.value.replace(/\D/g, "");
      v = v.slice(0, maxLen(v));
      if (input.value !== v) input.value = v;

      let error = "";
      if (v && !partialOk(v)) error = MSG;
      else if (final && !complete(v)) error = v ? MSG : "Enter your phone number.";

      msg.textContent = error;
      input.classList.toggle("is-invalid", !!error);
      input.classList.toggle("is-valid", !error && complete(v));
      input.setCustomValidity(error);
      return !error;
    }

    input.addEventListener("input", () => check(false));
    input.addEventListener("blur", () => { if (input.value) check(true); });
    input.form && input.form.addEventListener("submit", (e) => {
      if (!check(true)) { e.preventDefault(); input.focus(); }
    });
    if (input.value) check(false);
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("input[data-bd-phone]").forEach(attach);
  });
})();
