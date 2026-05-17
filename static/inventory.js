(function () {
  const root = document.querySelector(".product-entry");
  if (!root) return;

  const $ = (id) => document.getElementById(id);
  const primary = $("primary-category");
  const secondary = $("secondary-category");
  const category = $("category");
  const codeInput = $("lcsc-code");
  const lookup = $("lcsc-lookup");
  const preview = $("lcsc-preview");
  const status = $("prefill-status");
  const nameInput = $("product-name");
  const valueInput = $("value-spec");
  const packageInput = $("package");
  const voltageInput = $("voltage");
  const brandInput = $("brand");
  const urlInput = $("product-url");
  const quantityInput = $("quantity");
  const stepPanel = $("step-preview-panel");
  const stepStage = $("step-model-stage");
  const stepMeta = $("step-model-meta");
  const stepStatus = $("step-preview-status");
  const imagePanel = document.querySelector("[data-image-recognition]");
  const cameraInput = $("inventory-camera-input");
  const galleryInput = $("inventory-gallery-input");
  const imageStatus = $("image-recognition-status");
  const imageResults = $("image-recognition-results");

  let lastHydratedCode = "";
  let categories = [];
  try {
    categories = JSON.parse(root.dataset.categories || "[]");
  } catch (_) {
    categories = [];
  }

  function option(label, value) {
    const node = document.createElement("option");
    node.textContent = label;
    node.value = value == null ? label : value;
    return node;
  }

  function leafOptions(node, prefix) {
    const current = prefix ? `${prefix} / ${node.name}` : node.name;
    if (!node.children || node.children.length === 0) {
      return [{ label: node.name, value: current }];
    }
    return node.children.flatMap((child) => leafOptions(child, current));
  }

  function findCategoryPath(path) {
    const target = String(path || "").trim();
    if (!target) return null;
    let found = null;
    function walk(node, prefix, rootName) {
      if (found || !node) return;
      const current = prefix ? `${prefix} / ${node.name}` : node.name;
      if (target === current || target === node.name || target.endsWith(` / ${node.name}`)) {
        found = { rootName, value: current };
        return;
      }
      (node.children || []).forEach((child) => walk(child, current, rootName));
    }
    categories.forEach((item) => walk(item, "", item.name));
    return found;
  }

  function syncSecondary() {
    if (!primary || !secondary) return;
    const selected = categories.find((item) => item.name === primary.value);
    secondary.innerHTML = "";
    secondary.appendChild(option("自动匹配二级分类", ""));
    if (selected) {
      leafOptions(selected, "").forEach((item) => secondary.appendChild(option(item.label, item.value)));
    }
    syncCategory();
    refreshGlassSelect(secondary);
  }

  function syncCategory() {
    if (!category || !primary || !secondary) return;
    category.value = secondary.value || primary.value || category.value;
  }

  function setCategoryPath(path) {
    if (!path || !primary || !secondary || !category) return;
    category.value = path;
    const leafMatch = findCategoryPath(path);
    if (leafMatch && leafMatch.value) {
      path = leafMatch.value;
      category.value = path;
    }
    const rootMatch = categories.find((item) => path === item.name || path.startsWith(`${item.name} /`));
    if (!rootMatch) return;
    primary.value = rootMatch.name;
    syncSecondary();
    const exact = Array.from(secondary.options).find((item) => item.value === path);
    if (exact) {
      secondary.value = exact.value;
    } else {
      const fuzzy = Array.from(secondary.options).find((item) => path.endsWith(item.textContent));
      if (fuzzy) secondary.value = fuzzy.value;
    }
    category.value = secondary.value || path;
    refreshGlassSelect(primary);
    refreshGlassSelect(secondary);
  }

  function initCategories() {
    if (!primary || !categories.length) return;
    primary.innerHTML = "";
    primary.appendChild(option("选择一级分类", ""));
    categories.forEach((item) => primary.appendChild(option(item.name)));
    primary.addEventListener("change", syncSecondary);
    secondary && secondary.addEventListener("change", syncCategory);
  }

  function closeAllGlassSelect(except) {
    document.querySelectorAll(".glass-select.open").forEach((node) => {
      if (node !== except) node.classList.remove("open");
    });
  }

  function refreshGlassSelect(select) {
    if (!select || !select._glass) return;
    const { root, value, list } = select._glass;
    const selected = select.options[select.selectedIndex];
    value.textContent = selected ? selected.textContent : "";
    list.innerHTML = "";
    Array.from(select.options).forEach((opt) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "glass-option";
      item.textContent = opt.textContent;
      item.dataset.value = opt.value;
      if (opt.selected) item.classList.add("selected");
      item.addEventListener("click", () => {
        select.value = opt.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        root.classList.remove("open");
        refreshGlassSelect(select);
      });
      list.appendChild(item);
    });
  }

  function enhanceGlassSelect(select) {
    if (!select || select._glass) return;
    const rootNode = document.createElement("div");
    rootNode.className = "glass-select";
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "glass-select-trigger";
    const value = document.createElement("span");
    const arrow = document.createElement("i");
    arrow.textContent = "⌄";
    trigger.append(value, arrow);
    const list = document.createElement("div");
    list.className = "glass-select-list";
    rootNode.append(trigger, list);
    select.classList.add("native-select-hidden");
    select.insertAdjacentElement("afterend", rootNode);
    select._glass = { root: rootNode, value, list };
    trigger.addEventListener("click", () => {
      const open = rootNode.classList.contains("open");
      closeAllGlassSelect(rootNode);
      rootNode.classList.toggle("open", !open);
    });
    select.addEventListener("change", () => refreshGlassSelect(select));
    refreshGlassSelect(select);
  }

  function inferFromLocalInputs() {
    const text = `${nameInput.value} ${valueInput.value} ${packageInput.value}`.toLowerCase();
    if (!category.value) {
      if (/(ω|Ω|ohm|kohm|mohm|\d+[kKmMrR])/.test(text)) category.value = "电容 / 电阻 / 电感 / 电阻";
      if (/(pf|nf|uf|μf|碌f|渭f)/i.test(text)) category.value = "电容 / 电阻 / 电感 / 电容";
      if (/(uh|μh|碌h|渭h|mh)/i.test(text)) category.value = "电容 / 电阻 / 电感 / 电感";
    }
    if (!nameInput.value && valueInput.value) {
      nameInput.value = [valueInput.value, packageInput.value, voltageInput.value].filter(Boolean).join(" / ");
    }
  }

  function pickProductName(product) {
    return product.product_name || product.product_model || product.lcsc_code || "";
  }

  function setStatus(text, mode) {
    if (!status) return;
    status.textContent = text;
    status.dataset.mode = mode || "idle";
  }

  function setStepStatus(text, mode) {
    if (stepStatus) stepStatus.textContent = text;
    if (stepPanel) stepPanel.dataset.state = mode || "idle";
  }

  function setImageStatus(text, mode) {
    if (!imageStatus) return;
    imageStatus.textContent = text;
    imageStatus.dataset.mode = mode || "idle";
  }

  function renderStepModel(model, product, specs) {
    if (!stepStage || !stepMeta) return;
    const payload = model || {};
    const kind = payload.kind || "generic";
    const label = payload.label || product.product_model || product.product_name || product.lcsc_code || "LCSC Model";
    const packageName = payload.package || specs.package || product.package || "Package";
    stepStage.innerHTML = `
      <div class="step-render model-${kind}">
        <span class="model-shadow"></span>
        <span class="model-body"></span>
        <span class="model-pin pin-a"></span>
        <span class="model-pin pin-b"></span>
        <span class="model-pin pin-c"></span>
        <span class="model-mark"></span>
      </div>
    `;
    const structuredLinks = Array.isArray(payload.model_urls) ? payload.model_urls : [];
    const links = structuredLinks.length ? structuredLinks.map((item) => item.url).filter(Boolean) : (Array.isArray(payload.links) ? payload.links : []);
    stepMeta.innerHTML = "";
    const title = document.createElement("strong");
    const modelTitle = payload.model_title ? ` / ${payload.model_title}` : "";
    title.textContent = `${label} / ${packageName}${modelTitle}`;
    const desc = document.createElement("span");
    desc.textContent = links.length
      ? (payload.source === "easyeda" ? "已通过 EasyEDA/LCSC 资源接口关联真实 STEP 链接，同时渲染封装预览。" : "检测到 LCSC 模型链接，同时渲染本地实时封装预览。")
      : "未检测到公开 STEP 链接，已按 LCSC 封装参数实时生成 3D 预览。";
    stepMeta.append(title, desc);
    const linkRows = structuredLinks.length ? structuredLinks : links.map((link) => ({ url: link, kind: /\.step|\.stp/i.test(link) ? "step" : "model" }));
    linkRows.slice(0, 3).forEach((item) => {
      const a = document.createElement("a");
      a.href = item.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = item.kind === "step" ? "打开 STEP 模型" : (item.kind === "obj" ? "打开 OBJ 模型" : "打开 3D 模型");
      stepMeta.appendChild(a);
    });
    if (payload.viewer_url) {
      const a = document.createElement("a");
      a.href = payload.viewer_url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = "打开 EasyEDA Viewer";
      stepMeta.appendChild(a);
    }
    setStepStatus(links.length ? (payload.source === "easyeda" ? "EasyEDA STEP 已关联" : "STEP 已关联") : "参数模型已渲染", "ok");
  }

  function rewritePrefill(code, product, specs, suggestedCategory) {
    const productName = pickProductName(product);
    const value = specs.value_spec || "";
    const packageName = specs.package || "";
    const rating = specs.voltage || specs.power || "";
    const nextCategory = suggestedCategory || specs.category || product.category || "";
    const isNewCode = lastHydratedCode !== code;

    nameInput.value = productName;
    valueInput.value = value;
    packageInput.value = packageName;
    voltageInput.value = rating;
    brandInput.value = specs.brand || product.brand || "";
    urlInput.value = specs.url || product.product_url || "";
    setCategoryPath(nextCategory);
    if (isNewCode && quantityInput && (!quantityInput.value || Number(quantityInput.value) < 1)) {
      quantityInput.value = "1";
    }
    lastHydratedCode = code;
    setStatus(`已刷新 ${code} 的预写入信息`, "ok");
  }

  function applyRecognizedItem(item) {
    const data = item || {};
    if (codeInput) codeInput.value = (data.lcsc_code || "").toUpperCase();
    if (nameInput) nameInput.value = data.name || "";
    if (valueInput) valueInput.value = data.value_spec || "";
    if (packageInput) packageInput.value = data.package || "";
    if (voltageInput) voltageInput.value = data.voltage || "";
    if (brandInput) brandInput.value = data.brand || "";
    if (urlInput) urlInput.value = data.product_url || "";
    if (quantityInput) quantityInput.value = Math.max(1, Number.parseInt(data.quantity || "1", 10) || 1);
    const locationInput = document.querySelector('input[name="location"]');
    if (locationInput && data.location) locationInput.value = data.location;
    const noteInput = document.querySelector('textarea[name="note"]');
    if (noteInput && data.note) noteInput.value = data.note;
    setCategoryPath(data.category || "");
    if (category && data.category && !category.value) category.value = data.category;
    setStatus("图片识别结果已预写入，请检查后保存", "ok");
  }

  function renderRecognizedItems(items) {
    if (!imageResults) return;
    imageResults.innerHTML = "";
    (items || []).forEach((item, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "image-recognition-result";
      const title = item.name || item.lcsc_code || `识别结果 ${index + 1}`;
      const meta = [item.lcsc_code, item.value_spec, item.package, item.brand, item.quantity ? `数量 ${item.quantity}` : ""].filter(Boolean).join(" / ");
      button.innerHTML = `<strong>${title}</strong><span>${meta || "点击写入表单"}</span>`;
      button.addEventListener("click", () => applyRecognizedItem(item));
      imageResults.appendChild(button);
    });
  }

  async function uploadInventoryImage(file, input) {
    if (!file) return;
    if (imagePanel && imagePanel.dataset.enabled !== "1") {
      setImageStatus("后台未启用图片识别 API，请先在后台配置。", "error");
      if (input) input.value = "";
      return;
    }
    if (!/^image\//.test(file.type || "")) {
      setImageStatus("请选择 JPG、PNG、WEBP 或 GIF 图片。", "error");
      if (input) input.value = "";
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      setImageStatus("图片超过 8MB，请压缩后再上传。", "error");
      if (input) input.value = "";
      return;
    }
    const formData = new FormData();
    formData.append("inventory_image", file);
    setImageStatus("正在上传图片并调用 AI 识别...", "loading");
    setStatus("图片识别中", "loading");
    try {
      const response = await fetch("/api/inventory/image-recognize", {
        method: "POST",
        credentials: "same-origin",
        body: formData,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "图片识别失败。");
      const items = Array.isArray(data.items) ? data.items : [];
      if (!items.length) throw new Error("未识别到可入库器件。");
      renderRecognizedItems(items);
      applyRecognizedItem(items[0]);
      setImageStatus(`已识别 ${items.length} 条候选结果，已预写入第一条。`, "ok");
    } catch (error) {
      setImageStatus(error.message || "图片识别失败。", "error");
      setStatus("图片识别失败", "error");
    } finally {
      if (input) input.value = "";
    }
  }

  async function lookupLcsc() {
    const code = (codeInput.value || "").trim().toUpperCase();
    if (!/^C\d{3,}$/.test(code)) {
      preview.textContent = "请输入正确的 LCSC C 编号，例如 C25803。";
      preview.classList.add("error");
      setStatus("等待正确的 C 编号", "error");
      return;
    }
    codeInput.value = code;
    lookup.disabled = true;
    preview.classList.remove("error");
    preview.textContent = "正在连接立创商城并刷新商品预写入...";
    setStatus("正在刷新预写入", "loading");
    setStepStatus("正在请求 LCSC 模型", "loading");
    try {
      const res = await fetch(`/api/lcsc_lookup?code=${encodeURIComponent(code)}&force=1`);
      const data = await res.json();
      if (!res.ok || !data.product || data.product.status !== "ok") {
        preview.textContent = data.error || `未能识别 ${code}，可以先手动录入。`;
        preview.classList.add("error");
        setStatus("未匹配到 LCSC 商品", "error");
        return;
      }
      const product = data.product;
      const specs = data.specs || {};
      rewritePrefill(code, product, specs, data.suggested_category);
      renderStepModel(data.model_3d || {}, product, specs);
      preview.innerHTML = [
        `<strong>${product.product_model || product.product_name || code}</strong>`,
        product.product_name ? `<span>${product.product_name}</span>` : "",
        specs.value_spec ? `<span>${specs.value_spec}</span>` : "",
        specs.package ? `<span>封装 ${specs.package}</span>` : "",
        (specs.voltage || specs.power) ? `<span>${specs.voltage || specs.power}</span>` : "",
        product.stock ? `<span>立创库存 ${product.stock}</span>` : "",
        (specs.url || product.product_url) ? `<a href="${specs.url || product.product_url}" target="_blank" rel="noopener">打开 LCSC 商品页</a>` : "",
      ].filter(Boolean).join("");
    } catch (err) {
      preview.textContent = `联网查询失败：${err.message}`;
      preview.classList.add("error");
      setStatus("联网查询失败", "error");
      setStepStatus("模型读取失败", "error");
    } finally {
      lookup.disabled = false;
    }
  }

  initCategories();
  syncSecondary();
  [primary, secondary].forEach(enhanceGlassSelect);
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".glass-select")) closeAllGlassSelect();
  });
  codeInput && codeInput.addEventListener("input", () => setStatus("C 编号已修改，点击补全刷新预写入", "dirty"));
  lookup && lookup.addEventListener("click", lookupLcsc);
  [cameraInput, galleryInput].forEach((input) => {
    input && input.addEventListener("change", () => uploadInventoryImage(input.files && input.files[0], input));
  });
  [nameInput, valueInput, packageInput, voltageInput].forEach((node) => {
    node && node.addEventListener("change", inferFromLocalInputs);
  });
})();
(function () {
  const notice = document.querySelector("[data-inventory-release-notice]");
  if (!notice) return;
  const version = notice.dataset.version || "empty";
  const versionStorageKey = "warehouse.inventory.releaseNoticeSeen";
  const todayStorageKey = "warehouse.inventory.releaseNoticeToday";
  const today = new Date().toISOString().slice(0, 10);
  let seenVersion = "";
  let mutedToday = "";
  try {
    seenVersion = localStorage.getItem(versionStorageKey) || "";
    mutedToday = localStorage.getItem(todayStorageKey) || "";
  } catch (_) {
    seenVersion = "";
    mutedToday = "";
  }
  if (seenVersion === version || mutedToday === `${version}:${today}`) {
    notice.remove();
    return;
  }
  notice.classList.add("is-visible");
  document.body.classList.add("has-inventory-release-notice");

  function closeNotice(mode) {
    try {
      if (mode === "today") {
        localStorage.setItem(todayStorageKey, `${version}:${today}`);
      } else {
        localStorage.setItem(versionStorageKey, version);
      }
    } catch (_) {
      // Ignore private-mode storage failures; the close action should still work.
    }
    notice.classList.remove("is-visible");
    document.body.classList.remove("has-inventory-release-notice");
    window.setTimeout(() => notice.remove(), 180);
  }

  notice.querySelectorAll("[data-inventory-notice-close]").forEach((button) => {
    button.addEventListener("click", () => closeNotice(button.dataset.closeMode || "version"));
  });
  notice.addEventListener("click", (event) => {
    if (event.target === notice) closeNotice("today");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && notice.isConnected) closeNotice("today");
  });
})();

