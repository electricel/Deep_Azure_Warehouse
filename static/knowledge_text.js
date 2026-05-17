(function () {
  const root = document.getElementById("knowledge-text-network");
  const detail = document.getElementById("graph-detail");
  if (!root || !detail) return;

  const KIND_LABELS = {
    core: "库存核心",
    category: "库存分类",
    component: "库存器件",
    function: "功能关联",
    shortage: "BOM 缺口",
    memory: "助手记忆",
    note: "问答节点",
    term: "关键词",
  };

  const KIND_ORDER = ["core", "category", "component", "function", "shortage", "memory", "note", "term"];
  let nodes = [];
  let edges = [];
  let byId = new Map();
  let selectedId = "";

  function text(value) {
    return String(value == null ? "" : value);
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function nodeLabel(node) {
    return text(node && node.label).trim() || "未命名节点";
  }

  function kindLabel(kind) {
    return KIND_LABELS[kind] || "知识节点";
  }

  function edgeWeight(edge) {
    const weight = Number(edge && edge.weight);
    return Number.isFinite(weight) && weight > 0 ? Math.round(weight) : 1;
  }

  function neighbors(id) {
    const seen = new Set();
    return edges
      .map((edge) => {
        if (edge.from === id) return { edge, node: byId.get(edge.to), direction: "to" };
        if (edge.to === id) return { edge, node: byId.get(edge.from), direction: "from" };
        return null;
      })
      .filter((item) => {
        if (!item || !item.node || seen.has(item.node.id)) return false;
        seen.add(item.node.id);
        return true;
      })
      .sort((a, b) => edgeWeight(b.edge) - edgeWeight(a.edge))
      .slice(0, 18);
  }

  function setSelected(id) {
    selectedId = id;
    root.querySelectorAll(".knowledge-node-card").forEach((button) => {
      button.classList.toggle("selected", button.dataset.nodeId === selectedId);
      button.setAttribute("aria-pressed", button.dataset.nodeId === selectedId ? "true" : "false");
    });
    renderDetail(byId.get(id));
  }

  function makeNodeButton(node, compact) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = compact ? "knowledge-node-chip" : "knowledge-node-card";
    button.dataset.nodeId = node.id;
    button.setAttribute("aria-pressed", node.id === selectedId ? "true" : "false");

    const title = document.createElement("strong");
    title.textContent = nodeLabel(node);
    const meta = document.createElement("span");
    const parts = [kindLabel(node.kind)];
    if (node.category) parts.push(text(node.category));
    if (node.value != null && node.value !== "") parts.push(`权重 ${text(node.value)}`);
    meta.textContent = parts.join(" / ");
    button.append(title, meta);
    if (node.id === selectedId) button.classList.add("selected");
    button.addEventListener("click", () => setSelected(node.id));
    return button;
  }

  function renderNetwork() {
    clear(root);
    if (!nodes.length) {
      const empty = document.createElement("div");
      empty.className = "knowledge-empty";
      empty.textContent = "暂无知识节点。和库存助手对话后，这里会生成文字关联。";
      root.appendChild(empty);
      return;
    }

    const grouped = new Map();
    nodes.forEach((node) => {
      const kind = node.kind || "term";
      if (!grouped.has(kind)) grouped.set(kind, []);
      grouped.get(kind).push(node);
    });

    KIND_ORDER.concat(
      Array.from(grouped.keys()).filter((kind) => !KIND_ORDER.includes(kind))
    ).forEach((kind) => {
      const group = grouped.get(kind);
      if (!group || !group.length) return;

      const section = document.createElement("section");
      section.className = "knowledge-section";
      const heading = document.createElement("div");
      heading.className = "knowledge-section-head";
      const title = document.createElement("h3");
      title.textContent = kindLabel(kind);
      const count = document.createElement("span");
      count.textContent = `${group.length} 个节点`;
      heading.append(title, count);

      const list = document.createElement("div");
      list.className = "knowledge-node-list";
      group
        .slice()
        .sort((a, b) => Number(b.value || 0) - Number(a.value || 0))
        .forEach((node) => list.appendChild(makeNodeButton(node)));

      section.append(heading, list);
      root.appendChild(section);
    });
  }

  function addDetailLine(container, label, value) {
    if (value == null || value === "") return;
    const row = document.createElement("div");
    row.className = "graph-detail-row";
    const key = document.createElement("span");
    key.textContent = label;
    const val = document.createElement("strong");
    val.textContent = text(value);
    row.append(key, val);
    container.appendChild(row);
  }

  function renderDetail(node) {
    clear(detail);
    if (!node) {
      detail.textContent = "请选择一个知识节点。";
      return;
    }

    const title = document.createElement("div");
    title.className = "graph-detail-title";
    const name = document.createElement("strong");
    name.textContent = nodeLabel(node);
    const type = document.createElement("span");
    type.textContent = kindLabel(node.kind);
    title.append(name, type);

    const grid = document.createElement("div");
    grid.className = "graph-detail-grid";
    addDetailLine(grid, "节点 ID", node.id);
    addDetailLine(grid, "分类", node.category);
    addDetailLine(grid, "权重", node.value);
    addDetailLine(grid, "时间", node.created_at);

    detail.append(title, grid);

    if (node.detail) {
      const body = document.createElement("p");
      body.className = "graph-detail-body";
      body.textContent = text(node.detail);
      detail.appendChild(body);
    }

    const related = neighbors(node.id);
    const relWrap = document.createElement("div");
    relWrap.className = "graph-related";
    const relTitle = document.createElement("span");
    relTitle.textContent = related.length ? "关联节点" : "暂无关联节点";
    relWrap.appendChild(relTitle);
    related.forEach((item) => {
      const chip = makeNodeButton(item.node, true);
      chip.title = `${item.direction === "to" ? "指向" : "来自"}：${nodeLabel(item.node)}，关联强度 ${edgeWeight(item.edge)}`;
      relWrap.appendChild(chip);
    });
    detail.appendChild(relWrap);
  }

  async function loadGraph() {
    root.classList.add("is-loading");
    root.textContent = "正在载入知识关联...";
    try {
      const response = await fetch(root.dataset.source || "/api/knowledge_graph", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "知识网络接口不可用。");
      nodes = Array.isArray(payload.nodes) ? payload.nodes.filter((node) => node && node.id) : [];
      edges = Array.isArray(payload.edges) ? payload.edges.filter((edge) => edge && edge.from && edge.to) : [];
      byId = new Map(nodes.map((node) => [node.id, node]));
      edges = edges.filter((edge) => byId.has(edge.from) && byId.has(edge.to));
      selectedId = byId.has(selectedId) ? selectedId : (byId.has("inventory") ? "inventory" : (nodes[0] && nodes[0].id) || "");
      root.classList.remove("is-loading");
      renderNetwork();
      if (selectedId) setSelected(selectedId);
      else renderDetail(null);
    } catch (error) {
      root.classList.remove("is-loading");
      root.innerHTML = "";
      const failed = document.createElement("div");
      failed.className = "knowledge-empty";
      failed.textContent = `知识关联载入失败：${error.message}`;
      root.appendChild(failed);
      detail.textContent = "知识关联暂时不可用。";
    }
  }

  window.addEventListener("knowledge:refresh", loadGraph);
  loadGraph();
})();
