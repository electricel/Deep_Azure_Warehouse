(function () {
  const form = document.getElementById("assistant-form");
  const input = document.getElementById("assistant-question");
  const thread = document.getElementById("assistant-thread");
  const steps = document.getElementById("assistant-steps");
  if (!form || !input || !thread) return;

  const PAGE_SIZE = 4;
  const FOLD_LIMIT = 240;
  let currentPage = 1;
  let pager = null;
  let pageTitle = null;
  let pageCountLabel = null;
  let pageSlider = null;
  let prevPageBtn = null;
  let nextPageBtn = null;
  let latestPageBtn = null;

  const defaultStages = [
    "读取库存摘要",
    "检查 BOM / 库存缺口",
    "载入个人知识文档",
    "请求已配置模型 API",
    "写入知识节点",
  ];

  function clearWelcomeIfNeeded() {
    const first = thread.querySelector(".assistant-message.bot.welcome");
    if (first) first.remove();
  }

  function roleText(who) {
    if (who === "user") return "你";
    if (who === "error") return "错误";
    return "库存助手";
  }

  function clampPage(value, pageCount) {
    return Math.min(Math.max(1, Number(value) || 1), Math.max(1, pageCount || 1));
  }

  function ensurePager() {
    if (pager) return;
    pager = document.createElement("div");
    pager.className = "assistant-history-controls";
    pager.hidden = true;
    pager.innerHTML = `
      <div class="assistant-history-meta">
        <strong id="assistant-page-title">第 1 页 / 共 1 页</strong>
        <span id="assistant-page-count">0 条对话</span>
      </div>
      <label class="assistant-history-slider">
        <span>滑动翻页</span>
        <input id="assistant-page-slider" type="range" min="1" max="1" value="1" step="1">
      </label>
      <div class="assistant-history-actions">
        <button type="button" id="assistant-page-prev">上一页</button>
        <button type="button" id="assistant-page-next">下一页</button>
        <button type="button" id="assistant-page-latest">最新</button>
      </div>
    `;
    thread.parentNode.insertBefore(pager, thread);
    pageTitle = pager.querySelector("#assistant-page-title");
    pageCountLabel = pager.querySelector("#assistant-page-count");
    pageSlider = pager.querySelector("#assistant-page-slider");
    prevPageBtn = pager.querySelector("#assistant-page-prev");
    nextPageBtn = pager.querySelector("#assistant-page-next");
    latestPageBtn = pager.querySelector("#assistant-page-latest");

    pageSlider.addEventListener("input", () => setPage(pageSlider.value));
    prevPageBtn.addEventListener("click", () => setPage(currentPage - 1));
    nextPageBtn.addEventListener("click", () => setPage(currentPage + 1));
    latestPageBtn.addEventListener("click", () => setPage(Number(pageSlider.max) || 1));
  }

  function messageNodes() {
    return Array.from(thread.querySelectorAll(".assistant-message"));
  }

  function resetFold(node) {
    node.classList.remove("is-foldable", "is-folded", "is-expanded");
    delete node.dataset.foldReady;
    const oldToggle = node.querySelector(".message-fold-toggle");
    if (oldToggle) oldToggle.remove();
  }

  function prepareFold(node, reset) {
    if (reset) resetFold(node);
    if (node.dataset.foldReady === "1") return;
    const body = node.querySelector(".message-body");
    if (!body) return;
    const content = body.textContent.trim();
    node.dataset.foldReady = "1";
    if (content.length <= FOLD_LIMIT) return;

    node.classList.add("is-foldable", "is-folded");
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "message-fold-toggle";
    toggle.textContent = "展开全文";
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const nextFolded = !node.classList.contains("is-folded");
      node.classList.toggle("is-folded", nextFolded);
      node.classList.toggle("is-expanded", !nextFolded);
      toggle.textContent = nextFolded ? "展开全文" : "收起内容";
    });
    body.insertAdjacentElement("afterend", toggle);
  }

  function updatePagerControls(messageCount, pageCount) {
    ensurePager();
    pager.hidden = messageCount === 0;
    pageTitle.textContent = `第 ${currentPage} 页 / 共 ${pageCount} 页`;
    pageCountLabel.textContent = `${messageCount} 条对话，每页 ${PAGE_SIZE} 条`;
    pageSlider.min = "1";
    pageSlider.max = String(pageCount);
    pageSlider.value = String(currentPage);
    pageSlider.disabled = pageCount <= 1;
    prevPageBtn.disabled = currentPage <= 1;
    nextPageBtn.disabled = currentPage >= pageCount;
    latestPageBtn.disabled = currentPage >= pageCount;
  }

  function renderPagedThread(goLatest) {
    ensurePager();
    const nodes = messageNodes();
    nodes.forEach((node) => prepareFold(node));
    const pageCount = Math.max(1, Math.ceil(nodes.length / PAGE_SIZE));
    currentPage = goLatest ? pageCount : clampPage(currentPage, pageCount);

    const fragment = document.createDocumentFragment();
    nodes.forEach((node) => fragment.appendChild(node));
    thread.textContent = "";

    for (let index = 0; index < pageCount; index += 1) {
      const start = index * PAGE_SIZE;
      const items = nodes.slice(start, start + PAGE_SIZE);
      const page = document.createElement("section");
      page.className = "assistant-history-page";
      page.hidden = index + 1 !== currentPage;

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "assistant-page-toggle";
      toggle.setAttribute("aria-expanded", "true");
      toggle.innerHTML = `<strong>第 ${index + 1} 页</strong><span>${items.length} 条对话</span>`;

      const content = document.createElement("div");
      content.className = "assistant-page-content";
      items.forEach((node) => content.appendChild(node));
      toggle.addEventListener("click", () => {
        const collapsed = page.classList.toggle("collapsed");
        toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      });
      page.append(toggle, content);
      thread.appendChild(page);
    }

    if (!nodes.length) {
      thread.appendChild(fragment);
    }
    updatePagerControls(nodes.length, pageCount);
    thread.scrollTop = 0;
  }

  function setPage(page) {
    currentPage = clampPage(page, Number(pageSlider && pageSlider.max) || 1);
    renderPagedThread(false);
  }

  function addMessage(text, who, meta, options) {
    clearWelcomeIfNeeded();
    const node = document.createElement("article");
    node.className = `assistant-message ${who || "bot"}`;
    const role = document.createElement("span");
    role.className = "message-role";
    role.textContent = roleText(who);
    const body = document.createElement("div");
    body.className = "message-body";
    body.textContent = text;
    node.append(role, body);
    if (meta && meta.created_at) {
      const time = document.createElement("small");
      time.textContent = meta.created_at;
      node.appendChild(time);
    }
    thread.appendChild(node);
    prepareFold(node);
    if (!options || !options.deferPager) renderPagedThread(true);
    return { node, body };
  }

  function renderSteps(list, activeIndex, note) {
    if (!steps) return;
    steps.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "assistant-step-log";
    list.forEach((text, index) => {
      const item = document.createElement("span");
      item.className = "assistant-step";
      if (index < activeIndex) item.classList.add("done");
      if (index === activeIndex) item.classList.add("active");
      item.textContent = text;
      wrap.appendChild(item);
    });
    const status = document.createElement("strong");
    status.className = "assistant-live-status";
    status.textContent = note || (activeIndex >= list.length ? "API 调用完成" : `正在执行：${list[Math.max(0, activeIndex)]}`);
    steps.append(status, wrap);
  }

  async function loadHistory() {
    try {
      const res = await fetch("/api/assistant/history");
      const data = await res.json();
      const history = data.history || [];
      if (!history.length) return;
      thread.innerHTML = "";
      history.forEach((item) => addMessage(item.content, item.role === "assistant" ? "bot" : item.role, item, { deferPager: true }));
      renderPagedThread(true);
    } catch (_) {
      // 历史记录加载失败时保留欢迎内容。
    }
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(180, input.scrollHeight)}px`;
  }

  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    addMessage(question, "user");
    input.value = "";
    autosize();
    renderSteps(defaultStages, 0);
    const pending = addMessage("正在连接库存上下文和模型 API...", "bot");
    form.classList.add("is-sending");

    let active = 0;
    const ticker = window.setInterval(() => {
      active = Math.min(active + 1, defaultStages.length - 1);
      renderSteps(defaultStages, active);
    }, 720);

    try {
      const res = await fetch("/api/assistant/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const data = await res.json();
      window.clearInterval(ticker);
      const apiSteps = data.steps && data.steps.length ? data.steps : defaultStages;
      renderSteps(apiSteps, apiSteps.length, data.error ? "API 返回错误，已写入日志" : "已完成回答并同步知识网络");
      pending.body.textContent = [data.answer || data.error || "没有返回内容。", data.note || ""].filter(Boolean).join("\n\n");
      pending.node.classList.toggle("error", Boolean(data.error));
      prepareFold(pending.node, true);
      renderPagedThread(true);
      window.dispatchEvent(new CustomEvent("knowledge:refresh"));
    } catch (err) {
      window.clearInterval(ticker);
      renderSteps(defaultStages, defaultStages.length, "请求失败，已停止等待");
      pending.body.textContent = `请求失败：${err.message}`;
      pending.node.classList.add("error");
      prepareFold(pending.node, true);
      renderPagedThread(true);
    } finally {
      form.classList.remove("is-sending");
    }
  });

  ensurePager();
  renderPagedThread(false);
  loadHistory();
  autosize();
})();
