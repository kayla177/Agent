// Stocks "trading desk" — fetches /stocks/desk once (on first reveal) and
// renders: paper portfolio + positions, BUY/SELL/HOLD signals, a normalized
// price-trend line, the watchlist % bars, and recent paper trades.
(function () {
  var MUTED = "#8b93a6";
  var loaded = false;

  function fmt(n) {
    return (n == null) ? "—" : "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function el(id) { return document.getElementById(id); }
  function msg(id, t) { var e = el(id); if (e) { e.classList.remove("desk-loading"); e.innerHTML = '<p class="muted">' + t + "</p>"; } }

  function renderPortfolio(p) {
    var e = el("desk-portfolio"); if (!e) return;
    e.classList.remove("desk-loading");
    if (!p.configured) {
      e.innerHTML = '<p class="muted">No Alpaca keys — set ALPACA_API_KEY_ID / _SECRET_KEY to see your paper portfolio.</p>';
      return;
    }
    var plClass = p.pl > 0 ? "up" : p.pl < 0 ? "down" : "";
    var sim = p.simulated ? ' <span class="muted">(simulated)</span>' : "";
    var html =
      '<div class="stat-row"><div><div class="stat-lg">' + fmt(p.value) + "</div><div class=muted>value" + sim + "</div></div>" +
      "<div><div class=stat-lg>" + fmt(p.cash) + "</div><div class=muted>cash</div></div>" +
      '<div><div class="stat-lg ' + plClass + '">' + (p.pl >= 0 ? "+" : "") + fmt(p.pl).replace("$", "$") + "</div><div class=muted>unrealized P&L</div></div></div>";
    if (p.positions && p.positions.length) {
      html += '<table class="mini"><thead><tr><th>Symbol</th><th>Qty</th><th>Value</th><th>P&L</th></tr></thead><tbody>';
      p.positions.forEach(function (x) {
        var c = x.plpc > 0 ? "up" : x.plpc < 0 ? "down" : "";
        html += "<tr><td>" + x.symbol + "</td><td>" + x.qty + "</td><td>" + fmt(x.value) + '</td><td class="' + c + '">' + (x.plpc >= 0 ? "+" : "") + x.plpc + "%</td></tr>";
      });
      html += "</tbody></table>";
    } else {
      html += '<p class="muted">No open positions (pending orders fill at the next market open).</p>';
    }
    e.innerHTML = html;
  }

  function renderSignals(signals) {
    var e = el("desk-signals"); if (!e) return;
    e.classList.remove("desk-loading");
    if (!signals || !signals.length) { msg("desk-signals", "No signals."); return; }
    var html = '<table class="mini"><thead><tr><th>Symbol</th><th>Price</th><th>RSI</th><th>Signal</th></tr></thead><tbody>';
    signals.forEach(function (s) {
      var badge = '<span class="badge" style="background:' + s.color + '22;color:' + s.color + '">' + s.signal.toUpperCase() + "</span>";
      html += "<tr><td>" + s.symbol + "</td><td>" + (s.price != null ? "$" + Number(s.price).toFixed(2) : "—") +
        "</td><td>" + (s.rsi != null ? s.rsi : "—") + "</td><td>" + badge + "</td></tr>";
    });
    e.innerHTML = html + "</tbody></table>";
  }

  function renderTrend(trend) {
    var e = el("desk-trend"); if (!e) return;
    e.classList.remove("desk-loading");
    if (!trend.series || !trend.series.length) { msg("desk-trend", "No price history (needs Twelve Data key)."); return; }
    e.innerHTML = "";  // clear the "loading…" text before the chart mounts
    new ApexCharts(e, {
      chart: { type: "line", height: 240, background: "transparent", toolbar: { show: false } },
      theme: { mode: "dark" },
      series: trend.series,
      colors: ["#7fb0ff", "#e0b15a", "#7fc08a", "#c58cff", "#e0705a"],
      stroke: { curve: "smooth", width: 2 },
      xaxis: { categories: trend.labels, labels: { show: false }, axisTicks: { show: false }, axisBorder: { show: false } },
      yaxis: { labels: { style: { colors: MUTED }, formatter: function (v) { return v.toFixed(0); } } },
      legend: { position: "top", labels: { colors: MUTED } },
      grid: { borderColor: "rgba(255,255,255,0.08)" },
      tooltip: { theme: "dark" },
    }).render();
  }

  function renderWatchlist(wl) {
    var e = el("desk-watchlist"); if (!e) return;
    e.classList.remove("desk-loading");
    if (!wl.series || !wl.series.length) { msg("desk-watchlist", "No quotes."); return; }
    e.innerHTML = "";  // clear the "loading…" text before the chart mounts
    new ApexCharts(e, {
      chart: { type: "bar", height: 240, background: "transparent", toolbar: { show: false } },
      theme: { mode: "dark" },
      series: [{ name: "% change", data: wl.series }],
      colors: wl.colors,
      xaxis: { categories: wl.labels, labels: { style: { colors: MUTED } } },
      yaxis: { labels: { style: { colors: MUTED }, formatter: function (v) { return v.toFixed(1) + "%"; } } },
      plotOptions: { bar: { distributed: true, borderRadius: 4, columnWidth: "55%" } },
      dataLabels: { enabled: true, formatter: function (v) { return (v >= 0 ? "+" : "") + v.toFixed(1) + "%"; }, style: { colors: ["#04060d"] } },
      legend: { show: false },
      grid: { borderColor: "rgba(255,255,255,0.08)" },
      tooltip: { theme: "dark" },
    }).render();
  }

  function renderTrades(trades) {
    var e = el("desk-trades"); if (!e) return;
    e.classList.remove("desk-loading");
    if (!trades || !trades.length) { msg("desk-trades", "No paper trades yet — run the trader."); return; }
    var html = '<table class="mini"><thead><tr><th>When (UTC)</th><th>Side</th><th>Symbol</th><th>Amount</th><th>Status</th></tr></thead><tbody>';
    trades.forEach(function (t) {
      var amt = t.notional != null ? "$" + Number(t.notional).toFixed(0) : (t.qty != null ? t.qty + " sh" : "—");
      var side = '<span class="' + (t.side === "buy" ? "up" : "down") + '">' + (t.side || "").toUpperCase() + "</span>";
      html += "<tr><td class=muted>" + (t.ts || "").replace("T", " ").replace("+00:00", "") + "</td><td>" + side +
        "</td><td>" + t.symbol + "</td><td>" + amt + "</td><td class=muted>" + (t.status || "") + "</td></tr>";
    });
    e.innerHTML = html + "</tbody></table>";
  }

  window.renderStocksDesk = function () {
    if (loaded || typeof ApexCharts === "undefined") return;
    loaded = true;
    fetch("/stocks/desk")
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        renderPortfolio(d.portfolio);
        renderSignals(d.signals);
        renderTrend(d.trend);
        renderWatchlist(d.watchlist);
        renderTrades(d.trades);
      })
      .catch(function () {
        ["desk-portfolio", "desk-signals", "desk-trend", "desk-watchlist", "desk-trades"]
          .forEach(function (id) { msg(id, "Couldn't load the trading desk."); });
        loaded = false; // allow retry on next tab click
      });
  };
})();
