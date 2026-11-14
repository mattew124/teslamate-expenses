// Loaded in <head>, so the rows/tiles choice and any column widths the person has set are in place before
// the page first draws (no flash of the other layout). No inline scripts: the CSP only allows files.
// app.js wires up the buttons and the dragging.
(function () {
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem("expenses-view"); } catch (e) { /* private mode */ }
  root.dataset.view = saved === "rows" || saved === "tiles" ? saved
    : (matchMedia("(max-width: 40rem)").matches ? "tiles" : "rows");

  // Column widths for the rows layout, in pixels by column: {date: 120, cat: 160, ...}, or nothing for
  // the built-in ones. They go into custom properties that style.css reads (set through the CSSOM,
  // which the CSP allows where it would refuse a style attribute).
  var COLUMNS = ["date", "cat", "vendor", "amount", "odo", "inv", "act"];
  window.expenseColumns = {
    names: COLUMNS,
    apply: function (widths) {
      var total = 0;
      var ok = !!widths && COLUMNS.every(function (name) {
        return typeof widths[name] === "number" && widths[name] >= 20 && widths[name] <= 2000;
      });
      COLUMNS.forEach(function (name) {
        if (ok) { root.style.setProperty("--col-" + name, widths[name] + "px"); total += widths[name]; }
        else root.style.removeProperty("--col-" + name);
      });
      if (ok) root.style.setProperty("--cols-total", total + "px");
      else root.style.removeProperty("--cols-total");
      return ok;
    },
  };
  try { window.expenseColumns.apply(JSON.parse(localStorage.getItem("expenses-columns"))); } catch (e) { /* built-in widths */ }
})();
