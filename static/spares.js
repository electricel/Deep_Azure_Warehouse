(function () {
  const form = document.querySelector("[data-spare-material-form]");
  if (!form) return;

  const panel = document.querySelector("[data-spares-image-recognition]");
  const photoInput = document.getElementById("spare-photo-input");
  const recognizeButton = document.getElementById("spare-recognize-photo");
  const statusNode = document.getElementById("spare-image-status");
  const resultsNode = document.getElementById("spare-image-results");
  const materialType = document.getElementById("spare-material-type");
  const category = document.getElementById("spare-category");
  const hardwareSection = document.querySelector("[data-spare-hardware-section]");
  const hardwareCompare = document.getElementById("spare-hardware-compare");

  const field = (name) => form.elements[name] || document.getElementById(`spare-${name}`);

  function setStatus(message, mode) {
    if (!statusNode) return;
    statusNode.textContent = message || "";
    statusNode.dataset.mode = mode || "idle";
  }

  function appendText(parent, tag, text) {
    const node = document.createElement(tag);
    node.textContent = text || "";
    parent.appendChild(node);
    return node;
  }

  function isHardwareText(text) {
    return /(硬件|电控|模块|pcb|传感器|线材|接插件|连接器|电机|舵机|降压|芯片|端子)/i.test(String(text || ""));
  }

  function isMechanicalText(text) {
    return /(机械|铝管|型材|碳板|板材|气泵|电磁阀|气动|螺栓|螺母|螺丝|紧固|结构)/i.test(String(text || ""));
  }

  function inferTypeFromFields() {
    const text = `${category ? category.value : ""} ${field("name") ? field("name").value : ""} ${field("spec") ? field("spec").value : ""}`;
    if (isMechanicalText(text)) return "mechanical";
    if (isHardwareText(text)) return "hardware";
    return materialType ? materialType.value : "";
  }

  function syncHardwareSection() {
    const type = materialType ? materialType.value || inferTypeFromFields() : inferTypeFromFields();
    const showHardware = type === "hardware" || isHardwareText(category && category.value);
    if (hardwareSection) {
      hardwareSection.dataset.visible = showHardware ? "1" : "0";
    }
    if (hardwareCompare) {
      hardwareCompare.checked = showHardware;
    }
  }

  function setSelectValue(select, value) {
    if (!select || !value) return false;
    const target = String(value);
    const exact = Array.from(select.options).find((option) => option.value === target || option.textContent === target);
    if (exact) {
      select.value = exact.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
    return false;
  }

  function applyRecognizedItem(item) {
    const data = item || {};
    if (data.category && category) setSelectValue(category, data.category) || (category.value = data.category);
    if (data.material_type && materialType) setSelectValue(materialType, data.material_type);
    const mapping = {
      name: data.name || "",
      spec: data.spec || "",
      quantity: data.quantity || 1,
      unit: data.unit || "个",
      storage_location: data.storage_location || "",
      note: data.note || "",
    };
    Object.entries(mapping).forEach(([name, value]) => {
      const node = field(name);
      if (!node || value === "" || value == null) return;
      node.value = value;
    });
    if (hardwareCompare) hardwareCompare.checked = !!data.hardware_compare;
    syncHardwareSection();
    setStatus("识别结果已预填，请核对位置、保管人和数量后保存。", "ok");
  }

  function renderRecognizedItems(items) {
    if (!resultsNode) return;
    resultsNode.innerHTML = "";
    (items || []).forEach((item, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "image-recognition-result spare-recognition-result";
      const title = item.name || `识别结果 ${index + 1}`;
      const meta = [
        item.material_type_label || item.material_type,
        item.category,
        item.spec,
        item.quantity ? `数量 ${item.quantity}` : "",
        item.storage_location ? `位置 ${item.storage_location}` : "",
      ].filter(Boolean).join(" / ");
      appendText(button, "strong", title);
      appendText(button, "span", meta || "点击写入表单");
      button.addEventListener("click", () => applyRecognizedItem(item));
      resultsNode.appendChild(button);
    });
  }

  async function recognizeCurrentPhoto() {
    const file = photoInput && photoInput.files && photoInput.files[0];
    if (!file) {
      setStatus("请先选择或拍摄一张真实照片。", "error");
      return;
    }
    if (!/^image\//.test(file.type || "")) {
      setStatus("请选择 JPG、PNG、WEBP 或 GIF 图片。", "error");
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      setStatus("图片超过 8MB，请压缩后再上传。", "error");
      return;
    }
    if (panel && panel.dataset.enabled !== "1") {
      setStatus("后台未启用图片识别 API；照片仍会在保存时留档。", "error");
      return;
    }
    const formData = new FormData();
    formData.append("competition_photo", file);
    setStatus("正在上传照片并调用 AI 识别...", "loading");
    if (recognizeButton) recognizeButton.disabled = true;
    try {
      const response = await fetch("/api/spares/image-recognize", {
        method: "POST",
        credentials: "same-origin",
        body: formData,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "图片识别失败。");
      const items = Array.isArray(data.items) ? data.items : [];
      if (!items.length) throw new Error("未识别到比赛备件。");
      renderRecognizedItems(items);
      applyRecognizedItem(items[0]);
      setStatus(`已识别 ${items.length} 条候选结果，已预填第一条。`, "ok");
    } catch (error) {
      setStatus(error.message || "图片识别失败。", "error");
    } finally {
      if (recognizeButton) recognizeButton.disabled = false;
    }
  }

  if (photoInput) {
    photoInput.addEventListener("change", () => {
      const file = photoInput.files && photoInput.files[0];
      if (!file) return;
      setStatus(`已选择照片：${file.name}。可点击识别，或直接保存留档。`, "idle");
    });
  }
  if (recognizeButton) recognizeButton.addEventListener("click", recognizeCurrentPhoto);
  [materialType, category, field("name"), field("spec")].forEach((node) => {
    if (node) node.addEventListener("change", syncHardwareSection);
  });
  syncHardwareSection();
})();
