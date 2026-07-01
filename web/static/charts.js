// Dashboard charts via ApexCharts. Renders only into containers that exist on
// the current page (#chart-applications, #chart-stocks), so the same script is
// safe to include on the dashboard and the applications page.
(function () {
  if (typeof ApexCharts === "undefined") return;

  var MUTED = "#8b93a6";
  var BORDER = "rgba(255,255,255,0.14)";

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
          stroke: { width: 2, colors: ["#05060b"] },
          legend: { position: "bottom", labels: { colors: MUTED } },
          dataLabels: { enabled: true, style: { colors: ["#04060d"] } },
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
            style: { colors: ["#04060d"] },
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

  var apps = document.getElementById("chart-applications");
  if (apps) renderApplications(apps);
  var stocks = document.getElementById("chart-stocks");
  if (stocks) renderStocks(stocks);
})();
