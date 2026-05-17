(function () {
  const root = document.querySelector(".pcb-keyframe-lab");
  if (!root) return;

  const modelUrl = root.dataset.model || "/static/models/pcb_exploded_mesh.json";
  const viewport = document.getElementById("pcb-lab-viewport");
  const marker = document.getElementById("pcb-lab-text-marker");
  const tabs = document.getElementById("pcb-lab-shot-tabs");
  const jsonPreview = document.getElementById("pcb-lab-json");
  const status = document.getElementById("pcb-lab-status");

  const fields = {
    title: document.getElementById("pcb-lab-title"),
    kicker: document.getElementById("pcb-lab-kicker"),
    body: document.getElementById("pcb-lab-body"),
    motion: document.getElementById("pcb-lab-motion"),
    side: document.getElementById("pcb-lab-side"),
    textX: document.getElementById("pcb-lab-text-x"),
    textY: document.getElementById("pcb-lab-text-y"),
    explode: document.getElementById("pcb-lab-explode"),
    scale: document.getElementById("pcb-lab-scale"),
    rx: document.getElementById("pcb-lab-rx"),
    ry: document.getElementById("pcb-lab-ry"),
    rz: document.getElementById("pcb-lab-rz"),
    x: document.getElementById("pcb-lab-x"),
    y: document.getElementById("pcb-lab-y"),
    z: document.getElementById("pcb-lab-z"),
  };

  const outputs = {
    explode: document.getElementById("pcb-lab-explode-out"),
    scale: document.getElementById("pcb-lab-scale-out"),
    rx: document.getElementById("pcb-lab-rx-out"),
    ry: document.getElementById("pcb-lab-ry-out"),
    rz: document.getElementById("pcb-lab-rz-out"),
    x: document.getElementById("pcb-lab-x-out"),
    y: document.getElementById("pcb-lab-y-out"),
    z: document.getElementById("pcb-lab-z-out"),
  };

  const captureBtn = document.getElementById("pcb-lab-capture");
  const saveBtn = document.getElementById("pcb-lab-save");
  const copyBtn = document.getElementById("pcb-lab-copy");
  const resetBtn = document.getElementById("pcb-lab-reset");

  const SHOT_PRESETS = [
    {
      id: "shot-1",
      title: "Power Input Protection",
      kicker: "01 / Power Input",
      body: "Power entry, reverse protection, TVS clamp, and fuse boundary for the board front end.",
      side: "left",
      motion: "slide-rise",
    },
    {
      id: "shot-2",
      title: "Opto Isolation Module",
      kicker: "02 / Opto Isolation",
      body: "Isolation block with status indicator, showing the signal path and LED feedback.",
      side: "right",
      motion: "side-sweep",
    },
    {
      id: "shot-3",
      title: "Buck Converter",
      kicker: "03 / Buck Stage",
      body: "Dual buck section with output rail conversion and surrounding passive network.",
      side: "right",
      motion: "magnetic-pull",
    },
    {
      id: "shot-4",
      title: "CAN Bus Protection",
      kicker: "04 / CAN Protection",
      body: "Three-channel CAN protection, including the input protection network and termination support.",
      side: "left",
      motion: "spring-settle",
    },
  ];

  const state = {
    scene: null,
    camera: null,
    renderer: null,
    group: null,
    parts: [],
    dragging: null,
    model: {
      position: [0, 0, 0],
      rotation: [-18, 34, 0],
      scale: 1,
      explode: 0,
      target: [0, 0, 0],
    },
    text: {
      x: 66,
      y: 56,
      side: "left",
      motion: "slide-rise",
      title: "",
      kicker: "",
      body: "",
    },
    shots: JSON.parse(JSON.stringify(SHOT_PRESETS)),
    activeShot: 0,
  };

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function deg(value) {
    return value * (Math.PI / 180);
  }

  function rad(value) {
    return value * (180 / Math.PI);
  }

  function updateStatus(message) {
    status.textContent = message;
  }

  function updateOutputs() {
    outputs.explode.textContent = state.model.explode.toFixed(2);
    outputs.scale.textContent = state.model.scale.toFixed(2);
    outputs.rx.textContent = rad(state.model.rotation[0]).toFixed(1);
    outputs.ry.textContent = rad(state.model.rotation[1]).toFixed(1);
    outputs.rz.textContent = rad(state.model.rotation[2]).toFixed(1);
    outputs.x.textContent = state.model.position[0].toFixed(1);
    outputs.y.textContent = state.model.position[1].toFixed(1);
    outputs.z.textContent = state.model.position[2].toFixed(1);
  }

  function syncFieldsToState() {
    state.model.explode = Number(fields.explode.value);
    state.model.scale = Number(fields.scale.value);
    state.model.rotation = [deg(Number(fields.rx.value)), deg(Number(fields.ry.value)), deg(Number(fields.rz.value))];
    state.model.position = [Number(fields.x.value), Number(fields.y.value), Number(fields.z.value)];
    state.text.title = fields.title.value.trim();
    state.text.kicker = fields.kicker.value.trim();
    state.text.body = fields.body.value.trim();
    state.text.motion = fields.motion.value;
    state.text.side = fields.side.value;
    state.text.x = Number(fields.textX.value);
    state.text.y = Number(fields.textY.value);
    updateOutputs();
    renderOverlay();
    renderModel();
  }

  function syncStateToFields() {
    fields.explode.value = state.model.explode;
    fields.scale.value = state.model.scale;
    fields.rx.value = rad(state.model.rotation[0]);
    fields.ry.value = rad(state.model.rotation[1]);
    fields.rz.value = rad(state.model.rotation[2]);
    fields.x.value = state.model.position[0];
    fields.y.value = state.model.position[1];
    fields.z.value = state.model.position[2];
    fields.title.value = state.text.title;
    fields.kicker.value = state.text.kicker;
    fields.body.value = state.text.body;
    fields.motion.value = state.text.motion;
    fields.side.value = state.text.side;
    fields.textX.value = state.text.x;
    fields.textY.value = state.text.y;
    updateOutputs();
    renderOverlay();
  }

  function updateShotTabs() {
    tabs.innerHTML = "";
    state.shots.forEach((shot, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = index === state.activeShot ? "is-active" : "";
      button.textContent = `${index + 1}`;
      button.title = shot.title || `Shot ${index + 1}`;
      button.addEventListener("click", () => setShot(index));
      tabs.appendChild(button);
    });
  }

  function setShot(index) {
    state.activeShot = index;
    const shot = state.shots[index];
    state.model.position = Array.isArray(shot.model?.position) ? shot.model.position.slice(0, 3) : [0, 0, 0];
    state.model.rotation = Array.isArray(shot.model?.rotation) ? shot.model.rotation.slice(0, 3) : [deg(-18), deg(34), 0];
    state.model.scale = Number(shot.model?.scale ?? 1);
    state.model.explode = Number(shot.model?.explode ?? 0);
    state.model.target = Array.isArray(shot.camera?.target) ? shot.camera.target.slice(0, 3) : [0, 0, 0];
    state.text.title = shot.title || "";
    state.text.kicker = shot.kicker || "";
    state.text.body = shot.body || "";
    state.text.side = shot.side || "left";
    state.text.motion = shot.motion || "slide-rise";
    state.text.x = Number(shot.text?.x ?? (state.text.side === "right" ? 62 : 12));
    state.text.y = Number(shot.text?.y ?? 58);
    syncStateToFields();
    updateShotTabs();
    updateStatus(`镜头 ${index + 1} 已载入。`);
  }

  function motionClass() {
    return `motion-${state.text.motion}`;
  }

  function applyTextPosition() {
    const px = clamp(state.text.x, 2, 88);
    const py = clamp(state.text.y, 4, 90);
    marker.style.left = `${px}%`;
    marker.style.top = `${py}%`;
    marker.style.transform = "translate(-50%, -50%)";
    marker.dataset.side = state.text.side;
    marker.dataset.motion = state.text.motion;
  }

  function renderOverlay() {
    marker.className = `pcb-lab-text-marker ${motionClass()}`;
    marker.innerHTML = `
      <small>${state.text.kicker || "Close-up"}</small>
      <strong>${state.text.title || "Untitled shot"}</strong>
      <span>${state.text.body || "Edit this copy in the panel."}</span>
    `;
    applyTextPosition();
    jsonPreview.textContent = JSON.stringify(exportShots(), null, 2);
  }

  function buildMaterial(THREE, part) {
    const source = Array.isArray(part.color) ? part.color : [0.85, 0.87, 0.9];
    const color = part.kind === "board"
      ? new THREE.Color(0.12, 0.28, 0.74)
      : new THREE.Color(
          Math.min(1, source[0] * 1.05 + 0.02),
          Math.min(1, source[1] * 1.05 + 0.02),
          Math.min(1, source[2] * 1.05 + 0.02)
        );
    return new THREE.MeshStandardMaterial({
      color,
      metalness: part.kind === "board" ? 0.14 : 0.22,
      roughness: part.kind === "board" ? 0.5 : 0.42,
      emissive: part.kind === "board" ? new THREE.Color(0.01, 0.03, 0.1) : new THREE.Color(0.015, 0.015, 0.015),
    });
  }

  function buildMesh(THREE, part) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(part.positions, 3));
    if (part.normals && part.normals.length) {
      geometry.setAttribute("normal", new THREE.Float32BufferAttribute(part.normals, 3));
    } else {
      geometry.computeVertexNormals();
    }
    geometry.setIndex(part.indices);
    geometry.computeBoundingSphere();
    const mesh = new THREE.Mesh(geometry, buildMaterial(THREE, part));
    const outline = new THREE.LineSegments(
      new THREE.EdgesGeometry(geometry, part.kind === "board" ? 18 : 24),
      new THREE.LineBasicMaterial({
        color: part.kind === "board" ? 0x57b9ff : 0x1a202c,
        transparent: true,
        opacity: part.kind === "board" ? 0.7 : 0.42,
      })
    );
    mesh.add(outline);
    mesh.userData.kind = part.kind;
    mesh.userData.lift = part.kind === "board" ? -8 : clamp((part.bounds?.size?.[2] || 0) * 7, 18, 82);
    return mesh;
  }

  function fitScene(payload) {
    const THREE = window.THREE;
    const size = payload.bounds?.size || [100, 100, 100];
    const center = payload.bounds?.center || [0, 0, 0];
    const maxSize = Math.max(size[0], size[1], size[2], 1);
    const dist = maxSize * 1.75;
    state.camera.position.set(center[0] + dist * 0.1, center[1] - dist * 0.98, center[2] + dist * 0.72);
    state.camera.near = 0.1;
    state.camera.far = maxSize * 30;
    state.camera.lookAt(new THREE.Vector3(center[0], center[1], center[2]));
    state.camera.updateProjectionMatrix();
    state.group.position.set(-center[0], -center[1], -center[2]);
  }

  function resize() {
    if (!state.renderer) return;
    const rect = viewport.getBoundingClientRect();
    const width = Math.max(360, Math.floor(rect.width));
    const height = Math.max(360, Math.floor(rect.height));
    state.renderer.setSize(width, height, false);
    state.camera.aspect = width / height;
    state.camera.updateProjectionMatrix();
  }

  function renderModel() {
    if (!state.group) return;
    const { position, rotation, scale, explode } = state.model;
    state.group.position.set(position[0], position[1], position[2]);
    state.group.rotation.set(rotation[0], rotation[1], rotation[2]);
    state.group.scale.setScalar(scale);
    state.parts.forEach((mesh) => {
      mesh.position.set(0, 0, mesh.userData.lift * explode);
      mesh.rotation.set(0, 0, 0);
    });
  }

  function getPointerTextPosition(event) {
    const rect = viewport.getBoundingClientRect();
    return {
      x: clamp(((event.clientX - rect.left) / rect.width) * 100, 2, 88),
      y: clamp(((event.clientY - rect.top) / rect.height) * 100, 6, 90),
    };
  }

  function startPointerDrag(event, type) {
    event.preventDefault();
    state.dragging = {
      type,
      startX: event.clientX,
      startY: event.clientY,
      model: {
        position: state.model.position.slice(),
        rotation: state.model.rotation.slice(),
      },
    };
    viewport.setPointerCapture?.(event.pointerId);
  }

  function handlePointerMove(event) {
    if (!state.dragging) return;
    const dx = event.clientX - state.dragging.startX;
    const dy = event.clientY - state.dragging.startY;
    if (state.dragging.type === "model-rotate") {
      state.model.rotation[1] = state.dragging.model.rotation[1] + deg(dx * 0.42);
      state.model.rotation[0] = clamp(state.dragging.model.rotation[0] + deg(dy * 0.28), deg(-86), deg(86));
    } else if (state.dragging.type === "model-pan") {
      state.model.position[0] = state.dragging.model.position[0] + dx * 0.45;
      state.model.position[1] = state.dragging.model.position[1] - dy * 0.45;
    } else if (state.dragging.type === "text") {
      const pos = getPointerTextPosition(event);
      state.text.x = pos.x;
      state.text.y = pos.y;
    }
    syncStateToFields();
  }

  function stopPointerDrag() {
    state.dragging = null;
  }

  function captureShot() {
    const shot = {
      id: `shot-${state.activeShot + 1}`,
      title: fields.title.value.trim(),
      kicker: fields.kicker.value.trim(),
      body: fields.body.value.trim(),
      side: fields.side.value === "right" ? "right" : "left",
      motion: fields.motion.value,
      camera: {
        position: state.camera.position.toArray().map((v) => Number(v.toFixed(4))),
        rotation: [
          Number(state.camera.rotation.x.toFixed(4)),
          Number(state.camera.rotation.y.toFixed(4)),
          Number(state.camera.rotation.z.toFixed(4)),
        ],
        target: state.model.target.slice(),
        zoom: Number(state.camera.zoom.toFixed(4)),
      },
      model: {
        position: state.model.position.slice().map((v) => Number(v.toFixed(4))),
        rotation: state.model.rotation.slice().map((v) => Number(v.toFixed(4))),
        scale: Number(state.model.scale.toFixed(4)),
        explode: Number(state.model.explode.toFixed(4)),
      },
      text: {
        x: Number(state.text.x.toFixed(2)),
        y: Number(state.text.y.toFixed(2)),
      },
    };
    state.shots[state.activeShot] = shot;
    updateShotTabs();
    renderOverlay();
    updateStatus(`已记录镜头 ${state.activeShot + 1}。`);
    return shot;
  }

  function exportShots() {
    return {
      updated_at: new Date().toISOString(),
      source: "pcb-keyframe-lab",
      shots: state.shots.map((shot) => ({
        ...shot,
        camera: {
          ...shot.camera,
          position: (shot.camera?.position || [0, 0, 0]).map((v) => Number(v)),
          rotation: (shot.camera?.rotation || [0, 0, 0]).map((v) => Number(v)),
          target: (shot.camera?.target || [0, 0, 0]).map((v) => Number(v)),
        },
        model: {
          ...shot.model,
          position: (shot.model?.position || [0, 0, 0]).map((v) => Number(v)),
          rotation: (shot.model?.rotation || [0, 0, 0]).map((v) => Number(v)),
        },
      })),
    };
  }

  async function saveShots() {
    const response = await fetch("/api/pcb-keyframes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(exportShots()),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "保存失败。");
    }
    updateStatus(`已保存到 ${data.payload.updated_at}。`);
    return data.payload;
  }

  async function loadShots() {
    const response = await fetch("/api/pcb-keyframes", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    if (data && Array.isArray(data.shots) && data.shots.length === 4) {
      state.shots = data.shots;
      state.activeShot = 0;
      setShot(0);
      updateStatus("已读取保存的关键帧。");
    }
  }

  function animate() {
    const t = performance.now() * 0.00045;
    const breathe = 1 + Math.sin(t * 1.3) * 0.003;
    if (state.group) {
      state.group.scale.setScalar(state.model.scale * breathe);
      state.group.position.x = state.model.position[0];
      state.group.position.y = state.model.position[1];
      state.group.position.z = state.model.position[2];
    }
    state.renderer.render(state.scene, state.camera);
    requestAnimationFrame(animate);
  }

  async function init() {
    updateShotTabs();
    const threeModule = await import("/static/vendor/three.module.min.js");
    window.THREE = window.THREE || threeModule;
    const THREE = window.THREE;

    state.scene = new THREE.Scene();
    state.scene.background = new THREE.Color(0x000000);
    state.camera = new THREE.PerspectiveCamera(32, 1, 0.1, 10000);
    state.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    state.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    state.renderer.outputColorSpace = THREE.SRGBColorSpace;
    viewport.innerHTML = "";
    viewport.appendChild(state.renderer.domElement);

    state.group = new THREE.Group();
    state.scene.add(state.group);
    const payload = await fetch(modelUrl, { cache: "no-store" }).then((res) => {
      if (!res.ok) throw new Error(`model ${res.status}`);
      return res.json();
    });
    state.parts = payload.parts.map((part) => buildMesh(THREE, part));
    state.parts.forEach((mesh) => state.group.add(mesh));

    state.scene.add(new THREE.HemisphereLight(0xffffff, 0x03060a, 2.5));
    const key = new THREE.DirectionalLight(0xffffff, 3.4);
    key.position.set(120, -180, 260);
    state.scene.add(key);
    const rim = new THREE.DirectionalLight(0x5fc8ff, 2.0);
    rim.position.set(-210, 130, 180);
    state.scene.add(rim);
    const fill = new THREE.DirectionalLight(0xffffff, 0.8);
    fill.position.set(0, 160, 80);
    state.scene.add(fill);

    fitScene(payload);
    resize();
    setShot(0);
    loadShots().catch(() => {});
    animate();
    updateStatus("模型已加载，开始拖动。");
  }

  function bindEvents() {
    Object.values(fields).forEach((input) => input.addEventListener("input", syncFieldsToState));
    fields.side.addEventListener("change", syncFieldsToState);
    fields.motion.addEventListener("change", syncFieldsToState);
    viewport.addEventListener("pointerdown", (event) => {
      const wantsPan = event.button === 2 || event.shiftKey;
      if (event.target === marker) {
        startPointerDrag(event, "text");
      } else if (wantsPan) {
        startPointerDrag(event, "model-pan");
      } else {
        startPointerDrag(event, "model-rotate");
      }
    });
    viewport.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopPointerDrag);
    viewport.addEventListener("contextmenu", (event) => event.preventDefault());
    viewport.addEventListener("wheel", (event) => {
      event.preventDefault();
      const delta = Math.sign(event.deltaY);
      state.model.scale = clamp(state.model.scale * (delta > 0 ? 0.96 : 1.04), 0.12, 3.0);
      fields.scale.value = state.model.scale.toFixed(2);
      syncFieldsToState();
    }, { passive: false });
    captureBtn.addEventListener("click", captureShot);
    saveBtn.addEventListener("click", async () => {
      try {
        captureShot();
        await saveShots();
      } catch (error) {
        updateStatus(String(error.message || error));
      }
    });
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(JSON.stringify(exportShots(), null, 2));
        updateStatus("JSON 已复制到剪贴板。");
      } catch {
        updateStatus("复制失败，请手动选择 JSON 区域。");
      }
    });
    resetBtn.addEventListener("click", () => {
      state.model.position = [0, 0, 0];
      state.model.rotation = [deg(-18), deg(34), 0];
      state.model.scale = 1;
      state.model.explode = 0;
      state.text.x = 66;
      state.text.y = 56;
      state.text.side = "left";
      state.text.motion = "slide-rise";
      syncStateToFields();
      updateStatus("已重置到默认视角。");
    });
    window.addEventListener("resize", resize);
  }

  bindEvents();
  syncStateToFields();
  init().catch((error) => {
    viewport.innerHTML = `<div class="pcb-lab-loader">加载失败：${String(error.message || error)}</div>`;
    updateStatus("模型加载失败。");
  });
})();
