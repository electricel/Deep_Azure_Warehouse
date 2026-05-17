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

  function renderBoard(items, data) {
    const totals = data.totals || {};
    const top = items.slice(0, 4);
    const totalQty = totals.qty || top.reduce((sum, item) => sum + (Number(item.qty) || 0), 0);
    const entries = totals.entries || top.reduce((sum, item) => sum + (Number(item.entries) || 0), 0);
    root.innerHTML = "";
    root.classList.add("analytics-product-render");

    const shell = document.createElement("article");
    shell.className = "analytics-device product-device product-device-main";
    shell.innerHTML = `
      <div class="device-toolbar"><span></span><span></span><span></span></div>
      <div class="analytics-live-grid">
        <section class="analytics-live-main"><small>Inventory Core</small><strong>${numberText(totalQty)}</strong><em>${numberText(entries)} 条库存记录</em></section>
        ${top.map((item) => `<section><small>${shortLabel(item.label)}</small><strong>${numberText(item.qty)}</strong><em>${numberText(item.entries || 1)} 条记录</em></section>`).join("")}
      </div>
    `;

    const chips = document.createElement("div");
    chips.className = "analytics-live-chips";
    top.slice(0, 3).forEach((item, index) => {
      const chip = document.createElement("article");
      chip.className = `product-chip analytics-chip analytics-chip-${index + 1}`;
      chip.innerHTML = `<span>${shortLabel(item.label)}</span><strong>${numberText(item.qty)}</strong>`;
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
    tipsRoot.innerHTML = "";
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
        root.innerHTML = '<div class="empty">暂无库存数据</div>';
        renderTips(data);
        return;
      }
      renderBoard(items, data);
      renderTips(data);
    } catch (err) {
      root.innerHTML = `<div class="empty">库存洞察读取失败：${err.message}</div>`;
    }
  }

  load();
})();
