(function () {
  const root = document.querySelector("[data-location-picker]");
  const stage = document.getElementById("location-2d-stage");
  const locationInput = document.getElementById("inventory-location") || document.querySelector('input[name="location"]');
  if (!root || !stage || !locationInput) return;

  const status = document.getElementById("location-picker-status");
  const breadcrumb = document.getElementById("location-breadcrumb");
  const currentCode = document.getElementById("location-current-code");
  const backButton = document.getElementById("location-back");
  const backNote = document.getElementById("location-back-note");
  const detailMain = document.getElementById("location-detail-value-main");
  const detailSub = document.getElementById("location-detail-value-sub");
  const detailCell = document.getElementById("location-detail-value-cell");
  const detailFinal = document.getElementById("location-detail-value-final");
  const detailLabelMain = document.getElementById("location-detail-label-main");
  const detailLabelSub = document.getElementById("location-detail-label-sub");
  const detailLabelCell = document.getElementById("location-detail-label-cell");
  const detailLabelFinal = document.getElementById("location-detail-label-final");
  const cabinetInput = document.getElementById("location-cabinet-id");
  const modeButtons = Array.from(root.querySelectorAll("[data-location-mode]"));
  const deviceConfig = document.getElementById("location-device-config");
  const paperConfig = document.getElementById("location-paper-config");
  const DEVICE_BOX_COUNT = 30;

  const parsedLocation = parseLocation(locationInput.value);
  const state = {
    mode: parsedLocation?.mode || "device",
    deviceStage: parsedLocation?.mode === "device" ? parsedLocation.stage : "boxes",
    paperStage: parsedLocation?.mode === "paper" ? parsedLocation.stage : "layers",
    selectedCode: parsedLocation?.code || "",
    deviceBox: parsedLocation?.mode === "device" ? parsedLocation.box : 0,
    deviceStrip: parsedLocation?.mode === "device" ? parsedLocation.strip : 0,
    deviceCell: parsedLocation?.mode === "device" ? parsedLocation.cell : 0,
    paperCabinet: parsedLocation?.mode === "paper" ? parsedLocation.cabinet : cleanCodePart(cabinetInput?.value, "A1"),
    paperLayer: parsedLocation?.mode === "paper" ? parsedLocation.layer : 0,
    paperSlot: parsedLocation?.mode === "paper" ? parsedLocation.slot : 0,
  };

  function parseLocation(value) {
    const text = String(value || "").trim().toUpperCase();
    if (!text) return null;

    let match = text.match(/^([A-Z0-9]+)-L([1-5])-(\d{1,2})$/);
    if (match) {
      const slot = clampInt(match[3], 1, 1, 20);
      return {
        mode: "paper",
        cabinet: match[1],
        layer: clampInt(match[2], 1, 1, 5),
        slot,
        stage: "boxes",
        code: `${match[1]}-L${match[2]}-${pad2(slot)}`,
      };
    }

    match = text.match(/^([A-Z]+)(\d{1,2})-(\d{1,2})-(\d{1,2})$/);
    if (match) {
      const box = clampInt(match[2], 1, 1, DEVICE_BOX_COUNT);
      const strip = clampInt(match[3], 1, 1, 14);
      const cell = clampInt(match[4], 1, 1, 4);
      return {
        mode: "device",
        box,
        strip,
        cell,
        stage: "cells",
        code: `${match[1]}${box}-${pad2(strip)}-${pad2(cell)}`,
      };
    }

    return null;
  }

  function cleanCodePart(value, fallback) {
    const cleaned = String(value || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    return cleaned || fallback;
  }

  function clampInt(value, fallback, min, max) {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(min, Math.min(max, parsed));
  }

  function pad2(value) {
    return String(Math.max(0, Number.parseInt(value, 10) || 0)).padStart(2, "0");
  }

  function setInputValue(code) {
    state.selectedCode = code;
    locationInput.value = code;
    locationInput.dispatchEvent(new Event("input", { bubbles: true }));
    locationInput.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function createTile({ title, subtitle, meta, className = "", selected = false, complete = false, action }) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = ["location-tile", className, selected ? "selected" : "", complete ? "complete" : ""].filter(Boolean).join(" ");
    button.innerHTML = `
      <strong>${title}</strong>
      ${subtitle ? `<span>${subtitle}</span>` : ""}
      ${meta ? `<em>${meta}</em>` : ""}
    `;
    button.addEventListener("click", action);
    return button;
  }

  function renderFrame({ title, subtitle, className = "", children }) {
    stage.innerHTML = "";
    const header = document.createElement("div");
    header.className = "location-level-head";
    header.innerHTML = `<strong>${title}</strong><span>${subtitle}</span>`;
    const grid = document.createElement("div");
    grid.className = ["location-level-grid", className].filter(Boolean).join(" ");
    children.forEach((child) => grid.appendChild(child));
    stage.append(header, grid);
  }

  function renderDeviceBoxes() {
    const tiles = Array.from({ length: DEVICE_BOX_COUNT }, (_, index) => {
      const box = index + 1;
      return createTile({
        title: `${box} 号器件盒`,
        subtitle: "进入盒内",
        meta: "14 条 × 4 格",
        selected: state.deviceBox === box,
        action: () => {
          state.deviceBox = box;
          state.deviceStrip = 0;
          state.deviceCell = 0;
          state.deviceStage = "box";
          render();
        },
      });
    });
    renderFrame({
      title: "选择器件盒",
      subtitle: `共 ${DEVICE_BOX_COUNT} 个器件盒，选择后进入该盒内部。`,
      className: "location-grid-boxes",
      children: tiles,
    });
  }

  function renderDeviceBoxHome() {
    const tiles = [
      createTile({
        title: `${state.deviceBox} 号器件盒`,
        subtitle: "单个器件盒",
        meta: "点击进入器件盒内部",
        className: "location-tile-large",
        selected: true,
        action: () => {
          state.deviceStage = "strips";
          render();
        },
      }),
    ];
    renderFrame({
      title: `${state.deviceBox} 号器件盒`,
      subtitle: "当前已进入单个器件盒界面，再进入盒内选择 14 个小条。",
      className: "location-grid-single",
      children: tiles,
    });
  }

  function renderDeviceStrips() {
    const tiles = Array.from({ length: 14 }, (_, index) => {
      const strip = index + 1;
      const side = strip <= 7 ? "左侧" : "右侧";
      return createTile({
        title: `${side} ${strip} 条`,
        subtitle: `器件盒 ${state.deviceBox}`,
        meta: "进入 1-4 号格",
        className: strip === 8 ? "location-tile-break" : "",
        selected: state.deviceStrip === strip,
        action: () => {
          state.deviceStrip = strip;
          state.deviceCell = 0;
          state.deviceStage = "cells";
          render();
        },
      });
    });
    renderFrame({
      title: `${state.deviceBox} 号器件盒内部`,
      subtitle: "14 个小条分为左侧 7 条、右侧 7 条。",
      className: "location-grid-strips",
      children: tiles,
    });
  }

  function renderDeviceCells() {
    const tiles = Array.from({ length: 4 }, (_, index) => {
      const cell = index + 1;
      const code = `H${state.deviceBox}-${pad2(state.deviceStrip)}-${pad2(cell)}`;
      return createTile({
        title: `${cell} 号格`,
        subtitle: `第 ${state.deviceStrip} 条`,
        meta: code,
        selected: state.deviceCell === cell,
        complete: state.selectedCode === code,
        action: () => {
          state.deviceCell = cell;
          setInputValue(code);
          render();
        },
      });
    });
    renderFrame({
      title: `${state.deviceBox} 号器件盒 / 第 ${state.deviceStrip} 条`,
      subtitle: "选择最终 1-4 号格，点击后写入位置。",
      className: "location-grid-cells",
      children: tiles,
    });
  }

  function renderPaperLayers() {
    const cabinet = cleanCodePart(cabinetInput?.value, "A1");
    state.paperCabinet = cabinet;
    const tiles = Array.from({ length: 5 }, (_, index) => {
      const layer = index + 1;
      return createTile({
        title: `L${layer} 层`,
        subtitle: `${cabinet} 柜`,
        meta: "20 个纸盒",
        selected: state.paperLayer === layer,
        action: () => {
          state.paperLayer = layer;
          state.paperSlot = 0;
          state.paperStage = "boxes";
          render();
        },
      });
    });
    renderFrame({
      title: "选择柜内层级",
      subtitle: "柜内固定 5 层，每层都有 20 个纸盒栏位。",
      className: "location-grid-layers",
      children: tiles,
    });
  }

  function renderPaperBoxes() {
    const cabinet = cleanCodePart(cabinetInput?.value, "A1");
    state.paperCabinet = cabinet;
    const tiles = Array.from({ length: 20 }, (_, index) => {
      const slot = index + 1;
      const code = `${cabinet}-L${state.paperLayer}-${pad2(slot)}`;
      return createTile({
        title: `${pad2(slot)} 号纸盒`,
        subtitle: `L${state.paperLayer} 层`,
        meta: code,
        selected: state.paperSlot === slot,
        complete: state.selectedCode === code,
        action: () => {
          state.paperSlot = slot;
          setInputValue(code);
          render();
        },
      });
    });
    renderFrame({
      title: `${cabinet} 柜 / L${state.paperLayer} 层`,
      subtitle: "选择该层 20 个纸盒栏位之一。",
      className: "location-grid-paper-boxes",
      children: tiles,
    });
  }

  function getBreadcrumb() {
    if (state.mode === "device") {
      if (state.deviceStage === "boxes") return `器件盒 / 选择 1-${DEVICE_BOX_COUNT} 号器件盒`;
      if (state.deviceStage === "box") return `器件盒 / ${state.deviceBox} 号盒`;
      if (state.deviceStage === "strips") return `器件盒 / ${state.deviceBox} 号盒 / 器件盒内部 / 选择 14 个小条`;
      return `器件盒 / ${state.deviceBox} 号盒 / 第 ${state.deviceStrip} 条 / 选择 1-4 号格`;
    }
    if (state.paperStage === "layers") return "柜内纸箱 / 选择 L1-L5 层";
    return `柜内纸箱 / ${state.paperCabinet} 柜 / L${state.paperLayer} 层 / 选择纸盒`;
  }

  function updateDetail() {
    const isDevice = state.mode === "device";
    deviceConfig.hidden = !isDevice;
    paperConfig.hidden = isDevice;
    modeButtons.forEach((button) => {
      button.classList.toggle("active", button.dataset.locationMode === state.mode);
      button.setAttribute("aria-selected", button.dataset.locationMode === state.mode ? "true" : "false");
    });

    const canBack = isDevice ? state.deviceStage !== "boxes" : state.paperStage !== "layers";
    backButton.disabled = !canBack;
    backNote.textContent = canBack ? "可返回上一级" : "当前在顶层视图";
    breadcrumb.textContent = getBreadcrumb();

    if (isDevice) {
      const hasBox = state.deviceBox > 0;
      const hasStrip = state.deviceStrip > 0;
      const hasCell = state.deviceCell > 0;
      detailLabelMain.textContent = "器件盒";
      detailLabelSub.textContent = "小条";
      detailLabelCell.textContent = "格子";
      detailLabelFinal.textContent = "最终编码";
      detailMain.textContent = hasBox ? `${state.deviceBox} 号器件盒` : "待选择";
      detailSub.textContent = hasStrip ? `第 ${state.deviceStrip} 条` : "待选择";
      detailCell.textContent = hasCell ? `${state.deviceCell} 号格` : "待选择";
    } else {
      const hasLayer = state.paperLayer > 0;
      const hasSlot = state.paperSlot > 0;
      detailLabelMain.textContent = "柜号";
      detailLabelSub.textContent = "层级";
      detailLabelCell.textContent = "纸盒";
      detailLabelFinal.textContent = "最终编码";
      detailMain.textContent = state.paperCabinet || "A1";
      detailSub.textContent = hasLayer ? `L${state.paperLayer} 层` : "待选择";
      detailCell.textContent = hasSlot ? `${pad2(state.paperSlot)} 号纸盒` : "待选择";
    }

    currentCode.textContent = state.selectedCode || "未选择";
    detailFinal.textContent = state.selectedCode || "待确认";
    status.textContent = isDevice
      ? "器件盒按 盒 → 小条 → 格子 逐层选择"
      : "柜内纸箱按 层 → 纸盒 逐层选择";
  }

  function render() {
    updateDetail();
    if (state.mode === "device") {
      if (state.deviceStage === "strips") return renderDeviceStrips();
      if (state.deviceStage === "cells") return renderDeviceCells();
      if (state.deviceStage === "box") return renderDeviceBoxHome();
      return renderDeviceBoxes();
    }
    if (state.paperStage === "boxes") return renderPaperBoxes();
    return renderPaperLayers();
  }

  function switchMode(mode) {
    state.mode = mode;
    if (mode === "device" && !["boxes", "box", "strips", "cells"].includes(state.deviceStage)) state.deviceStage = "boxes";
    if (mode === "paper" && !["layers", "boxes"].includes(state.paperStage)) state.paperStage = "layers";
    render();
  }

  function goBack() {
    if (state.mode === "device") {
      if (state.deviceStage === "cells") {
        state.deviceStage = "strips";
        state.deviceCell = 0;
      } else if (state.deviceStage === "strips") {
        state.deviceStage = "box";
        state.deviceStrip = 0;
      } else if (state.deviceStage === "box") {
        state.deviceStage = "boxes";
        state.deviceBox = 0;
      }
    } else if (state.paperStage === "boxes") {
      state.paperStage = "layers";
      state.paperSlot = 0;
    }
    render();
  }

  modeButtons.forEach((button) => {
    button.addEventListener("click", () => switchMode(button.dataset.locationMode));
  });
  backButton.addEventListener("click", goBack);
  cabinetInput?.addEventListener("input", () => {
    state.paperCabinet = cleanCodePart(cabinetInput.value, "A1");
    if (state.mode === "paper") render();
  });

  render();
})();
