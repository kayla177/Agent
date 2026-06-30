// Lush generative tree (p5.js, instance mode). A curved tapering trunk splits
// into branches; the canopy is hundreds of layered leaf dots (dark mass ->
// mid sage -> bright highlights) for a painterly, dense crown. Geometry is
// generated once with a fixed seed; only a gentle per-leaf sway animates.
(function () {
  var mount = document.getElementById("tree-canvas");
  if (!mount || typeof p5 === "undefined") return;

  var HEIGHT = 316;
  var SEED = 11;

  // Palette (RGB) — kept in sync with the CSS theme.
  var BARK_DEEP = [120, 86, 58];
  var BARK = [178, 130, 88];
  var LEAF_LAYERS = [
    { col: [92, 108, 60], rMin: 9, rMax: 16, alpha: 120 }, // dark base mass
    { col: [128, 150, 84], rMin: 7, rMax: 13, alpha: 130 }, // mid sage
    { col: [172, 196, 126], rMin: 5, rMax: 10, alpha: 150 }, // light sage
    { col: [203, 224, 196], rMin: 3, rMax: 7, alpha: 160 }, // mint highlights
  ];

  var sketch = function (p) {
    var t = 0;
    var segs = [];   // trunk/branch segments {x1,y1,x2,y2,w}
    var leaves = []; // {x,y,r,layer,liftFactor}
    var baseY = 0;

    function build(w, h) {
      segs = [];
      leaves = [];
      p.randomSeed(SEED);
      var baseX = w / 2;
      baseY = h - 2;
      var tips = [];
      var MAXD = 4;

      function walk(x, y, ang, len, weight, depth) {
        var steps = 5;
        var a = ang;
        var bend = p.random(-0.18, 0.18);
        var nx = x, ny = y;
        for (var i = 0; i < steps; i++) {
          a += bend / steps + p.random(-0.03, 0.03);
          var ex = nx + Math.cos(a) * (len / steps);
          var ey = ny + Math.sin(a) * (len / steps);
          var ww = p.lerp(weight, weight * 0.55, depth / MAXD);
          segs.push({ x1: nx, y1: ny, x2: ex, y2: ey, w: ww });
          nx = ex; ny = ey;
        }
        if (depth >= MAXD) {
          tips.push({ x: nx, y: ny });
          return;
        }
        var nb = depth < 2 ? 2 : p.random() < 0.6 ? 2 : 3;
        for (var b = 0; b < nb; b++) {
          var spread = 0.55;
          var na = a + (b - (nb - 1) / 2) * spread + p.random(-0.12, 0.12);
          walk(nx, ny, na, len * 0.72, weight * 0.62, depth + 1);
        }
      }

      var trunkLen = h * 0.3;
      walk(baseX, baseY, -p.PI / 2, trunkLen, Math.max(12, w * 0.018), 0);

      // Canopy: scatter leaves around every branch tip, plus a crown-fill pass.
      var cx = 0, cy = 0;
      for (var i = 0; i < tips.length; i++) { cx += tips[i].x; cy += tips[i].y; }
      cx /= tips.length || 1; cy /= tips.length || 1;

      function addLeaf(x, y) {
        var layer = p.floor(p.random(LEAF_LAYERS.length));
        var spec = LEAF_LAYERS[layer];
        leaves.push({
          x: x, y: y,
          r: p.random(spec.rMin, spec.rMax),
          layer: layer,
          lift: p.constrain((baseY - y) / (baseY - cy + 1), 0, 1.3),
        });
      }
      for (var ti = 0; ti < tips.length; ti++) {
        var n = p.floor(p.random(55, 80));
        for (var k = 0; k < n; k++) {
          var ang = p.random(p.TWO_PI);
          var rad = Math.abs(p.randomGaussian(0, 1)) * 15;
          addLeaf(tips[ti].x + Math.cos(ang) * rad, tips[ti].y + Math.sin(ang) * rad * 0.8);
        }
      }
      // Fill the crown centre densely so it reads as one rounded leafy mass.
      var crownW = w * 0.2, crownH = h * 0.24;
      for (var f = 0; f < 800; f++) {
        var u = p.random(p.TWO_PI), v = Math.sqrt(p.random());
        addLeaf(cx + Math.cos(u) * v * crownW, cy + Math.sin(u) * v * crownH - h * 0.05);
      }
      // Draw dark/large first, bright/small last (depth layering).
      leaves.sort(function (a, b) { return a.layer - b.layer; });
    }

    p.setup = function () {
      var w = mount.clientWidth || 800;
      var c = p.createCanvas(w, HEIGHT);
      c.parent(mount);
      p.frameRate(30);
      build(w, HEIGHT);
    };

    p.windowResized = function () {
      var w = mount.clientWidth || 800;
      p.resizeCanvas(w, HEIGHT);
      build(w, HEIGHT);
    };

    p.draw = function () {
      p.clear();
      t += 0.01;

      // soft ground shadow
      p.noStroke();
      p.fill(0, 0, 0, 45);
      p.ellipse(p.width / 2, baseY, p.width * 0.32, 16);

      // trunk + branches
      p.strokeCap(p.ROUND);
      for (var i = 0; i < segs.length; i++) {
        var s = segs[i];
        var shade = p.lerpColor(
          p.color(BARK_DEEP[0], BARK_DEEP[1], BARK_DEEP[2]),
          p.color(BARK[0], BARK[1], BARK[2]),
          p.constrain(s.w / 14, 0, 1)
        );
        p.stroke(shade);
        p.strokeWeight(s.w);
        p.line(s.x1, s.y1, s.x2, s.y2);
      }

      // leaves (with gentle, height-weighted sway)
      p.noStroke();
      for (var j = 0; j < leaves.length; j++) {
        var lf = leaves[j];
        var spec = LEAF_LAYERS[lf.layer];
        var sway = Math.sin(t + lf.y * 0.02) * 3.2 * lf.lift;
        var c2 = p.color(spec.col[0], spec.col[1], spec.col[2]);
        c2.setAlpha(spec.alpha);
        p.fill(c2);
        p.ellipse(lf.x + sway, lf.y, lf.r, lf.r * 0.92);
      }
    };
  };

  var inst = new p5(sketch);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) inst.noLoop();
    else inst.loop();
  });
})();
