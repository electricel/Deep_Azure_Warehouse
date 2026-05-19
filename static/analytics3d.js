(function () {
  const root = document.getElementById("inventory-3d");
  const tipsRoot = document.getElementById("inventory-tips");
  if (!root) return;

  function shortLabel(label) {
    return String(label || "未分类").split("/").map((item) => item.trim()).filter(Boolean).slice(-1)[0] || "未分类";
  }

  function numberText(value) {
    return (Number(value) || 0).toLocaleString("zh-CN");
  }

  function appendText(parent, tag, text, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = text || "";
    parent.appendChild(node);
    return node;
  }

  function createMetric(label, qty, entries, className) {
    const section = document.createElement("section");
    if (className) section.className = className;
    appendText(section, "small", label);
    appendText(section, "strong", numberText(qty));
    appendText(section, "em", `${numberText(entries)} 条记录`);
    return section;
  }

  function renderBoard(items, data) {
    const totals = data.totals || {};
    const top = items.slice(0, 4);
    const totalQty = totals.qty || top.reduce((sum, item) => sum + (Number(item.qty) || 0), 0);
    const entries = totals.entries || top.reduce((sum, item) => sum + (Number(item.entries) || 0), 0);
    root.textContent = "";
    root.classList.add("analytics-product-render");

    const shell = document.createElement("article");
    shell.className = "analytics-device product-device product-device-main";
    const toolbar = document.createElement("div");
    toolbar.className = "device-toolbar";
    toolbar.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
    const grid = document.createElement("div");
    grid.className = "analytics-live-grid";
    grid.appendChild(createMetric("Inventory Core", totalQty, entries, "analytics-live-main"));
    top.forEach((item) => grid.appendChild(createMetric(shortLabel(item.label), item.qty, item.entries || 1)));
    shell.append(toolbar, grid);

    const chips = document.createElement("div");
    chips.className = "analytics-live-chips";
    top.slice(0, 3).forEach((item, index) => {
      const chip = document.createElement("article");
      chip.className = `product-chip analytics-chip analytics-chip-${index + 1}`;
      appendText(chip, "span", shortLabel(item.label));
      appendText(chip, "strong", numberText(item.qty));
      chips.appendChild(chip);
    });

    root.append(shell, chips);
  }

  function renderTips(data) {
    if (!tipsRoot) return;
    const warnings = [
      ...(data.shortages || []).slice(0, 3).map((item) => `缺料：${item.name}，建议采购 ${item.qty}`),
      ...(data.low_stock || []).slice(0, 3).map((item) => `低库存：${item.name}，剩余 ${item.qty}`),
      ...(data.tips || []),
    ];
    tipsRoot.textContent = "";
    warnings.slice(0, 6).forEach((text) => {
      const pill = document.createElement("div");
      pill.className = "insight-pill";
      pill.textContent = text;
      tipsRoot.appendChild(pill);
    });
  }

  async function load() {
    try {
      const res = await fetch(root.dataset.source || "/api/inventory_insights");
      const data = await res.json();
      const items = data.categories || [];
      if (!items.length) {
        root.textContent = "";
        appendText(root, "div", "暂无库存数据", "empty");
        renderTips(data);
        return;
      }
      renderBoard(items, data);
      renderTips(data);
    } catch (err) {
      root.textContent = "";
      appendText(root, "div", `库存洞察读取失败：${err.message || "网络异常"}`, "empty");
    }
  }

  load();
})();
