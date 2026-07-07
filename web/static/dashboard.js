// Planet switcher for the dashboard hero. Clicking an agent in the pill nav
// re-themes the page (body[data-planet]) and reveals that agent's panel below.
// The stocks panel lazy-loads its trading-desk data the first time it's shown.
(function () {
  var nav = document.querySelector(".pill-nav");
  if (!nav) return;
  var buttons = nav.querySelectorAll("button[data-agent]");

  function select(btn) {
    var agent = btn.dataset.agent;
    document.body.dataset.planet = btn.dataset.planet || "earth";

    buttons.forEach(function (b) { b.classList.toggle("on", b === btn); });
    document.querySelectorAll(".panel").forEach(function (p) {
      p.hidden = p.id !== "panel-" + agent;
    });

    // Lazy-load the stocks trading desk on first reveal (charts need a visible,
    // sized container to render correctly).
    if (agent === "stock_digest" && window.renderStocksDesk) {
      window.renderStocksDesk();
    }
  }

  buttons.forEach(function (btn) {
    btn.addEventListener("click", function () { select(btn); });
  });
})();
