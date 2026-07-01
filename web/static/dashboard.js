// Planet switcher for the dashboard hero. Clicking an agent in the pill nav
// re-themes the page (body[data-planet]) — which swaps the planet visual, the
// title gradient, and the accent — and reveals that agent's featured block.
(function () {
  var nav = document.querySelector(".pill-nav");
  if (!nav) return;
  var buttons = nav.querySelectorAll("button[data-agent]");

  function select(btn) {
    var agent = btn.dataset.agent;
    var planet = btn.dataset.planet || "earth";
    document.body.dataset.planet = planet;

    buttons.forEach(function (b) { b.classList.toggle("on", b === btn); });
    document.querySelectorAll(".featured").forEach(function (f) {
      f.hidden = f.id !== "feat-" + agent;
    });
  }

  buttons.forEach(function (btn) {
    btn.addEventListener("click", function () { select(btn); });
  });
})();
