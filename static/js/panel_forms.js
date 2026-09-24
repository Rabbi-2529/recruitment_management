/*
 * Admin panel form helpers:
 *  - Select2 on every <select data-searchable>
 *  - Candidate form: department list depends on the selected circular
 */
$(function () {
  $("select[data-searchable]").each(function () {
    const $s = $(this);
    $s.select2({
      width: "100%",
      placeholder: $s.data("placeholder") || $s.find('option[value=""]').first().text() || "Select",
      closeOnSelect: !$s.prop("multiple"),
      language: { noResults: () => "No match found" },
    });
  });

  // focus the search box as soon as a single select opens
  $(document).on("select2:open", () => {
    const field = document.querySelector(".select2-container--open .select2-search__field");
    if (field) field.focus();
  });

  // ---- department depends on circular
  const mapEl = document.getElementById("circular-departments");
  const $dept = $('select[data-depends-on="circular"]');
  const $circular = $("#id_circular");
  if (!mapEl || !$dept.length || !$circular.length) return;

  const map = JSON.parse(mapEl.textContent);
  const allOptions = $dept.find("option").map((i, o) => ({ value: o.value, text: o.text })).get();

  function applyCircular() {
    const circularId = $circular.val();
    const allowed = map[circularId]; // undefined => circular has no departments set yet, show all
    const current = $dept.val();

    $dept.empty();
    allOptions.forEach((o) => {
      if (o.value === "" || !circularId || !allowed || allowed.includes(Number(o.value))) {
        $dept.append(new Option(o.text, o.value));
      }
    });

    const values = $dept.find("option").map((i, o) => o.value).get().filter(Boolean);
    if (values.includes(current)) $dept.val(current);
    else if (values.length === 1) $dept.val(values[0]); // only one department: pick it
    else $dept.val("");

    $dept.prop("disabled", !circularId);
    $dept.trigger("change.select2");
  }

  $circular.on("change", applyCircular);
  applyCircular();
});
