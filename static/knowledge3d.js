(function () {
  const canvases = Array.from(document.querySelectorAll(".knowledge-canvas"));
  if (!canvases.length) return;

  const palette = {
    core: "#dbeafe",
    category: "#60a5fa",
    component: "#22c55e",
    function: "#facc15",
    shortage: "#fb7185",
    memory: "#a78bfa",
    note: "#38bdf8",
    term: "#f59e0b",
  };

  function resize(canvas) {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w: rect.width, h: rect.height };
  }

  function radius(node) {
    const base = node.kind === "core" ? 28 : node.kind === "memory" ? 18 : node.kind === "shortage" ? 16 : 10;
    return Math.min(34, base + Math.log2(Number(node.value || 1) + 1) * 1.4);
  }

  function initScene(nodes) {
    const core = nodes.find((node) => node.kind === "core") || nodes[0];
    nodes.forEach((node, index) => {
      node.r = radius(node);
      if (node === core || node.kind === "core") {
        node.kind = "core";
        node.x3 = 0;
        node.y3 = 0;
        node.z3 = 0;
        return;
      }
      const i = index + 1;
      const angle = i * 2.399963;
      const layer = 110 + (i % 4) * 42 + Math.sqrt(i) * 8;
      const y = ((i % 7) - 3) * 34;
      node.x3 = Math.cos(angle) * layer;
      node.y3 = y;
      node.z3 = Math.sin(angle) * layer;
    });
  }

  function init(canvas) {
    const detail = document.getElementById(canvas.dataset.detail || "graph-detail");
    const graph = { nodes: [], edges: [], projected: [] };
    const state = {
      rotX: -0.34,
      rotY: 0.72,
      zoom: 1,
      dragging: false,
      lastX: 0,
      lastY: 0,
      hover: null,
      selected: null,
      auto: true,
    };
    let size = resize(canvas);

    function nodeMap() {
      return new Map(graph.nodes.map((node) => [node.id, node]));
    }

    function project(node) {
      const cx = size.w / 2;
      const cy = size.h / 2;
      const cosY = Math.cos(state.rotY);
      const sinY = Math.sin(state.rotY);
      const cosX = Math.cos(state.rotX);
      const sinX = Math.sin(state.rotX);
      const x1 = node.x3 * cosY - node.z3 * sinY;
      const z1 = node.x3 * sinY + node.z3 * cosY;
      const y2 = node.y3 * cosX - z1 * sinX;
      const z2 = node.y3 * sinX + z1 * cosX;
      const depth = 620;
      const scale = state.zoom * depth / (depth + z2);
      return {
        node,
        x: cx + x1 * scale,
        y: cy + y2 * scale,
        z: z2,
        scale,
        r: node.r * scale,
      };
    }

    function projected() {
      graph.projected = graph.nodes.map(project);
      return graph.projected;
    }

    function nearest(x, y) {
      let best = null;
      let bestD = Infinity;
      projected().forEach((item) => {
        const d = Math.hypot(item.x - x, item.y - y);
        if (d < item.r + 14 && d < bestD) {
          best = item.node;
          bestD = d;
        }
      });
      return best;
    }

    function updateDetail(node) {
      if (!detail || !node) return;
      const lines = [
        node.label,
        `类型：${node.kind} / 权重：${node.value || 0}`,
        node.category ? `分类：${node.category}` : "",
        node.created_at ? `时间：${node.created_at}` : "",
        node.detail ? `详情：${node.detail}` : "",
      ].filter(Boolean);
      detail.textContent = lines.join("\n");
    }

    function drawRings(ctx, cx, cy) {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(state.rotY * 0.35);
      for (let i = 0; i < 3; i++) {
        ctx.strokeStyle = `rgba(255,255,255,${0.26 - i * 0.04})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.ellipse(0, 0, (150 + i * 70) * state.zoom, (54 + i * 22) * state.zoom, state.rotX + i * 0.24, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    }

    function draw() {
      size = resize(canvas);
      const { ctx, w, h } = size;
      ctx.clearRect(0, 0, w, h);
      const bg = ctx.createRadialGradient(w / 2, h / 2, 10, w / 2, h / 2, Math.max(w, h) * 0.62);
      bg.addColorStop(0, "rgba(255,255,255,0.52)");
      bg.addColorStop(1, "rgba(178,216,235,0.12)");
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, w, h);
      drawRings(ctx, w / 2, h / 2);

      const map = nodeMap();
      const points = projected();
      const pointById = new Map(points.map((item) => [item.node.id, item]));

      graph.edges.forEach((edge) => {
        const a = pointById.get(edge.from);
        const b = pointById.get(edge.to);
        if (!a || !b) return;
        const alpha = Math.max(0.12, Math.min(0.42, 0.22 + ((a.z + b.z) / 1200)));
        ctx.strokeStyle = edge.alert ? `rgba(251,113,133,${alpha})` : `rgba(65,88,112,${alpha})`;
        ctx.lineWidth = Math.max(0.7, Math.min(2.6, Math.log2(Number(edge.weight || 1) + 1))) * state.zoom;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      });

      points.sort((a, b) => a.z - b.z).forEach((item) => {
        const node = item.node;
        const color = palette[node.kind] || palette.component;
        const selected = node === state.selected || node === state.hover;
        ctx.save();
        ctx.globalAlpha = Math.max(0.48, Math.min(1, 0.72 + item.z / 620));
        ctx.shadowColor = color;
        ctx.shadowBlur = selected ? 26 : 12;
        const grad = ctx.createRadialGradient(item.x - item.r * 0.3, item.y - item.r * 0.35, 2, item.x, item.y, item.r * 1.2);
        grad.addColorStop(0, "rgba(255,255,255,0.78)");
        grad.addColorStop(1, color);
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(item.x, item.y, item.r, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.strokeStyle = node.kind === "core" ? "rgba(255,255,255,0.98)" : "rgba(255,255,255,0.64)";
        ctx.lineWidth = node.kind === "core" ? 3 : selected ? 2.4 : 1.2;
        ctx.stroke();
        if (selected || node.kind === "core" || node.kind === "memory") {
          ctx.font = `${Math.max(11, 13 * item.scale)}px -apple-system, BlinkMacSystemFont, Segoe UI, Microsoft YaHei, sans-serif`;
          ctx.fillStyle = "rgba(31,41,55,0.9)";
          ctx.textAlign = "center";
          const label = node.label.length > 18 ? `${node.label.slice(0, 17)}...` : node.label;
          ctx.fillText(label, item.x, item.y - item.r - 9);
        }
        ctx.restore();
      });
    }

    function loop() {
      if (state.auto && !state.dragging) {
        state.rotY += 0.0022;
      }
      draw();
      requestAnimationFrame(loop);
    }

    function load() {
      fetch(canvas.dataset.source || "/api/knowledge_graph")
        .then((res) => res.json())
        .then((data) => {
          graph.nodes = data.nodes || [];
          graph.edges = data.edges || [];
          size = resize(canvas);
          initScene(graph.nodes);
          const core = graph.nodes.find((node) => node.kind === "core");
          state.selected = core || null;
          if (state.selected) updateDetail(state.selected);
          draw();
        })
        .catch((err) => {
          if (detail) detail.textContent = `知识网络读取失败：${err.message}`;
        });
    }

    canvas.addEventListener("pointerdown", (event) => {
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      state.selected = nearest(x, y) || state.selected;
      if (state.selected) updateDetail(state.selected);
      state.dragging = true;
      state.auto = false;
      state.lastX = event.clientX;
      state.lastY = event.clientY;
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", (event) => {
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      if (state.dragging) {
        state.rotY += (event.clientX - state.lastX) * 0.006;
        state.rotX = Math.max(-1.05, Math.min(1.05, state.rotX + (event.clientY - state.lastY) * 0.005));
        state.lastX = event.clientX;
        state.lastY = event.clientY;
      } else {
        state.hover = nearest(x, y);
        if (state.hover) updateDetail(state.hover);
      }
    });
    canvas.addEventListener("pointerup", () => {
      state.dragging = false;
      window.setTimeout(() => (state.auto = true), 1200);
    });
    canvas.addEventListener("pointerleave", () => {
      state.dragging = false;
      state.hover = null;
      window.setTimeout(() => (state.auto = true), 1200);
    });
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      state.zoom = Math.max(0.48, Math.min(2.6, state.zoom - event.deltaY * 0.0012));
    }, { passive: false });
    window.addEventListener("resize", () => (size = resize(canvas)));
    window.addEventListener("knowledge:refresh", load);
    load();
    loop();
  }

  canvases.forEach(init);
})();
