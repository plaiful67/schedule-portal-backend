/* mobile-a11y.js — shared accessibility progressive-enhancement for every
 * giready.com mobile handout page. Spliced in just before </body> by each
 * skill's mobile render (see _inject_shared_mobile_a11y). Companion to
 * shared/mobile-base.css.
 *
 * Runs after the template's inline scripts (checklist builder, feedback FAB),
 * so the .time-box.ck / #fab-x elements already exist. Everything here is
 * additive and idempotent: it adds the keyboard + ARIA semantics the inline
 * markup is missing (WCAG 2.1.1 keyboard, 4.1.2 name/role/value, 1.3.1 info &
 * relationships) without changing any behaviour for mouse/touch users. If JS
 * is off the page degrades to the same static, readable content it always was.
 */
(function () {
  "use strict";

  /* --- 1.3.1: give every table header a scope --------------------------- */
  try {
    document.querySelectorAll("table").forEach(function (tbl) {
      tbl.querySelectorAll("thead th").forEach(function (th) {
        if (!th.hasAttribute("scope")) th.setAttribute("scope", "col");
      });
      tbl.querySelectorAll("tbody th").forEach(function (th) {
        if (!th.hasAttribute("scope")) th.setAttribute("scope", "row");
      });
    });
  } catch (e) {}

  /* --- 2.1.1 / 4.1.2: checklist gets a keyboard-operable checkbox --------
   * The interactive control is the .ck-box (not the whole step) because steps
   * can contain links, and a role=button must not wrap focusable descendants.
   * Clicking the box bubbles to the inline cascade handler on the step, so the
   * existing tap behaviour is reused verbatim. aria-checked mirrors the
   * cascade's .done state. */
  function activate(el) {
    return function (ev) {
      if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
        ev.preventDefault();
        el.click();
      }
    };
  }
  var boxes = [].slice.call(document.querySelectorAll(".time-box.ck .ck-box"));
  function syncChecked() {
    boxes.forEach(function (box) {
      var step = box.closest(".time-box");
      box.setAttribute("aria-checked", step && step.classList.contains("done") ? "true" : "false");
    });
  }
  if (boxes.length) {
    boxes.forEach(function (box) {
      var step = box.closest(".time-box");
      box.setAttribute("role", "checkbox");
      if (!box.hasAttribute("tabindex")) box.setAttribute("tabindex", "0");
      var label = step && step.querySelector(".ck-body");
      if (label && !box.hasAttribute("aria-label")) {
        box.setAttribute("aria-label",
          "Mark done: " + label.textContent.trim().replace(/\s+/g, " ").slice(0, 110));
      }
      box.addEventListener("keydown", activate(box));
      box.addEventListener("click", function () { requestAnimationFrame(syncChecked); });
      box.addEventListener("keydown", function () { requestAnimationFrame(syncChecked); });
    });
    // The whole step stays mouse-clickable (inline handler); reflect that the
    // box is the keyboard control, and resync after a row/Start-over click too.
    document.addEventListener("click", function () { requestAnimationFrame(syncChecked); });
    syncChecked();
  }

  /* --- 2.1.1: feedback FAB + its dismiss are keyboard operable ---------- */
  var fabX = document.getElementById("fab-x");
  if (fabX) {
    if (!fabX.hasAttribute("tabindex")) fabX.setAttribute("tabindex", "0");
    fabX.addEventListener("keydown", activate(fabX));
  }
  // The FAB and any other survey trigger respond to Enter/Space, not just click.
  [].slice.call(document.querySelectorAll('[data-open-survey][role="button"]')).forEach(function (el) {
    el.addEventListener("keydown", activate(el));
  });
})();