(function () {
  const forms = document.querySelectorAll("[data-inventory-adjust-form]");
  if (!forms.length) return;

  function setStatus(form, message, mode) {
    const status = form.querySelector(".inventory-adjust-status");
    if (!status) return;
    status.textContent = message || "";
    status.classList.toggle("is-error", mode === "error");
    status.classList.toggle("is-ok", mode === "ok");
  }

  function cleanError(message, statusCode) {
    if (statusCode === 401) return "Please sign in again.";
    if (statusCode === 403) return "You do not have permission to adjust this row.";
    const text = typeof message === "string" ? message.trim() : "";
    if (!text) return "Adjustment failed.";
    return text.length > 180 ? `${text.slice(0, 180)}...` : text;
  }

  function parseWholeNumber(value) {
    const raw = String(value || "").trim();
    if (!/^-?\d+$/.test(raw)) return null;
    const parsed = Number.parseInt(raw, 10);
    return Number.isSafeInteger(parsed) ? parsed : null;
  }

  forms.forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const mode = form.elements.mode ? form.elements.mode.value : "quantity_delta";
      const value = parseWholeNumber(form.elements.adjust_value ? form.elements.adjust_value.value : "");
      const reason = form.elements.reason ? form.elements.reason.value.trim() : "";
      if (value === null) {
        setStatus(form, "Enter a whole-number quantity.", "error");
        return;
      }
      if (!reason) {
        setStatus(form, "Reason is required.", "error");
        return;
      }

      const payload = { reason };
      if (mode === "quantity_after") {
        payload.quantity_after = value;
      } else {
        payload.quantity_delta = value;
      }

      const button = form.querySelector('button[type="submit"]');
      if (button) button.disabled = true;
      setStatus(form, "Applying adjustment...", "");
      try {
        const response = await fetch(form.action, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw { message: data.error, status: response.status };
        }
        const nextQuantity = Number.isInteger(data.quantity_after)
          ? data.quantity_after
          : data.inventory_entry && Number.isInteger(data.inventory_entry.quantity)
            ? data.inventory_entry.quantity
            : null;
        if (nextQuantity !== null) {
          const row = form.closest("tr");
          const quantityNode = row && row.querySelector("[data-inventory-quantity]");
          if (quantityNode) quantityNode.textContent = String(nextQuantity);
        }
        if (form.elements.adjust_value) form.elements.adjust_value.value = "";
        if (form.elements.reason) form.elements.reason.value = "";
        setStatus(
          form,
          nextQuantity === null
            ? "Adjustment saved. Refresh to see the audit history."
            : `Quantity updated to ${nextQuantity}. Refresh to see the audit history.`,
          "ok"
        );
      } catch (error) {
        setStatus(form, cleanError(error && error.message, error && error.status), "error");
      } finally {
        if (button) button.disabled = false;
      }
    });
  });

})();

(function () {
  const smartSearchForm = document.querySelector("[data-smart-inventory-search]");
  if (!smartSearchForm) return;
  const smartInput = smartSearchForm.querySelector('input[name="q"]');
  const smartField = smartSearchForm.querySelector(".smart-search-field");
  const chips = document.querySelectorAll(".smart-search-chip");
  if (smartInput && smartField) {
    const updateState = () => {
      smartField.dataset.hasValue = smartInput.value.trim() ? "1" : "0";
    };
    smartInput.addEventListener("input", updateState);
    smartInput.addEventListener("focus", updateState);
    smartInput.addEventListener("blur", updateState);
    updateState();
  }
  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      if (!smartInput) return;
      const value = chip.dataset.value || chip.textContent.replace(/\s+/g, " ").trim();
      smartInput.value = value;
      smartInput.dispatchEvent(new Event("input", { bubbles: true }));
      smartSearchForm.requestSubmit ? smartSearchForm.requestSubmit() : smartSearchForm.submit();
    });
  });
})();
