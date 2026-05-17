(function () {
  const panel = document.querySelector("[data-admin-analysis-panel]");
  if (!panel) return;

  const listUrl = panel.dataset.listUrl || "/api/admin/analysis-jobs?type=schematic_pdf_analysis&limit=12";
  const processUrl = panel.dataset.processUrl || "/api/admin/analysis-jobs/process-queued";
  const workerUrl = panel.dataset.workerUrl || "/api/admin/analysis-worker";
  const statusEl = panel.querySelector("[data-admin-analysis-status]");
  const rowsEl = panel.querySelector("[data-admin-analysis-rows]");
  const refreshButton = panel.querySelector("[data-admin-analysis-refresh]");
  const processButton = panel.querySelector("[data-admin-analysis-process]");
  const workerStateEl = panel.querySelector("[data-admin-analysis-worker-state]");
  const workerMetaEl = panel.querySelector("[data-admin-analysis-worker-meta]");
  const workerIntervalInput = panel.querySelector("[data-admin-analysis-worker-interval]");
  const workerBatchInput = panel.querySelector("[data-admin-analysis-worker-batch]");
  const workerToggleButton = panel.querySelector("[data-admin-analysis-worker-toggle]");
  const workerSaveButton = panel.querySelector("[data-admin-analysis-worker-save]");
  const STATUS_LABELS = {
    pending: "待分析",
    queued: "排队中",
    running: "分析中",
    completed: "已完成",
    failed: "失败",
    canceled: "已取消",
    cancelled: "已取消",
  };

  function statusLabel(value) {
    const key = String(value || "").trim().toLowerCase();
    return STATUS_LABELS[key] || String(value || "-");
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
    ));
  }

  function setBusy(isBusy) {
    [refreshButton, processButton].forEach((button) => {
      if (button) button.disabled = Boolean(isBusy);
    });
    [workerToggleButton, workerSaveButton].forEach((button) => {
      if (button) button.disabled = Boolean(isBusy);
    });
    panel.querySelectorAll("[data-admin-analysis-retry]").forEach((button) => {
      button.disabled = Boolean(isBusy);
    });
  }

  function setStatus(text, isError) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.classList.toggle("error", Boolean(isError));
  }

  function listItems(label, values) {
    if (!Array.isArray(values) || !values.length) return "";
    return `<strong>${escapeHtml(label)}</strong><ul>${values.slice(0, 4).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
  }

  function resultHtml(job) {
    const preview = job.result_preview || {};
    const summary = preview.summary || job.result_summary || "";
    const error = job.error_summary || "";
    if (!summary && !error) return "-";
    if (!preview.summary) return escapeHtml(error || summary || "-");
    const modules = Array.isArray(preview.detected_modules)
      ? preview.detected_modules.map((item) => item && item.name).filter(Boolean).join(", ")
      : "";
    const blocks = [
      `<p>${escapeHtml(summary)}</p>`,
      modules ? `<p><strong>识别模块：</strong> ${escapeHtml(modules)}</p>` : "",
      listItems("修改建议", preview.recommendations),
      listItems("人工复核", preview.manual_review),
      listItems("风险点", preview.risks),
    ].join("");
    return `<details class="admin-analysis-result"><summary>${escapeHtml(summary.slice(0, 120))}</summary>${blocks}</details>`;
  }

  function renderRows(jobs) {
    if (!rowsEl) return;
    if (!Array.isArray(jobs) || !jobs.length) {
      rowsEl.innerHTML = '<tr><td colspan="9">暂无 PDF 原理图分析任务。</td></tr>';
      return;
    }
    rowsEl.innerHTML = jobs.map((job) => {
      const retry = job.can_retry && job.retry_url
        ? `<button class="button admin-analysis-retry" type="button" data-admin-analysis-retry data-retry-url="${escapeHtml(job.retry_url)}" data-job-id="${escapeHtml(job.job_id)}">重试</button>`
        : "-";
      const bomUpload = job.bom_upload_id ? `#${escapeHtml(job.bom_upload_id)}` : "-";
      const fileLabel = job.original_filename || job.pcb_file_id || "-";
      const detailUrl = job.detail_url || (job.job_id ? `/analysis-jobs/${encodeURIComponent(job.job_id)}` : "");
      const jobIdHtml = `<code>${escapeHtml(job.job_id)}</code>`;
      const jobLink = detailUrl
        ? `<a class="admin-analysis-detail-link" href="${escapeHtml(detailUrl)}">${jobIdHtml}</a>`
        : jobIdHtml;
      return `<tr>
        <td>${jobLink}</td>
        <td>${escapeHtml(job.owner_username)}</td>
        <td>${escapeHtml(statusLabel(job.status))}</td>
        <td>${escapeHtml(job.progress)}%</td>
        <td>${bomUpload}</td>
        <td>${escapeHtml(fileLabel)}</td>
        <td>${resultHtml(job)}</td>
        <td>${escapeHtml(job.updated_at || "")}</td>
        <td>${retry}</td>
      </tr>`;
    }).join("");
  }

  function renderSummary(summary) {
    if (!summary) return;
    const cards = panel.querySelectorAll(".admin-analysis-summary .metric strong");
    const values = [summary.queued_count, summary.running_count, summary.failed_count, summary.completed_count];
    cards.forEach((node, index) => {
      node.textContent = Number(values[index] || 0);
    });
  }

  function renderWorker(worker) {
    if (!worker) return;
    const enabled = Boolean(worker.enabled);
    const running = Boolean(worker.running);
    const started = Boolean(worker.started);
    const queued = Number(worker.queued_count || (worker.queue && worker.queue.queued_count) || 0);
    if (workerIntervalInput) workerIntervalInput.value = Number(worker.interval_seconds || 120);
    if (workerBatchInput) workerBatchInput.value = Number(worker.batch_limit || 1);
    if (workerToggleButton) {
      workerToggleButton.dataset.enabled = enabled ? "1" : "0";
      workerToggleButton.textContent = enabled ? "暂停后台任务" : "恢复后台任务";
    }
    if (workerStateEl) {
      const state = enabled ? (running ? "运行中" : "已启用") : "已暂停";
      workerStateEl.textContent = `${state}；${started ? "已启动" : "未启动"}；排队 ${queued}；上次处理 ${Number(worker.last_processed_count || 0)}`;
    }
    if (workerMetaEl) {
      const lastTick = worker.last_tick_at || worker.last_tick_time || "-";
      const error = worker.last_error_summary || "无";
      workerMetaEl.textContent = `上次心跳 ${lastTick}；错误 ${error}`;
    }
  }

  async function refreshQueue(message) {
    setBusy(true);
    if (message) setStatus(message);
    try {
      const response = await fetch(listUrl, { headers: { Accept: "application/json" }, cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      renderSummary(data.summary || {});
      renderRows(data.jobs || []);
      const count = data.summary && Number.isFinite(Number(data.summary.filtered_count))
        ? Number(data.summary.filtered_count)
        : (data.jobs || []).length;
      setStatus(`最近 ${count} 个原理图分析任务`);
    } catch (error) {
      setStatus(error.message || "分析队列刷新失败。", true);
    } finally {
      setBusy(false);
    }
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function refreshWorker() {
    try {
      const response = await fetch(workerUrl, { headers: { Accept: "application/json" }, cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      renderWorker(data);
      return data;
    } catch (error) {
      setStatus(error.message || "后台任务状态刷新失败。", true);
      return null;
    }
  }

  async function saveWorkerConfig(payload, message) {
    setBusy(true);
    if (message) setStatus(message);
    try {
      const data = await postJson(workerUrl, payload);
      renderWorker(data);
      await refreshQueue();
      return data;
    } catch (error) {
      setStatus(error.message || "后台任务设置更新失败。", true);
      setBusy(false);
      return null;
    }
  }

  if (refreshButton) {
    refreshButton.addEventListener("click", async () => {
      await refreshWorker();
      refreshQueue("正在刷新 PDF 分析队列...");
    });
  }

  if (processButton) {
    processButton.addEventListener("click", async () => {
      setBusy(true);
      setStatus("正在处理下一个 PDF 分析任务...");
      try {
        const data = await postJson(processUrl, { limit: 1 });
        setStatus(`已处理 ${data.processed || 0} 个；仍有 ${data.remaining_queued || 0} 个排队。`);
        await refreshWorker();
        await refreshQueue();
      } catch (error) {
        setStatus(error.message || "处理排队任务失败。", true);
        setBusy(false);
      }
    });
  }

  panel.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-admin-analysis-retry]");
    if (!button) return;
    setBusy(true);
    setStatus("正在重试失败的 PDF 分析任务...");
    try {
      const data = await postJson(button.dataset.retryUrl, {});
      const job = data.job || {};
      setStatus(`重试任务已排队${job.job_id ? `：${job.job_id}` : "。"}`);
      await refreshWorker();
      await refreshQueue();
    } catch (error) {
      setStatus(error.message || "重试失败。", true);
      setBusy(false);
    }
  });

  if (workerToggleButton) {
    workerToggleButton.addEventListener("click", () => {
      const enabled = workerToggleButton.dataset.enabled === "1";
      saveWorkerConfig({ enabled: !enabled }, enabled ? "正在暂停本地 PDF 后台任务..." : "正在恢复本地 PDF 后台任务...");
    });
  }

  if (workerSaveButton) {
    workerSaveButton.addEventListener("click", () => {
      saveWorkerConfig(
        {
          interval_seconds: Number(workerIntervalInput && workerIntervalInput.value),
          batch_limit: Number(workerBatchInput && workerBatchInput.value),
        },
        "正在保存本地 PDF 后台任务设置..."
      );
    });
  }

  refreshWorker();
})();
