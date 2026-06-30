// Dashboard charts via ApexCharts. Renders only into containers that exist on
// the current page (#chart-applications, #chart-stocks), so the same script is
// safe to include on the dashboard and the applications page.
(function () {
  if (typeof ApexCharts === "undefined") return;

  var MUTED = "#8fa394";
  var BORDER = "#283a31";

  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(url + " -> " + r.status);
      return r.json();
    });
  }

  function empty(el, msg) {
    el.innerHTML = '<p class="muted" style="padding:1rem">' + msg + "</p>";
  }

  function renderApplications(el) {
    getJSON("/charts/applications")
      .then(function (d) {
        if (!d.total) {
          empty(el, "No applications logged yet.");
          return;
        }
        new ApexCharts(el, {
          chart: { type: "donut", height: 250, background: "transparent" },
          theme: { mode: "dark" },
          series: d.series,
          labels: d.labels,
          colors: d.colors,
          stroke: { width: 2, colors: ["#13201b"] },
          legend: { position: "bottom", labels: { colors: MUTED } },
          dataLabels: { enabled: true, style: { colors: ["#0c1a0d"] } },
          plotOptions: {
            pie: {
              donut: {
                labels: {
                  show: true,
                  total: { show: true, label: "Total", color: MUTED },
                },
              },
            },
          },
          tooltip: { theme: "dark" },
        }).render();
      })
      .catch(function () {
        empty(el, "Couldn't load application stats.");
      });
  }

  function renderStocks(el) {
    getJSON("/charts/stocks")
      .then(function (d) {
        if (!d.series || !d.series.length) {
          empty(el, "No quotes available right now.");
          return;
        }
        new ApexCharts(el, {
          chart: {
            type: "bar",
            height: 250,
            background: "transparent",
            toolbar: { show: false },
          },
          theme: { mode: "dark" },
          series: [{ name: "% change", data: d.series }],
          colors: d.colors,
          xaxis: { categories: d.labels, labels: { style: { colors: MUTED } } },
          yaxis: {
            labels: {
              style: { colors: MUTED },
              formatter: function (v) {
                return v.toFixed(1) + "%";
              },
            },
          },
          plotOptions: {
            bar: { distributed: true, borderRadius: 4, columnWidth: "55%" },
          },
          dataLabels: {
            enabled: true,
            formatter: function (v) {
              return (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
            },
            style: { colors: ["#0c1a0d"] },
          },
          legend: { show: false },
          grid: { borderColor: BORDER },
          tooltip: { theme: "dark" },
        }).render();
      })
      .catch(function () {
        empty(el, "Couldn't load quotes.");
      });
  }

  // Decorative green "mountains" backdrop for the hero (no data — just ambiance).
  function renderMountains(el) {
    new ApexCharts(el, {
      chart: {
        type: "area",
        height: 320,
        sparkline: { enabled: true },
        animations: { enabled: true, easing: "easeinout", speed: 900 },
      },
      series: [
        { name: "ridge", data: [34, 58, 44, 72, 52, 88, 62, 78, 48, 68, 54, 64, 50] },
        { name: "hill", data: [16, 32, 24, 40, 30, 48, 36, 44, 28, 38, 30, 36, 28] },
      ],
      colors: ["#7fa157", "#3f5a36"],
      stroke: { curve: "smooth", width: 2 },
      fill: {
        type: "gradient",
        gradient: { shadeIntensity: 1, opacityFrom: 0.55, opacityTo: 0.05, stops: [0, 100] },
      },
      tooltip: { enabled: false },
      legend: { show: false },
      dataLabels: { enabled: false },
    }).render();
  }

  var mountains = document.getElementById("hero-mountains");
  if (mountains) renderMountains(mountains);
  var apps = document.getElementById("chart-applications");
  if (apps) renderApplications(apps);
  var stocks = document.getElementById("chart-stocks");
  if (stocks) renderStocks(stocks);
})();
