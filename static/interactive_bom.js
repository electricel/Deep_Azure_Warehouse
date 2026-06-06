(function () {
  "use strict";

  const VIEWER_SELECTOR = ".interactive-bom-viewer";
  const PAYLOAD_SELECTOR = 'script[type="application/json"][data-interactive-bom-payload]';

  function parseJson(text) {
    if (!text || !String(text).trim()) return null;
    try {
      return JSON.parse(text);
    } catch (_err) {
      return null;
    }
  }

  function findPayload(viewer) {
    const localScript = viewer.querySelector(PAYLOAD_SELECTOR);
    const script =
      localScript ||
      (viewer.id
        ? document.querySelector(`${PAYLOAD_SELECTOR}[data-target="${CSS.escape(viewer.id)}"]`)
        : null) ||
      document.querySelector(PAYLOAD_SELECTOR);
    const fromScript = script ? parseJson(script.textContent) : null;
    if (fromScript) return fromScript;

    return (
      parseJson(viewer.dataset.interactiveBomPayload) ||
      parseJson(viewer.dataset.payload) ||
      parseJson(viewer.getAttribute("data-components"))
    );
  }

  function asArray(value) {
    if (Array.isArray(value)) return value;
    if (value == null || value === "") return [];
    return [value];
  }

  function textValue(value) {
    if (value == null) return "";
    return String(value).trim();
  }

  function refsFor(component) {
    const refs = asArray(component.refs && component.refs.length ? component.refs : component.ref)
      .flatMap((item) => String(item).split(/[,\s]+/))
      .map((item) => item.trim())
      .filter(Boolean);
    return Array.from(new Set(refs));
  }

  function bboxFromFootprint(footprint) {
    if (!footprint) return null;
    const bbox = footprint.bbox || {};
    const pos = Array.isArray(bbox.pos) ? bbox.pos : footprint.center;
    const relpos = Array.isArray(bbox.relpos) ? bbox.relpos : [-1.5, -1.2];
    const size = Array.isArray(bbox.size) ? bbox.size : [3, 2.4];
    if (!Array.isArray(pos) || pos.length < 2 || size.length < 2) return null;
    const x = Number(pos[0]) + Number(relpos[0] || 0);
    const y = Number(pos[1]) + Number(relpos[1] || 0);
    const w = Number(size[0]);
    const h = Number(size[1]);
    if ([x, y, w, h].some((item) => !Number.isFinite(item))) return null;
    return { x, y, w, h };
  }

  function componentsFromPcbdata(pcbdata) {
    if (!pcbdata || !Array.isArray(pcbdata.footprints)) return [];
    const footprints = pcbdata.footprints;
    const bomRows = pcbdata.bom && Array.isArray(pcbdata.bom.both) ? pcbdata.bom.both : [];
    const fields = pcbdata.bom && pcbdata.bom.fields && typeof pcbdata.bom.fields === "object" ? pcbdata.bom.fields : {};

    if (bomRows.length) {
      return bomRows.map((row, index) => {
        const pairs = Array.isArray(row) ? row : [];
        const ids = pairs
          .map((pair) => Array.isArray(pair) ? Number(pair[1]) : NaN)
          .filter((id) => Number.isInteger(id) && id >= 0);
        const refs = pairs
          .map((pair) => Array.isArray(pair) ? textValue(pair[0]) : textValue(pair))
          .filter(Boolean);
        const firstFootprint = footprints[ids[0]] || footprints.find((footprint) => refs.includes(textValue(footprint.ref))) || {};
        const firstFields = asArray(fields[String(ids[0])]).map(textValue);
        return {
          id: `pcbdata-${index}`,
          refs,
          ref: refs[0] || textValue(firstFootprint.ref),
          name: firstFields[0] || textValue(firstFootprint.ref),
          value: firstFields[1] || "",
          footprint: firstFields[2] || "",
          quantity: refs.length || 1,
          lcsc_code: firstFields[3] || "",
          side: textValue(firstFootprint.layer) === "B" ? "bottom" : "top",
          bbox: bboxFromFootprint(firstFootprint),
          tokens: refs.concat(firstFields).filter(Boolean),
        };
      });
    }

    return footprints.map((footprint, index) => ({
      id: `pcbdata-footprint-${index}`,
      refs: [textValue(footprint.ref) || `FP${index + 1}`],
      ref: textValue(footprint.ref) || `FP${index + 1}`,
      name: textValue(footprint.ref),
      value: "",
      footprint: "",
      quantity: 1,
      lcsc_code: "",
      side: textValue(footprint.layer) === "B" ? "bottom" : "top",
      bbox: bboxFromFootprint(footprint),
      tokens: [textValue(footprint.ref)].filter(Boolean),
    }));
  }

  function normalizeComponents(payload) {
    const explicitComponents = Array.isArray(payload && payload.components) ? payload.components : [];
    const source = Array.isArray(payload)
      ? payload
      : explicitComponents.length
        ? explicitComponents
        : componentsFromPcbdata(payload && payload.pcbdata);
    return source.map((component, index) => {
      const refs = refsFor(component);
      const name = textValue(component.name);
      const value = textValue(component.value);
      const footprint = textValue(component.footprint);
      const lcsc = textValue(component.lcsc_code || component.lcsc || component.lcscCode);
      const tokens = asArray(component.tokens).map(textValue).filter(Boolean);
      const quantity = Number(component.quantity || component.qty || refs.length || 1);
      return {
        raw: component,
        id: `ibom-${index}`,
        index,
        refs,
        label: refs.join(", ") || name || value || `#${index + 1}`,
        name,
        value,
        footprint,
        quantity: Number.isFinite(quantity) && quantity > 0 ? quantity : 1,
        lcsc,
        side: textValue(component.side || "top").toLowerCase(),
        tokens,
        bbox: normalizeBbox(component.bbox),
      };
    });
  }

  function normalizeBbox(bbox) {
    if (!bbox) return null;
    if (Array.isArray(bbox) && bbox.length >= 4) {
      return {
        x: Number(bbox[0]),
        y: Number(bbox[1]),
        w: Number(bbox[2]),
        h: Number(bbox[3]),
      };
    }
    const x = Number(bbox.x ?? bbox.left ?? bbox.min_x ?? bbox.minX);
    const y = Number(bbox.y ?? bbox.top ?? bbox.min_y ?? bbox.minY);
    const w = Number(bbox.w ?? bbox.width ?? ((bbox.max_x ?? bbox.maxX) - (bbox.min_x ?? bbox.minX)));
    const h = Number(bbox.h ?? bbox.height ?? ((bbox.max_y ?? bbox.maxY) - (bbox.min_y ?? bbox.minY)));
    if ([x, y, w, h].some((item) => !Number.isFinite(item))) return null;
    return { x, y, w, h };
  }

  function bboxBounds(components) {
    const boxes = components.map((component) => component.bbox).filter(Boolean);
    if (!boxes.length) return null;
    const minX = Math.min(...boxes.map((box) => box.x));
    const minY = Math.min(...boxes.map((box) => box.y));
    const maxX = Math.max(...boxes.map((box) => box.x + box.w));
    const maxY = Math.max(...boxes.map((box) => box.y + box.h));
    const width = maxX - minX;
    const height = maxY - minY;
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
    return { minX, minY, width, height };
  }

  function componentRect(component, bounds, index, total) {
    if (component.bbox && isPercentBbox(component.bbox)) {
      const box = component.bbox;
      return {
        left: clamp(box.x, 0, 98),
        top: clamp(box.y, 0, 98),
        width: clamp(box.w, 2.2, 28),
        height: clamp(box.h, 2.2, 18),
      };
    }

    if (component.bbox && bounds) {
      const box = component.bbox;
      const left = ((box.x - bounds.minX) / bounds.width) * 100;
      const top = ((box.y - bounds.minY) / bounds.height) * 100;
      const width = Math.max((box.w / bounds.width) * 100, 2.2);
      const height = Math.max((box.h / bounds.height) * 100, 2.2);
      return {
        left: clamp(left, 0, 98),
        top: clamp(top, 0, 98),
        width: clamp(width, 2.2, 28),
        height: clamp(height, 2.2, 18),
      };
    }

    const cols = Math.ceil(Math.sqrt(Math.max(total, 1)));
    const rows = Math.ceil(total / cols);
    const col = index % cols;
    const row = Math.floor(index / cols);
    const cellW = 100 / cols;
    const cellH = 100 / rows;
    return {
      left: col * cellW + cellW * 0.26,
      top: row * cellH + cellH * 0.28,
      width: Math.max(Math.min(cellW * 0.48, 14), 4),
      height: Math.max(Math.min(cellH * 0.42, 10), 3),
    };
  }

  function isPercentBbox(box) {
    return [box.x, box.y, box.w, box.h].every((value) => value >= 0 && value <= 100) &&
      box.x + box.w <= 100 &&
      box.y + box.h <= 100;
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function matchesQuery(component, query) {
    if (!query) return true;
    const haystack = [
      component.refs.join(" "),
      component.name,
      component.value,
      component.footprint,
      component.lcsc,
      component.tokens.join(" "),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  }

  function createElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  }

  function renderViewer(viewer, payload) {
    const components = normalizeComponents(payload);
    viewer.textContent = "";
    viewer.dataset.interactiveBomReady = "true";

    const shell = createElement("div", "interactive-bom-shell");
    const toolbar = createElement("div", "interactive-bom-toolbar");
    const searchLabel = createElement("label", "interactive-bom-search");
    const search = document.createElement("input");
    search.type = "search";
    search.placeholder = "搜索 Ref / 名称 / 数值 / LCSC";
    search.autocomplete = "off";
    search.setAttribute("aria-label", "搜索 BOM");
    const count = createElement("span", "interactive-bom-count");
    searchLabel.append(search);
    toolbar.append(searchLabel, count);

    const boardPane = createElement("section", "interactive-bom-board-pane");
    const board = createElement("div", "interactive-bom-board");
    board.setAttribute("role", "img");
    board.setAttribute("aria-label", "PCB 元件图");
    const boardLayer = createElement("div", "interactive-bom-board-layer");
    board.append(boardLayer);
    boardPane.append(board);

    const tablePane = createElement("section", "interactive-bom-table-pane");
    const table = createElement("table", "interactive-bom-table");
    table.innerHTML =
      "<thead><tr><th>位号</th><th>名称</th><th>数值</th><th>封装</th><th>数量</th><th>LCSC</th><th>板面</th></tr></thead>";
    const tbody = createElement("tbody");
    table.append(tbody);
    tablePane.append(table);

    shell.append(toolbar, boardPane, tablePane);
    viewer.append(shell);

    if (!components.length) {
      const empty = createElement("div", "interactive-bom-empty", "暂无可视化 BOM 数据");
      boardLayer.append(empty);
      count.textContent = "0 个元件";
      return;
    }

    const bounds = bboxBounds(components);
    const markerById = new Map();
    const rowById = new Map();
    let activeId = "";
    let pinnedId = "";

    components.forEach((component) => {
      const rect = componentRect(component, bounds, component.index, components.length);
      const marker = createElement("button", "interactive-bom-marker", component.label);
      marker.type = "button";
      marker.dataset.componentId = component.id;
      marker.dataset.side = component.side;
      marker.title = [component.label, component.value, component.lcsc].filter(Boolean).join(" | ");
      marker.style.left = `${rect.left}%`;
      marker.style.top = `${rect.top}%`;
      marker.style.width = `${rect.width}%`;
      marker.style.height = `${rect.height}%`;
      boardLayer.append(marker);
      markerById.set(component.id, marker);

      const row = createElement("tr", "interactive-bom-row");
      row.dataset.componentId = component.id;
      row.tabIndex = 0;
      [
        component.label,
        component.name,
        component.value,
        component.footprint,
        String(component.quantity),
        component.lcsc,
        component.side,
      ].forEach((cellText) => row.append(createElement("td", "", cellText || "-")));
      tbody.append(row);
      rowById.set(component.id, row);

      marker.addEventListener("mouseenter", () => setActive(component.id, false, true));
      marker.addEventListener("mouseleave", () => setActive(pinnedId, false, false));
      marker.addEventListener("click", () => setActive(component.id, true, true));
      row.addEventListener("mouseenter", () => setActive(component.id, false, false));
      row.addEventListener("mouseleave", () => setActive(pinnedId, false, false));
      row.addEventListener("click", () => setActive(component.id, true, false));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          setActive(component.id, true, false);
        }
      });
    });

    function setActive(id, pin, scrollRow) {
      activeId = id || "";
      if (pin) pinnedId = pinnedId === id ? "" : id;
      markerById.forEach((marker, markerId) => {
        const isActive = markerId === activeId || markerId === pinnedId;
        marker.classList.toggle("is-active", isActive);
        marker.setAttribute("aria-pressed", markerId === pinnedId ? "true" : "false");
      });
      rowById.forEach((row, rowId) => {
        const isActive = rowId === activeId || rowId === pinnedId;
        row.classList.toggle("is-active", isActive);
      });
      if (scrollRow && id && rowById.has(id)) {
        rowById.get(id).scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }

    function applyFilter() {
      const query = search.value.trim().toLowerCase();
      let visible = 0;
      components.forEach((component) => {
        const show = matchesQuery(component, query);
        visible += show ? 1 : 0;
        markerById.get(component.id).hidden = !show;
        rowById.get(component.id).hidden = !show;
      });
      count.textContent = `${visible} / ${components.length} 个元件`;
      if ((pinnedId && markerById.get(pinnedId).hidden) || (activeId && markerById.get(activeId).hidden)) {
        pinnedId = "";
        setActive("", false, false);
      }
    }

    search.addEventListener("input", applyFilter);
    applyFilter();
  }

  function init() {
    document.querySelectorAll(VIEWER_SELECTOR).forEach((viewer) => {
      if (viewer.dataset.interactiveBomReady === "true") return;
      renderViewer(viewer, findPayload(viewer));
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.InteractiveBomViewer = {
    init,
    render: renderViewer,
  };
})();
