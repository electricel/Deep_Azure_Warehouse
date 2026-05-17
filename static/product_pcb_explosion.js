(function () {
  const root = document.getElementById("pcb-explosion-viewer");
  if (!root) return;

  const section = document.getElementById("pcb-explosion-scroll");
  const productSection = document.getElementById("product-scroll");
  const moduleCards = section ? Array.from(section.querySelectorAll(".pcb-module-card")) : [];
  const dataUrl = root.dataset.model || "/static/models/pcb_exploded_mesh.json";
  const keyframesUrl = "/api/pcb-keyframes";
  const shotKeys = ["power", "opto", "buck", "can"];
  const moduleHighlightRefs = {
    power: new Set([
      "Q1", "Q7", "D4", "F7", "F8",
      "C7", "C8", "C9", "C10", "C11", "C12", "C13", "C14",
      "D9", "D10", "R6", "R13", "R14", "R18", "R20",
    ]),
    opto: new Set([
      "U2", "U3", "LED2", "LED3", "Q2", "Q3", "D2", "D8", "F2", "F6",
      "R3", "R4", "R5", "R7", "R8", "R17", "C1", "C2", "C3", "C4",
    ]),
    buck: new Set([
      "U4", "U8", "U28", "L1", "L2", "D3", "D13",
      "C17", "C22", "C24", "C25", "C34", "C35", "C111",
      "R9", "R10", "R19", "R21", "R22", "R28", "R29", "R30", "R31", "R32", "R33", "R34",
    ]),
    can: new Set([
      "D1", "D5", "D7", "L3", "L5", "L6", "U1", "U7",
      "CN1", "CN3", "CN25", "CN30",
      "C15", "C16", "C19", "C20", "C23", "C26", "C27", "C30",
      "R1", "R2", "R11", "R12", "R15", "R16",
    ]),
  };
  let scene;
  let camera;
  let renderer;
  let group;
  let parts = [];
  let moduleHighlightPads = {};
  let savedShots = [];
  let raf = 0;
  let active = false;
  let initialized = false;
  let initializing = false;
  let disposed = false;
  let visibleInViewport = false;
  let observer = null;
  let threeModulePromise = null;
  let modelPayloadPromise = null;
  let keyframesPromise = null;
  let prewarmStarted = false;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function easeInOut(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function softStep(t) {
    t = clamp(t, 0, 1);
    return t * t * (3 - 2 * t);
  }

  function mix(a, b, t) {
    return a + (b - a) * t;
  }

  function mixArray(a, b, t) {
    return [
      mix(a[0] || 0, b[0] || 0, t),
      mix(a[1] || 0, b[1] || 0, t),
      mix(a[2] || 0, b[2] || 0, t),
    ];
  }

  function mixAngle(a, b, t) {
    let delta = (b - a) % (Math.PI * 2);
    if (delta > Math.PI) delta -= Math.PI * 2;
    if (delta < -Math.PI) delta += Math.PI * 2;
    return a + delta * t;
  }

  function mixAngles(a, b, t) {
    return [
      mixAngle(a[0] || 0, b[0] || 0, t),
      mixAngle(a[1] || 0, b[1] || 0, t),
      mixAngle(a[2] || 0, b[2] || 0, t),
    ];
  }

  function revealAfter(local, start, span) {
    return easeInOut(clamp((local - start) / span, 0, 1));
  }

  function refFromPartName(name) {
    const match = String(name || "").match(/\/\s*([^~\/]+)~/);
    return match ? match[1].trim() : "";
  }

  function waitForIdle(timeout) {
    return new Promise((resolve) => {
      if ("requestIdleCallback" in window) {
        window.requestIdleCallback(resolve, { timeout: timeout || 120 });
      } else {
        window.setTimeout(resolve, 0);
      }
    });
  }

  function loadThreeModule() {
    if (!threeModulePromise) {
      threeModulePromise = import("/static/vendor/three.module.min.js");
    }
    return threeModulePromise;
  }

  function loadModelPayload() {
    if (!modelPayloadPromise) {
      modelPayloadPromise = fetch(dataUrl, { cache: "force-cache" }).then((res) => {
        if (!res.ok) throw new Error(`model ${res.status}`);
        return res.json();
      });
    }
    return modelPayloadPromise;
  }

  function ensureKeyframes() {
    if (!keyframesPromise) keyframesPromise = loadKeyframes();
    return keyframesPromise;
  }

  function prewarmAssets() {
    loadThreeModule().catch(() => {});
    loadModelPayload().catch(() => {});
    ensureKeyframes().catch(() => {});
  }

  const fallbackShots = [
    {
      key: "power",
      side: "left",
      title: "Power Input Protection",
      kicker: "01 / Power Input",
      body: "Power entry, reverse protection, TVS clamp, and fuse boundary for the board front end.",
      model: { position: [-4.8, -34.8, 16.3], rotation: [0.3735, -0.2129, 3.1416], scale: 0.86, explode: 0 },
      text: { x: 3.5, y: 50 },
      motion: "slide-rise",
    },
    {
      key: "opto",
      side: "right",
      title: "Opto Isolation Module",
      kicker: "02 / Opto Isolation",
      body: "Isolation block with status indicator, showing the signal path and LED feedback.",
      model: { position: [-20.1, 32.9, 0], rotation: [0.2321, 0.5899, 0.007], scale: 0.87, explode: 0 },
      text: { x: 96.5, y: 50 },
      motion: "side-sweep",
    },
    {
      key: "buck",
      side: "right",
      title: "Buck Converter",
      kicker: "03 / Buck Stage",
      body: "Dual buck section with output rail conversion and surrounding passive network.",
      model: { position: [-12.5, 25.2, 0], rotation: [0.733, -0.3386, -0.1065], scale: 0.9, explode: 0 },
      text: { x: 96.5, y: 50 },
      motion: "magnetic-pull",
    },
    {
      key: "can",
      side: "left",
      title: "CAN Bus Protection",
      kicker: "04 / CAN Protection",
      body: "Three-channel CAN protection, including the input protection network and termination support.",
      model: { position: [-4.8, -7.3, -20.1], rotation: [0.1937, 0.1623, -0.7714], scale: 1.41, explode: 0 },
      text: { x: 3.5, y: 50 },
      motion: "spring-settle",
    },
  ];

  function normalizeShot(shot, index) {
    const fallback = fallbackShots[index] || fallbackShots[0];
    const model = shot.model || {};
    const text = shot.text || {};
    return {
      key: fallback.key,
      side: shot.side || fallback.side,
      title: shot.title || fallback.title,
      kicker: shot.kicker || fallback.kicker,
      body: shot.body || fallback.body,
      motion: shot.motion || fallback.motion,
      model: {
        position: Array.isArray(model.position) ? model.position.slice(0, 3).map(Number) : fallback.model.position.slice(),
        rotation: Array.isArray(model.rotation) ? model.rotation.slice(0, 3).map(Number) : fallback.model.rotation.slice(),
        scale: Number(model.scale ?? fallback.model.scale),
        explode: Number(model.explode ?? fallback.model.explode),
      },
      text: {
        x: Number(text.x ?? fallback.text.x),
        y: Number(text.y ?? fallback.text.y),
      },
    };
  }

  function progressFromScroll() {
    if (!section) return 0;
    const rect = section.getBoundingClientRect();
    const total = Math.max(1, rect.height - window.innerHeight);
    return clamp(-rect.top / total, 0, 1);
  }

  function makeMaterial(part) {
    const THREE = window.THREE;
    const source = Array.isArray(part.color) ? part.color : null;
    const c = part.kind === "board"
      ? [0.02, 0.28, 0.72]
      : (source ? source.map((v) => Math.min(1, v * 1.08 + 0.06)) : [0.9, 0.9, 0.88]);
    return new THREE.MeshStandardMaterial({
      color: new THREE.Color(c[0], c[1], c[2]),
      metalness: part.kind === "board" ? 0.16 : 0.28,
      roughness: part.kind === "board" ? 0.48 : 0.42,
      emissive: part.kind === "board" ? new THREE.Color(0, 0.025, 0.08) : new THREE.Color(0.018, 0.018, 0.018),
    });
  }

  function verticalLiftFor(part, index) {
    if (part.kind === "board") return -9;
    const size = part.bounds.size;
    const footprint = Math.max(size[0] * size[1], 1);
    const height = Math.max(size[2], 0.2);
    if (footprint > 520 || height > 13) return 78 + (index % 3) * 5;
    if (footprint > 160 || height > 7) return 56 + (index % 4) * 4;
    if (footprint > 45 || height > 3.2) return 36 + (index % 5) * 3;
    return 22 + (index % 6) * 2;
  }

  function shouldDrawEdges(part) {
    if (part.kind === "board") return true;
    const size = part.bounds && Array.isArray(part.bounds.size) ? part.bounds.size : [0, 0, 0];
    const footprint = Math.max(size[0] * size[1], 0);
    const height = Math.max(size[2], 0);
    return footprint > 150 || height > 6;
  }

  function buildMesh(part, index) {
    const THREE = window.THREE;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(part.positions, 3));
    if (part.normals && part.normals.length) {
      geometry.setAttribute("normal", new THREE.Float32BufferAttribute(part.normals, 3));
    } else {
      geometry.computeVertexNormals();
    }
    geometry.setIndex(part.indices);
    geometry.computeBoundingSphere();
    const material = makeMaterial(part);
    const mesh = new THREE.Mesh(geometry, material);
    if (shouldDrawEdges(part)) {
      const edge = new THREE.LineSegments(
        new THREE.EdgesGeometry(geometry, part.kind === "board" ? 18 : 24),
        new THREE.LineBasicMaterial({
          color: part.kind === "board" ? 0x35a9ff : 0x151a20,
          transparent: true,
          opacity: part.kind === "board" ? 0.68 : 0.28,
        })
      );
      edge.userData.baseColor = edge.material.color.clone();
      edge.userData.baseOpacity = edge.material.opacity;
      mesh.add(edge);
    }
    mesh.userData.kind = part.kind;
    mesh.userData.ref = refFromPartName(part.name);
    mesh.userData.baseColor = material.color.clone();
    mesh.userData.baseEmissive = material.emissive.clone();
    mesh.userData.verticalLift = verticalLiftFor(part, index);
    return mesh;
  }

  function moduleHighlightStrength(mesh, moduleKey, local, enterDetail, time) {
    if (!mesh.userData.ref) return 0;
    const refs = moduleHighlightRefs[moduleKey];
    if (!refs || !refs.has(mesh.userData.ref)) return 0;
    const inShot = revealAfter(local, 0.12, 0.2);
    const outShot = 1 - revealAfter(local, 0.78, 0.16);
    const breath = 0.58 + Math.sin(time * 0.0048) * 0.42;
    return clamp(enterDetail * inShot * outShot * (0.55 + breath * 0.45), 0, 1);
  }

  function modulePulseStrength(moduleKey, activeModuleKey, local, enterDetail, time) {
    if (moduleKey !== activeModuleKey) return 0;
    const inShot = revealAfter(local, 0.08, 0.18);
    const outShot = 1 - revealAfter(local, 0.82, 0.14);
    const breath = 0.55 + Math.sin(time * 0.0048) * 0.45;
    return clamp(enterDetail * inShot * outShot * (0.45 + breath * 0.55), 0, 1);
  }

  function applyMeshHighlight(mesh, moduleKey, local, enterDetail, time) {
    const strength = moduleHighlightStrength(mesh, moduleKey, local, enterDetail, time);
    const material = mesh.material;
    if (!material || !mesh.userData.baseColor || !mesh.userData.baseEmissive) return;
    const isBoard = mesh.userData.kind === "board";
    const highlightColor = moduleKey === "power"
      ? [1.0, 0.72, 0.22]
      : moduleKey === "opto"
        ? [0.18, 0.9, 1.0]
        : moduleKey === "buck"
          ? [0.55, 1.0, 0.45]
          : [0.42, 0.68, 1.0];
    const THREE = window.THREE;
    const glow = new THREE.Color(highlightColor[0], highlightColor[1], highlightColor[2]);
    material.color.copy(mesh.userData.baseColor).lerp(glow, isBoard ? strength * 0.18 : strength * 0.78);
    material.emissive.copy(mesh.userData.baseEmissive).lerp(glow, isBoard ? strength * 0.08 : strength * 0.95);
    material.needsUpdate = true;
    for (const child of mesh.children) {
      if (!child.material || child.type !== "LineSegments") continue;
      if (child.userData.baseColor) {
        child.material.color.copy(child.userData.baseColor).lerp(glow, strength);
      }
      child.material.opacity = (child.userData.baseOpacity ?? 0.28) + strength * 0.58;
      child.material.needsUpdate = true;
    }
  }

  function moduleGlowColor(moduleKey) {
    if (moduleKey === "power") return [1.0, 0.66, 0.16];
    if (moduleKey === "opto") return [0.12, 0.92, 1.0];
    if (moduleKey === "buck") return [0.5, 1.0, 0.34];
    return [0.78, 0.42, 1.0];
  }

  function buildModuleHighlightPads(sourceParts) {
    const THREE = window.THREE;
    moduleHighlightPads = {};
    for (const [moduleKey, refs] of Object.entries(moduleHighlightRefs)) {
      const bounds = {
        min: [Infinity, Infinity, Infinity],
        max: [-Infinity, -Infinity, -Infinity],
        count: 0,
      };
      for (const part of sourceParts) {
        if (!refs.has(refFromPartName(part.name))) continue;
        bounds.count += 1;
        for (let axis = 0; axis < 3; axis += 1) {
          bounds.min[axis] = Math.min(bounds.min[axis], part.bounds.min[axis]);
          bounds.max[axis] = Math.max(bounds.max[axis], part.bounds.max[axis]);
        }
      }
      if (!bounds.count) continue;
      const width = Math.max(6, bounds.max[0] - bounds.min[0] + 5.4);
      const height = Math.max(6, bounds.max[1] - bounds.min[1] + 5.4);
      const centerX = (bounds.min[0] + bounds.max[0]) / 2;
      const centerY = (bounds.min[1] + bounds.max[1]) / 2;
      const color = moduleGlowColor(moduleKey);
      const pad = new THREE.Mesh(
        new THREE.PlaneGeometry(width, height),
        new THREE.MeshBasicMaterial({
          color: new THREE.Color(color[0], color[1], color[2]),
          transparent: true,
          opacity: 0,
          depthWrite: false,
          depthTest: false,
          blending: THREE.AdditiveBlending,
          side: THREE.DoubleSide,
        })
      );
      pad.position.set(centerX, centerY, bounds.max[2] + 0.18);
      pad.userData.moduleKey = moduleKey;
      pad.userData.baseScale = 1;
      pad.renderOrder = 20;
      const hw = width / 2;
      const hh = height / 2;
      const ringGeometry = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(-hw, -hh, 0),
        new THREE.Vector3(hw, -hh, 0),
        new THREE.Vector3(hw, -hh, 0),
        new THREE.Vector3(hw, hh, 0),
        new THREE.Vector3(hw, hh, 0),
        new THREE.Vector3(-hw, hh, 0),
        new THREE.Vector3(-hw, hh, 0),
        new THREE.Vector3(-hw, -hh, 0),
      ]);
      const ring = new THREE.LineSegments(
        ringGeometry,
        new THREE.LineBasicMaterial({
          color: new THREE.Color(color[0], color[1], color[2]),
          transparent: true,
          opacity: 0,
          depthWrite: false,
          depthTest: false,
          blending: THREE.AdditiveBlending,
        })
      );
      ring.position.copy(pad.position);
      ring.renderOrder = 21;
      moduleHighlightPads[moduleKey] = { pad, ring };
      group.add(pad);
      group.add(ring);
    }
  }

  function updateModuleHighlightPads(activeModuleKey, local, enterDetail, time) {
    for (const [moduleKey, item] of Object.entries(moduleHighlightPads)) {
      const strength = modulePulseStrength(moduleKey, activeModuleKey, local, enterDetail, time);
      const scale = 1 + strength * 0.045;
      item.pad.material.opacity = strength * 0.42;
      item.pad.scale.set(scale, scale, 1);
      item.pad.visible = strength > 0.01;
      item.pad.material.needsUpdate = true;
      item.ring.material.opacity = strength * 0.92;
      item.ring.scale.set(scale + 0.02, scale + 0.02, 1);
      item.ring.visible = strength > 0.01;
      item.ring.material.needsUpdate = true;
    }
  }

  function fitCamera(payload) {
    const THREE = window.THREE;
    const size = payload.bounds.size;
    const center = payload.bounds.center;
    const maxSize = Math.max(size[0], size[1], size[2], 1);
    camera.position.set(center[0] + maxSize * 0.05, center[1] - maxSize * 1.05, center[2] + maxSize * 0.72);
    camera.near = 0.1;
    camera.far = maxSize * 20;
    camera.lookAt(new THREE.Vector3(center[0], center[1], center[2]));
    camera.updateProjectionMatrix();
    group.userData.basePosition = new THREE.Vector3(-center[0], -center[1], -center[2]);
    group.position.copy(group.userData.basePosition);
  }

  function resize() {
    if (!renderer || !camera) return;
    const rect = root.getBoundingClientRect();
    const width = Math.max(320, Math.floor(rect.width));
    const height = Math.max(320, Math.floor(rect.height));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }

  function currentDetailShot(progress) {
    const detailStart = 0.34;
    const detailEnd = 0.94;
    const detail = clamp((progress - detailStart) / (detailEnd - detailStart), 0, 1);
    const n = savedShots.length;
    const raw = Math.min(n - 0.0001, detail * n);
    const index = clamp(Math.min(n - 1, Math.floor(raw)), 0, n - 1);
    const nextIndex = Math.min(n - 1, index + 1);
    const local = clamp(raw - index, 0, 1);
    const hold = local < 0.84 ? 0 : softStep((local - 0.84) / 0.16);
    const hasNextShot = nextIndex !== index;
    return {
      index,
      nextIndex,
      local,
      t: hasNextShot ? hold : 0,
      motion: easeInOut(clamp(local / 0.28, 0, 1)),
      enter: easeInOut(clamp((progress - 0.315) / 0.085, 0, 1)),
      hasNextShot,
    };
  }

  function applyTextState(section, shot, nextShot, blend, detailState, visibility = 1) {
    const textX = mix(shot.text.x, nextShot.text.x, blend);
    const textY = mix(shot.text.y, nextShot.text.y, blend);
    const isLeftPlacement = textX < 50;
    const sideShift = isLeftPlacement ? -1 : 1;
    const settle = detailState.motion;
    const drift = isLeftPlacement ? -1 : 1;
    section.style.setProperty("--pcb-text-x", `${textX}%`);
    section.style.setProperty("--pcb-text-y", `${textY}%`);
    section.style.setProperty("--pcb-text-enter-x", `${sideShift * mix(150, 0, settle)}px`);
    section.style.setProperty("--pcb-text-enter-y", `${mix(58, -8, settle)}px`);
    section.style.setProperty("--pcb-text-scale", String(mix(0.82, 1.02, settle)));
    section.style.setProperty("--pcb-text-opacity", String(clamp(detailState.enter * (0.2 + settle * 0.9) * visibility, 0, 1)));
    section.style.setProperty("--pcb-text-align", isLeftPlacement ? "left" : "right");
    section.style.setProperty("--pcb-text-anchor-x", isLeftPlacement ? "0%" : "-100%");
    section.style.setProperty("--pcb-line-drift-x", `${drift * 86}px`);
    section.style.setProperty("--pcb-copy-step", detailState.local.toFixed(3));
    const kicker = revealAfter(detailState.local, 0.04, 0.18);
    const title = revealAfter(detailState.local, 0.1, 0.22);
    const body = revealAfter(detailState.local, 0.22, 0.24);
    const meta = revealAfter(detailState.local, 0.36, 0.24);
    section.style.setProperty("--pcb-copy-kicker", kicker.toFixed(3));
    section.style.setProperty("--pcb-copy-title", title.toFixed(3));
    section.style.setProperty("--pcb-copy-body", body.toFixed(3));
    section.style.setProperty("--pcb-copy-meta", meta.toFixed(3));
  }

  function hideModuleCopy() {
    if (!section) return;
    section.style.setProperty("--pcb-text-opacity", "0");
    section.style.setProperty("--pcb-copy-kicker", "0");
    section.style.setProperty("--pcb-copy-title", "0");
    section.style.setProperty("--pcb-copy-body", "0");
    section.style.setProperty("--pcb-copy-meta", "0");
    moduleCards.forEach((card) => card.classList.remove("is-active"));
  }

  function compositionFor(shot) {
    if (shot.key === "buck") return 42;
    if (shot.key === "can") return 58;
    return shot.side === "right" ? -18 : 42;
  }

  function renderFrame() {
    if (!active || disposed || !scene || !camera || !renderer || !group) {
      raf = 0;
      return;
    }
    const THREE = window.THREE;
    const progress = progressFromScroll();
    const sectionRect = section ? section.getBoundingClientRect() : null;
    const productRect = productSection ? productSection.getBoundingClientRect() : null;
    const sectionExitFade = sectionRect
      ? clamp(sectionRect.bottom / Math.max(window.innerHeight * 0.55, 1), 0, 1)
      : 1;
    const productEntryFade = productRect
      ? clamp((productRect.top - window.innerHeight * 0.72) / Math.max(window.innerHeight * 0.18, 1), 0, 1)
      : 1;
    const textVisibility = Math.min(sectionExitFade, productEntryFade);
    const base = group.userData.basePosition || new THREE.Vector3(0, 0, 0);
    const collapse = easeInOut(clamp((progress - 0.08) / 0.17, 0, 1));
    const overview = easeInOut(clamp((progress - 0.18) / 0.16, 0, 1));
    const detailState = currentDetailShot(progress);
    const current = savedShots[detailState.index] || fallbackShots[0];
    const next = savedShots[detailState.nextIndex] || current;
    const enterDetail = detailState.enter;
    const explode = mix(1, current.model.explode, enterDetail) * (1 - collapse + current.model.explode * collapse);
    const focusPos = mixArray(current.model.position, next.model.position, detailState.t);
    const focusRot = mixAngles(current.model.rotation, next.model.rotation, detailState.t);
    const focusScale = mix(current.model.scale, next.model.scale, detailState.t);
    const compositionOffset = mix(compositionFor(current), compositionFor(next), detailState.t);
    const overviewRot = [0.12 + overview * 0.5, -0.08 + overview * 0.62, -0.04 - overview * 0.22];
    const scale = mix(mix(0.5, 0.6, overview), focusScale, enterDetail);
    const slide = easeInOut(clamp((progress - 0.275) / 0.14, 0, 1));
    const now = performance.now();

    group.rotation.x = mix(overviewRot[0], focusRot[0], enterDetail);
    const transitionNudge = detailState.hasNextShot ? Math.sin(detailState.t * Math.PI) * 0.06 : 0;
    group.rotation.y = mix(overviewRot[1], focusRot[1], enterDetail) + transitionNudge;
    group.rotation.z = mix(overviewRot[2], focusRot[2], enterDetail);
    group.position.x = mix(base.x + slide * 10, focusPos[0] + compositionOffset, enterDetail);
    group.position.y = mix(base.y - slide * 6, focusPos[1], enterDetail);
    group.position.z = mix(base.z, focusPos[2], enterDetail);
    group.scale.setScalar(scale);

    for (const mesh of parts) {
      mesh.position.set(0, 0, mesh.userData.verticalLift * explode);
      mesh.rotation.set(0, 0, 0);
      applyMeshHighlight(mesh, current.key, detailState.local, enterDetail, now);
    }
    updateModuleHighlightPads(current.key, detailState.local, enterDetail, now);

    root.style.setProperty("--pcb-progress", progress.toFixed(3));
    root.style.setProperty("--pcb-explode", explode.toFixed(3));
    root.style.setProperty("--pcb-detail", enterDetail.toFixed(3));
    if (section) {
      section.style.setProperty("--pcb-progress", progress.toFixed(3));
      section.style.setProperty("--pcb-explode", explode.toFixed(3));
      section.style.setProperty("--pcb-detail", enterDetail.toFixed(3));
      section.style.setProperty("--pcb-module-local", detailState.local.toFixed(3));
      section.style.setProperty("--pcb-module-motion", detailState.motion.toFixed(3));
      section.dataset.module = current.key;
      section.dataset.phase = progress < 0.12 ? "exploded" : (progress < 0.34 ? "overview" : "detail");
      section.dataset.side = current.side || "left";
      section.dataset.motion = current.motion || "slide-rise";
      applyTextState(section, current, next, detailState.t, detailState, textVisibility);
      moduleCards.forEach((card) => {
        card.classList.toggle("is-active", card.dataset.module === current.key && enterDetail > 0.2 && textVisibility > 0.02);
      });
    }
    renderer.render(scene, camera);
    raf = active ? requestAnimationFrame(renderFrame) : 0;
  }

  function stopRenderLoop() {
    active = false;
    if (raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
    hideModuleCopy();
  }

  function startRenderLoop() {
    if (disposed || !initialized || document.visibilityState === "hidden") return;
    if (!visibleInViewport) return;
    if (active && raf) return;
    active = true;
    renderFrame();
  }

  function disposeScene(event) {
    if (event && event.persisted) {
      stopRenderLoop();
      return;
    }
    disposed = true;
    stopRenderLoop();
    window.removeEventListener("resize", resize);
    document.removeEventListener("visibilitychange", handleVisibilityChange);
    window.removeEventListener("pagehide", disposeScene);
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    threeModulePromise = null;
    modelPayloadPromise = null;
    keyframesPromise = null;
    if (group) {
      group.traverse((node) => {
        if (node.geometry && typeof node.geometry.dispose === "function") node.geometry.dispose();
        const materials = Array.isArray(node.material) ? node.material : [node.material];
        materials.filter(Boolean).forEach((material) => {
          if (typeof material.dispose === "function") material.dispose();
        });
      });
    }
    if (renderer) {
      renderer.dispose();
      if (typeof renderer.forceContextLoss === "function") renderer.forceContextLoss();
      if (renderer.domElement && renderer.domElement.parentNode) {
        renderer.domElement.parentNode.removeChild(renderer.domElement);
      }
    }
    scene = null;
    camera = null;
    renderer = null;
    group = null;
    parts = [];
    moduleHighlightPads = {};
  }

  function handleVisibilityChange() {
    if (document.visibilityState === "hidden") {
      stopRenderLoop();
    } else {
      startRenderLoop();
    }
  }

  async function loadKeyframes() {
    try {
      const payload = await fetch(keyframesUrl, { cache: "no-store" }).then((res) => {
        if (!res.ok) throw new Error(`keyframes ${res.status}`);
        return res.json();
      });
      if (Array.isArray(payload.shots) && payload.shots.length === 4) {
        savedShots = payload.shots.map(normalizeShot);
        root.dataset.keyframes = payload.updated_at || "saved";
        return;
      }
    } catch (error) {
      root.dataset.keyframes = "fallback";
    }
    savedShots = fallbackShots.map(normalizeShot);
  }

  async function buildMeshesIncrementally(payload) {
    parts = [];
    const sourceParts = Array.isArray(payload.parts) ? payload.parts : [];
    const chunkSize = 28;
    for (let index = 0; index < sourceParts.length; index += 1) {
      if (disposed) return;
      const mesh = buildMesh(sourceParts[index], index);
      parts.push(mesh);
      group.add(mesh);
      if (index % chunkSize === chunkSize - 1) {
        root.dataset.loadedParts = String(index + 1);
        await waitForIdle(80);
      }
    }
    buildModuleHighlightPads(sourceParts);
  }

  async function init() {
    if (initialized || initializing || disposed) return;
    initializing = true;
    root.dataset.state = "loading";
    try {
      prewarmAssets();
      const threeModule = await loadThreeModule();
      if (disposed) return;
      window.THREE = window.THREE || threeModule;
      const THREE = window.THREE;
      const [payload] = await Promise.all([loadModelPayload(), ensureKeyframes()]);
      if (disposed) return;

      scene = new THREE.Scene();
      scene.background = null;
      camera = new THREE.PerspectiveCamera(32, 1, 0.1, 10000);
      renderer = new THREE.WebGLRenderer({
        antialias: window.devicePixelRatio <= 1.5,
        alpha: true,
        powerPreference: "high-performance",
      });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      root.innerHTML = "";
      root.appendChild(renderer.domElement);

      group = new THREE.Group();
      scene.add(group);

      const hemi = new THREE.HemisphereLight(0xffffff, 0x0a1018, 2.2);
      scene.add(hemi);
      const key = new THREE.DirectionalLight(0xffffff, 3.6);
      key.position.set(140, -200, 280);
      scene.add(key);
      const rim = new THREE.DirectionalLight(0x7de8ff, 2.4);
      rim.position.set(-220, 110, 190);
      scene.add(rim);
      const fill = new THREE.DirectionalLight(0xffffff, 0.9);
      fill.position.set(80, 160, 80);
      scene.add(fill);

      await buildMeshesIncrementally(payload);
      if (disposed) return;
      fitCamera(payload);
      resize();
      initialized = true;
      root.dataset.state = "ready";
      root.dataset.parts = String(payload.partCount || parts.length);
      startRenderLoop();
    } finally {
      initializing = false;
    }
  }

  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", handleVisibilityChange);
  window.addEventListener("pagehide", disposeScene);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) startRenderLoop();
  });

  if ("IntersectionObserver" in window) {
    observer = new IntersectionObserver((entries) => {
      const entry = entries.find((item) => item.target === (section || root)) || entries[0];
      const ratio = entry ? entry.intersectionRatio : 0;
      visibleInViewport = entries.some((item) => item.isIntersecting && item.intersectionRatio > 0.06);
      if (entry && entry.isIntersecting && !prewarmStarted) {
        prewarmStarted = true;
        prewarmAssets();
      }
      if (visibleInViewport) {
        init().catch((error) => {
          root.dataset.state = "error";
          root.innerHTML = `<div class="pcb-explosion-error">3D STEP model failed: ${String(error.message || error)}</div>`;
        });
        startRenderLoop();
      } else {
        stopRenderLoop();
      }
    }, { rootMargin: "1400px 0px", threshold: [0, 0.01, 0.08] });
    observer.observe(section || root);
  } else {
    visibleInViewport = true;
    init().catch((error) => {
      root.dataset.state = "error";
      root.innerHTML = `<div class="pcb-explosion-error">3D STEP model failed: ${String(error.message || error)}</div>`;
    });
  }

  waitForIdle(1500).then(() => {
    if (!disposed && !prewarmStarted) {
      prewarmStarted = true;
      prewarmAssets();
    }
  });
})();
