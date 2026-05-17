(function () {
  const board = document.getElementById("workflow-3d-board");
  const detail = document.getElementById("workflow-detail");
  const source = board ? board.dataset.source : "";
  let payload = null;
  let zoom = 1;
  let tiltX = -8;
  let tiltY = 10;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function esc(value) {
    return String(value || "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function setDetail(project, stage) {
    if (!detail) return;
    detail.innerHTML = `
      <strong>${esc(project.name)} · ${esc(stage.title)}</strong>
      <span>${esc(stage.status_label)} / ${stage.progress || 0}% / 第 ${stage.start_day || 1}-${stage.end_day || 1} 天</span>
      <p>${esc(stage.detail)}</p>
      <em>负责人 ${esc(stage.owner_username || project.owner_username)}，项目组 ${esc(project.owner_group || "未分组")}</em>
    `;
  }

  function updateTransform() {
    if (!board) return;
    board.style.setProperty("--wf-zoom", zoom.toFixed(2));
    board.style.setProperty("--wf-tilt-x", `${tiltX.toFixed(2)}deg`);
    board.style.setProperty("--wf-tilt-y", `${tiltY.toFixed(2)}deg`);
  }

  function collectNodes(projects) {
    const nodes = [];
    projects.slice(0, 5).forEach((project, projectIndex) => {
      const stages = project.stages || [];
      stages.forEach((stage, stageIndex) => {
        nodes.push({ project, stage, projectIndex, stageIndex, count: stages.length || 1 });
      });
    });
    return nodes;
  }

  function render() {
    if (!board || !payload) return;
    const projects = payload.projects || [];
    const avg = projects.length
      ? Math.round(projects.reduce((sum, item) => sum + (Number(item.progress) || 0), 0) / projects.length)
      : 0;
    if (!projects.length) {
      board.innerHTML = `
        <div class="workflow-core-node"><strong>0%</strong><span>等待项目</span></div>
        <div class="workflow-orbit-empty">创建工作流后，项目会在这里生成实时 3D 进度节点。</div>
      `;
      updateTransform();
      return;
    }
    const nodes = collectNodes(projects);
    const nodeHtml = nodes.map((item, index) => {
      const angle = ((item.stageIndex / item.count) * 360) + item.projectIndex * 31;
      const radius = 150 + item.projectIndex * 42;
      const x = Math.cos(angle * Math.PI / 180) * radius;
      const y = Math.sin(angle * Math.PI / 180) * (radius * 0.45);
      const z = (item.projectIndex - 2) * 46 + Math.sin(angle * Math.PI / 180) * 38;
      const size = 84 + clamp(Number(item.stage.progress) || 0, 0, 100) * 0.36;
      return `
        <button
          class="workflow-stage-node"
          style="--node-x:${x.toFixed(1)}px;--node-y:${y.toFixed(1)}px;--node-z:${z.toFixed(1)}px;--node-size:${size.toFixed(1)}px;--node-color:${esc(item.stage.color || "#0a84ff")};"
          data-index="${index}"
          type="button"
        >
          <span>${esc(item.stage.category || "stage")}</span>
          <strong>${esc(item.stage.title)}</strong>
          <em>${item.stage.progress || 0}%</em>
        </button>
      `;
    }).join("");
    const projectRail = projects.slice(0, 5).map((project) => `
      <article>
        <strong>${esc(project.name)}</strong>
        <span>${esc(project.owner_username)} · ${esc(project.status_label)} · ${project.progress || 0}%</span>
      </article>
    `).join("");
    board.innerHTML = `
      <div class="workflow-orbit">
        <div class="workflow-core-node"><strong>${avg}%</strong><span>总完成度</span></div>
        ${nodeHtml}
      </div>
      <div class="workflow-project-rail">${projectRail}</div>
    `;
    board.querySelectorAll(".workflow-stage-node").forEach((node) => {
      node.addEventListener("click", () => {
        const item = nodes[Number(node.dataset.index)];
        if (item) setDetail(item.project, item.stage);
      });
    });
    if (nodes[0]) setDetail(nodes[0].project, nodes[0].stage);
    updateTransform();
  }

  async function load() {
    if (!source) return;
    try {
      const response = await fetch(source, { cache: "no-store" });
      payload = await response.json();
      render();
    } catch (error) {
      if (detail) detail.textContent = `工作流看板载入失败：${error.message}`;
    }
  }

  if (board) {
    board.addEventListener("mousemove", (event) => {
      const rect = board.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      tiltY = clamp(x * 22, -16, 16);
      tiltX = clamp(-8 - y * 16, -20, 8);
      updateTransform();
    });
    board.addEventListener("mouseleave", () => {
      tiltX = -8;
      tiltY = 10;
      updateTransform();
    });
    board.addEventListener("wheel", (event) => {
      event.preventDefault();
      zoom = clamp(zoom + (event.deltaY > 0 ? -0.05 : 0.05), 0.86, 1.18);
      updateTransform();
    }, { passive: false });
    load();
    window.setInterval(load, 8000);
  }

  const probe = document.getElementById("workflow-model-probe");
  const endpoint = document.getElementById("workflow-ai-endpoint");
  const key = document.getElementById("workflow-ai-key");
  const model = document.getElementById("workflow-ai-model");
  const datalist = document.getElementById("workflow-models");
  const steps = document.getElementById("workflow-api-steps");
  if (probe && endpoint && key && datalist) {
    probe.addEventListener("click", async () => {
      steps.textContent = "正在读取 API 模型列表...";
      probe.disabled = true;
      try {
        const response = await fetch("/api/workflow/models", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint: endpoint.value, api_key: key.value }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "模型探测失败");
        datalist.innerHTML = (data.models || []).map((item) => `<option value="${esc(item.id)}">${esc(item.name || item.id)}</option>`).join("");
        if (!model.value && data.models && data.models[0]) model.value = data.models[0].id;
        steps.innerHTML = (data.steps || []).map((item) => `<span>${esc(item)}</span>`).join("");
      } catch (error) {
        steps.textContent = error.message;
      } finally {
        probe.disabled = false;
      }
    });
  }

  document.querySelectorAll('.workflow-update-form input[type="range"]').forEach((range) => {
    range.addEventListener("input", () => {
      range.style.setProperty("--range-value", `${range.value}%`);
    });
  });
})();
