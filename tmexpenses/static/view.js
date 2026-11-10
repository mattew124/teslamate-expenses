// Loaded in <head>, so the rows/tiles choice is in place before the page first draws (no flash of the other
// layout). No inline scripts: the CSP only allows files. app.js wires up the buttons.
(function () {
  var saved = null;
  try { saved = localStorage.getItem("expenses-view"); } catch (e) { /* private mode */ }
  document.documentElement.dataset.view = saved === "rows" || saved === "tiles" ? saved
    : (matchMedia("(max-width: 40rem)").matches ? "tiles" : "rows");
})();
