// Live 3D tree hero — a procedural EZ-Tree mesh rendered with Three.js, lit and
// themed to the palette, slowly rotating on its trunk over the gradient
// mountains. Loaded as an ES module; "three" and "@dgreenheck/ez-tree" are
// resolved by the importmap in the dashboard template.
import * as THREE from "three";
import { Tree } from "@dgreenheck/ez-tree";

const mount = document.getElementById("tree3d");
if (mount) init(mount);

function init(mount) {
  const H = 320;
  const w = () => mount.clientWidth || 800;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(w(), H);
  renderer.setClearColor(0x000000, 0); // transparent — mountains show through
  mount.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, w() / H, 0.1, 1000);

  // Soft sky/ground hemisphere + a warm key light for depth.
  scene.add(new THREE.HemisphereLight(0xd6e4c6, 0x20281f, 1.15));
  const key = new THREE.DirectionalLight(0xfff1d6, 1.5);
  key.position.set(6, 12, 8);
  scene.add(key);

  let tree;
  try {
    tree = new Tree();
    tree.options.seed = 22;
    // Theme the foliage/bark toward the palette (guarded — option shape varies).
    try { tree.options.leaves.tint = 0xa8c47e; } catch (e) {}
    try { tree.options.bark.tint = 0xb0825a; } catch (e) {}
    tree.generate();
  } catch (e) {
    console.error("EZ-Tree generation failed:", e);
    mount.style.display = "none"; // fall back to mountains-only hero
    return;
  }
  scene.add(tree);

  // Frame the tree: look at its centre, back the camera off by its size.
  const box = new THREE.Box3().setFromObject(tree);
  const size = new THREE.Vector3();
  const center = new THREE.Vector3();
  box.getSize(size);
  box.getCenter(center);
  const maxDim = Math.max(size.x, size.y, size.z) || 10;
  const dist = maxDim * 1.25; // closer -> larger, fills the hero
  const target = center.y * 0.86; // aim a touch lower so the tree feels grounded
  camera.position.set(0, target, dist);
  camera.lookAt(0, target, 0);

  function resize() {
    renderer.setSize(w(), H);
    camera.aspect = w() / H;
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);

  let raf = null;
  function animate() {
    raf = requestAnimationFrame(animate);
    tree.rotation.y += 0.003; // gentle spin on the trunk axis
    renderer.render(scene, camera);
  }
  animate();

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      if (raf) cancelAnimationFrame(raf);
      raf = null;
    } else if (!raf) {
      animate();
    }
  });
}
