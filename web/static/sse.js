// Live run viewer. Streams node events + final output for a running run.
(function () {
  var runId = window.RUN_ID;
  if (!runId) return;

  var log = document.getElementById("node-log");
  var output = document.getElementById("output");
  var statusBadge = document.getElementById("run-status");
  var placeholder = document.getElementById("log-placeholder");

  var es = new EventSource("/runs/" + runId + "/events");

  // One line per node: replace in place by data-node so start -> finish updates.
  es.addEventListener("node", function (e) {
    var d = JSON.parse(e.data);
    if (placeholder) { placeholder.remove(); placeholder = null; }
    var existing = log.querySelector('[data-node="' + cssEscape(d.node) + '"]');
    var tmp = document.createElement("div");
    tmp.innerHTML = d.html.trim();
    var line = tmp.firstChild;
    if (existing) {
      existing.replaceWith(line);
    } else {
      log.appendChild(line);
    }
  });

  es.addEventListener("done", function (e) {
    var d = JSON.parse(e.data);
    output.innerHTML = d.html
      ? '<div class="output">' + d.html + "</div>"
      : '<div class="output empty">No output.</div>';
    setStatus(d.status || "success");
    es.close();
  });

  es.addEventListener("failed", function (e) {
    var d = JSON.parse(e.data);
    output.innerHTML =
      '<pre class="output" style="white-space:pre-wrap; color:var(--red)">' +
      escapeHtml(d.error || "run failed") + "</pre>";
    setStatus("error");
    es.close();
  });

  es.onerror = function () {
    // EventSource auto-reconnects; if the run already ended the server replays
    // from SQLite and closes. Nothing to do here.
  };

  function setStatus(s) {
    if (!statusBadge) return;
    statusBadge.textContent = s;
    statusBadge.className = "badge " + s;
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function cssEscape(s) {
    return String(s).replace(/["\\]/g, "\\$&");
  }
})();
