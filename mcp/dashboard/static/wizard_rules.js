/* Rule-picker behaviour for the create-standards wizard.
 *
 * Two jobs: the expand/collapse-all toggle over the rule categories, and the
 * per-rule help dialog. Help text for the current step's rules is inlined by
 * the template as a JSON island, so opening a popup costs no round trip.
 */
(function () {
  "use strict";

  // --- Expand / collapse all ------------------------------------------------

  var toggle = document.getElementById("toggle-all");
  if (toggle) {
    var cats = document.querySelectorAll("details.rule-category");
    var expand = true;
    toggle.addEventListener("click", function (e) {
      e.preventDefault();
      Array.prototype.forEach.call(cats, function (d) { d.open = expand; });
      expand = !expand;
      toggle.textContent = expand ? "Expand all" : "Collapse all";
    });
  }

  // --- Rule help dialog -----------------------------------------------------

  var modal = document.getElementById("rule-help-modal");
  var island = document.getElementById("rule-help-data");
  if (!modal || !island) return;

  var titleEl = modal.querySelector("#rule-help-title");
  var metaEl = modal.querySelector(".rule-help-meta");
  var bodyEl = modal.querySelector(".modal-body");
  var closeBtn = modal.querySelector(".modal-close");
  var backdrop = modal.querySelector(".modal-backdrop");

  var help = {};
  try {
    help = JSON.parse(island.textContent) || {};
  } catch (err) {
    return;  // malformed payload: leave the buttons inert rather than throwing
  }

  var lastFocus = null;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;  // never innerHTML
    return node;
  }

  /* One side of the do/don't pair. Rendered only when that side is authored,
     so a rule with just a wrong way shows one full-width column. */
  function codeColumn(kind, label, code, lang) {
    var col = el("div", "rule-help-col rule-help-col-" + kind);
    col.appendChild(el("div", "rule-help-col-label", label));
    var pre = el("pre", "rule-help-code");
    var codeEl = el("code", lang ? "language-" + lang : null, code);
    pre.appendChild(codeEl);
    col.appendChild(pre);
    return col;
  }

  function render(rule) {
    titleEl.textContent = rule.title;

    metaEl.textContent = "";
    var bits = [rule.severity];
    if (rule.locked) bits.push("required");
    bits.push(rule.rule_id);
    if (rule.source) bits.push(rule.source);
    metaEl.appendChild(el("span", null, bits.join("  ·  ")));

    bodyEl.textContent = "";

    /* With no authored help the rule's own one-liner is the explanation. That
       keeps every icon leading somewhere useful while packs are filled in. */
    bodyEl.appendChild(el("p", "rule-help-prose", rule.help || rule.body));
    if (rule.help && rule.body) {
      bodyEl.appendChild(el("p", "rule-help-rule-text", rule.body));
    }

    var ex = rule.example;
    if (ex && (ex.bad || ex.good)) {
      if (ex.caption) bodyEl.appendChild(el("p", "rule-help-caption", ex.caption));
      var grid = el("div", "rule-help-example");
      if (ex.bad) grid.appendChild(codeColumn("bad", "✗ Don't", ex.bad, ex.lang));
      if (ex.good) grid.appendChild(codeColumn("good", "✓ Do", ex.good, ex.lang));
      if (!ex.bad || !ex.good) grid.classList.add("is-single");
      bodyEl.appendChild(grid);
    }

    var lands = el("p", "rule-help-lands");
    lands.appendChild(el("span", "rule-help-lands-label", "Lands in"));
    lands.appendChild(el("span", null, rule.doc_label));
    bodyEl.appendChild(lands);
  }

  function open(button) {
    var rule = help[button.getAttribute("data-help-for")];
    if (!rule) return;
    lastFocus = button;
    render(rule);
    modal.hidden = false;
    if (closeBtn) closeBtn.focus();
  }

  function close() {
    if (modal.hidden) return;
    modal.hidden = true;
    bodyEl.textContent = "";
    if (lastFocus) {
      lastFocus.focus();
      lastFocus = null;
    }
  }

  /* Delegated, and stopped hard: the button sits next to a <label>, and a
     click that reaches the row would toggle the rule the user is only asking
     about. */
  document.addEventListener("click", function (e) {
    var button = e.target.closest ? e.target.closest(".rule-help-btn") : null;
    if (!button) return;
    e.preventDefault();
    e.stopPropagation();
    open(button);
  });

  if (closeBtn) closeBtn.addEventListener("click", close);
  if (backdrop) backdrop.addEventListener("click", close);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") close();
  });
})();
