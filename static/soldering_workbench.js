(function () {
  const root = document.querySelector(".soldering-workbench");
  if (!root) return;

  const bomId = root.dataset.bomId || "";
  const rows = Array.from(root.querySelectorAll(".bom-link-table tbody tr[data-row]"));
  const markers = Array.from(root.querySelectorAll(".pcb-marker"));
  const statusEl = document.getElementById("soldering-status");
  const feedback = document.getElementById("soldering-feedback");
  const startBtn = document.getElementById("start-soldering");
  const finishBtn = document.getElementById("finish-soldering");
  const boardInput = document.getElementById("board-count");
  const pcbSelect = document.getElementById("pcb-file-id");
  const notesInput = document.getElementById("soldering-notes");
  const consumptionHost = document.getElementById("soldering-consumption-summary");
  const analysisMeta = root.querySelector(".pcb-selected-meta");
  const analysisStatusEl = root.querySelector(".pcb-selected-meta [data-analysis-status]");
  const analysisForm = analysisMeta ? analysisMeta.querySelector(".pcb-analysis-form") : null;
  const analysisButton = analysisForm ? analysisForm.querySelector("button") : null;
  const analysisHistoryPanel = root.querySelector("[data-analysis-history-panel]");
  const analysisHistoryList = root.querySelector("[data-analysis-history-list]");
  const analysisHistoryCount = root.querySelector("[data-analysis-history-count]");
  const analysisHistoryLatest = root.querySelector("[data-analysis-history-latest]");
  let analysisStatusUrl = root.dataset.analysisStatusUrl || (analysisForm ? analysisForm.getAttribute("action") || "" : "");
  let analysisPollTimer = 0;
  const ANALYSIS_POLL_INTERVAL_MS = 5000;
  const STATUS_LABELS = {
    pending: "待分析",
    queued: "排队中",
    running: "分析中",
    completed: "已完成",
    failed: "失败",
    canceled: "已取消",
    cancelled: "已取消",
    awaiting_pcb: "等待 PCB 文件",
    soldering_active: "焊接中",
    completed_with_shortage: "已完成，有缺口",
  };

  function statusLabel(value) {
    const key = normalizeAnalysisStatus(value);
    return STATUS_LABELS[key] || String(value || "-");
  }

  function setFeedback(message, isError) {
    if (!feedback) return;
    feedback.textContent = message;
    feedback.classList.toggle("error", Boolean(isError));
  }

  function setActiveRow(rowId) {
    rows.forEach((row) => row.classList.toggle("is-active", row.dataset.row === rowId));
    markers.forEach((marker) => {
      const active = marker.dataset.row === rowId;
      marker.classList.toggle("is-active", active);
      marker.classList.toggle("is-muted", Boolean(rowId) && !active);
    });
  }

  async function postJson(url, payload) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  async function fetchAnalysisJson(url, options) {
    const res = await fetch(url, {
      method: (options && options.method) || "GET",
      headers: { Accept: "application/json", "Cache-Control": "no-store" },
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function numberValue(value) {
    const parsed = Number(value || 0);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function normalizeAnalysisStatus(value) {
    return String(value || "").trim().toLowerCase();
  }

  function isActiveAnalysisStatus(status) {
    const normalized = normalizeAnalysisStatus(status);
    return normalized === "queued" || normalized === "running";
  }

  function analysisButtonLabel(status) {
    const normalized = normalizeAnalysisStatus(status);
    if (normalized === "queued") return "已排队";
    if (normalized === "running") return "分析中";
    if (normalized === "completed") return "再次分析";
    return "提交分析";
  }

  function analysisStatusClass(status) {
    return normalizeAnalysisStatus(status).replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";
  }

  function analysisProgress(job) {
    const parsed = Number(job && job.progress);
    if (!Number.isFinite(parsed)) return null;
    return Math.max(0, Math.min(100, Math.round(parsed)));
  }

  function safeAnalysisDetailUrl(url, jobId) {
    const candidate = String(url || "");
    if (candidate.indexOf("/analysis-jobs/") === 0) return candidate;
    return jobId ? `/analysis-jobs/${encodeURIComponent(jobId)}` : "";
  }

  function analysisDetailUrl(job, jobId) {
    return safeAnalysisDetailUrl(job && job.detail_url, jobId);
  }

  function analysisStatusText(data, status, progress) {
    const file = data && data.pcb_file ? data.pcb_file : {};
    if (file.status_text) {
      return progress == null ? String(file.status_text) : `${file.status_text} - ${progress}%`;
    }
    if (!status) return "分析状态不可用。";
    return progress == null ? `分析${statusLabel(status)}` : `分析${statusLabel(status)} - ${progress}%`;
  }

  function updateAnalysisDetailLink(url) {
    if (!analysisMeta) return;
    let link = analysisMeta.querySelector("[data-analysis-detail-link]");
    if (!url) {
      if (link) link.remove();
      return;
    }
    if (!link) {
      link = document.createElement("a");
      link.className = "button pcb-analysis-button pcb-analysis-detail-link";
      link.dataset.analysisDetailLink = "true";
      link.textContent = "查看分析";
      const historyLink = Array.from(analysisMeta.querySelectorAll("a")).find((item) => item.getAttribute("href") === "/analysis-jobs");
      analysisMeta.insertBefore(link, historyLink || null);
    }
    link.href = url;
  }

  function removeAnalysisHistoryEmpty() {
    if (!analysisHistoryList) return;
    analysisHistoryList.querySelectorAll("[data-analysis-history-empty]").forEach((item) => item.remove());
  }

  function analysisHistoryResultText(job, status) {
    const normalized = normalizeAnalysisStatus(status || (job && job.status));
    if (normalized === "failed" && job && job.error_summary) return String(job.error_summary);
    if (job && job.result_summary) return String(job.result_summary);
    if (job && job.analysis_result_summary) return String(job.analysis_result_summary);
    if (normalized === "queued") return "等待本地后台分析。";
    if (normalized === "running") return "正在分析。";
    if (normalized === "completed") return "已完成，但没有记录预览摘要。";
    return "暂无结果预览。";
  }

  function analysisHistoryContextText(job) {
    const bits = [];
    if (job && job.is_selected_pcb) bits.push("当前 PDF");
    ["file_kind", "kind", "document_type", "status_text"].forEach((key) => {
      const text = String((job && job[key]) || "").trim();
      if (text && bits.indexOf(text) === -1) bits.push(text);
    });
    return bits.length ? bits.join(" | ") : "BOM schematic context";
  }

  function findHistoryJob(payload, jobId) {
    const history = payload && payload.history ? payload.history : null;
    const historyJobs = history && Array.isArray(history.jobs) ? history.jobs : [];
    const found = historyJobs.find((item) => String((item && item.job_id) || "") === jobId);
    if (found) return found;
    if (history && history.cross_user_restricted) return null;
    const job = payload && payload.job ? payload.job : {};
    const file = payload && payload.pcb_file ? payload.pcb_file : {};
    if (!jobId) return null;
    return {
      job_id: jobId,
      status: job.status || (payload && payload.analysis_status) || file.analysis_status || "",
      progress: job.progress,
      detail_url: analysisDetailUrl(job, jobId),
      original_filename: file.original_filename || file.pcb_file_id || "原理图 PDF",
      file_kind: file.kind || "",
      kind: file.kind || "",
      document_type: file.document_type || "",
      status_text: file.status_text || "",
      error_summary: job.error_summary || file.analysis_error_summary || "",
      result_summary: file.analysis_result_summary || "",
      created_at: job.created_at || "",
      updated_at: job.updated_at || file.analysis_updated_at || "",
      is_selected_pcb: true,
    };
  }

  function createAnalysisHistoryRow(jobId) {
    if (!analysisHistoryList || !jobId) return null;
    removeAnalysisHistoryEmpty();
    const row = document.createElement("article");
    row.className = "analysis-workbench-job";
    row.dataset.analysisHistoryJobId = jobId;

    const top = document.createElement("div");
    top.className = "analysis-workbench-job-top";
    const detail = document.createElement("a");
    detail.className = "analysis-history-detail-link";
    detail.dataset.analysisHistoryDetailLink = "true";
    const code = document.createElement("code");
    code.textContent = jobId || "-";
    detail.appendChild(code);
    const statusBadge = document.createElement("span");
    statusBadge.dataset.analysisHistoryStatus = "true";
    const progressBadge = document.createElement("span");
    progressBadge.className = "analysis-workbench-progress";
    progressBadge.dataset.analysisHistoryProgress = "true";
    top.appendChild(detail);
    top.appendChild(statusBadge);
    top.appendChild(progressBadge);

    const source = document.createElement("div");
    source.className = "analysis-workbench-source";
    const file = document.createElement("strong");
    file.dataset.analysisHistoryFile = "true";
    const context = document.createElement("span");
    context.dataset.analysisHistoryContext = "true";
    source.appendChild(file);
    source.appendChild(context);

    const result = document.createElement("p");
    result.className = "analysis-workbench-result";
    result.dataset.analysisHistoryResult = "true";

    const meta = document.createElement("div");
    meta.className = "analysis-workbench-meta";
    const updated = document.createElement("span");
    updated.dataset.analysisHistoryUpdated = "true";
    const created = document.createElement("span");
    created.dataset.analysisHistoryCreated = "true";
    const open = document.createElement("a");
    open.className = "button analysis-workbench-open";
    open.dataset.analysisHistoryOpen = "true";
    open.textContent = "详情";
    meta.appendChild(updated);
    meta.appendChild(created);
    meta.appendChild(open);

    row.appendChild(top);
    row.appendChild(source);
    row.appendChild(result);
    row.appendChild(meta);
    analysisHistoryList.prepend(row);
    return row;
  }

  function updateAnalysisHistorySummary(history, job) {
    if (!analysisHistoryPanel) return;
    const summary = history && history.summary ? history.summary : {};
    const rowsCount = analysisHistoryList
      ? analysisHistoryList.querySelectorAll(".analysis-workbench-job[data-analysis-history-job-id]").length
      : 0;
    if (analysisHistoryCount) {
      const historyCount = history && Number.isFinite(Number(history.count)) ? Number(history.count) : rowsCount;
      const countValue = Math.max(historyCount, rowsCount);
      const countText = summary.count_text && historyCount >= rowsCount ? summary.count_text : `最近 ${countValue} 个`;
      analysisHistoryCount.textContent = countText;
    }
    if (analysisHistoryLatest) {
      let latestText = summary.latest_text || "";
      if (!latestText && job) {
        const status = normalizeAnalysisStatus(job.status);
        const timestamp = String(job.updated_at || job.created_at || "").trim();
        latestText = status ? `最新 ${statusLabel(status)}${timestamp ? ` ${timestamp}` : ""}` : "暂无最新任务";
      }
      analysisHistoryLatest.textContent = latestText || "暂无最新任务";
    }
  }

  function updateAnalysisHistoryRow(payload, jobId, status, progress, detailUrl) {
    const history = payload && payload.history ? payload.history : null;
    const historyJob = findHistoryJob(payload, jobId);
    if (history && history.cross_user_restricted) {
      updateAnalysisHistorySummary(history, null);
      return;
    }
    if (!historyJob || !jobId) {
      updateAnalysisHistorySummary(history, null);
      return;
    }
    let row = Array.from(root.querySelectorAll(".analysis-workbench-job[data-analysis-history-job-id]")).find(
      (item) => item.dataset.analysisHistoryJobId === jobId
    );
    if (!row) row = createAnalysisHistoryRow(jobId);
    if (!row) return;
    if (analysisHistoryList && analysisHistoryList.firstElementChild !== row) analysisHistoryList.prepend(row);
    const rowStatus = normalizeAnalysisStatus(historyJob.status || status);
    const rowProgress = analysisProgress(historyJob);
    const displayProgress = rowProgress == null ? progress : rowProgress;
    const rowDetailUrl = safeAnalysisDetailUrl(historyJob.detail_url || detailUrl, jobId);
    const className = analysisStatusClass(rowStatus);
    Array.from(row.classList).forEach((item) => {
      if (item.indexOf("analysis-workbench-job-") === 0) row.classList.remove(item);
    });
    row.classList.add(`analysis-workbench-job-${className}`);
    row.dataset.analysisStatus = rowStatus;
    row.dataset.analysisHistoryJobId = jobId;
    const statusBadge = row.querySelector("[data-analysis-history-status]");
    if (statusBadge) {
      statusBadge.textContent = statusLabel(rowStatus);
      statusBadge.className = `analysis-history-status status-${className}`;
    }
    const progressBadge = row.querySelector("[data-analysis-history-progress]");
    if (progressBadge && displayProgress != null) progressBadge.textContent = `${displayProgress}%`;
    const detailLinks = row.querySelectorAll("[data-analysis-history-detail-link], [data-analysis-history-open]");
    detailLinks.forEach((link) => {
      if (rowDetailUrl) link.href = rowDetailUrl;
    });
    const code = row.querySelector("[data-analysis-history-detail-link] code");
    if (code) code.textContent = jobId || "-";
    const fileLabel = row.querySelector("[data-analysis-history-file]");
    if (fileLabel) fileLabel.textContent = historyJob.original_filename || historyJob.pcb_file_id || "原理图 PDF";
    const context = row.querySelector("[data-analysis-history-context]");
    if (context) context.textContent = analysisHistoryContextText(historyJob);
    const result = row.querySelector("[data-analysis-history-result]");
    if (result) result.textContent = analysisHistoryResultText(historyJob, rowStatus);
    const updated = row.querySelector("[data-analysis-history-updated]");
    if (updated) updated.textContent = `更新 ${historyJob.updated_at || "-"}`;
    const created = row.querySelector("[data-analysis-history-created]");
    if (created) created.textContent = `创建 ${historyJob.created_at || "-"}`;
    updateAnalysisHistorySummary(history, historyJob);
  }

  function applyAnalysisStatus(data) {
    const payload = data || {};
    const job = payload.job || {};
    const file = payload.pcb_file || {};
    const status = normalizeAnalysisStatus(job.status || payload.analysis_status || file.analysis_status || root.dataset.analysisStatus);
    const progress = analysisProgress(job);
    const jobId = String(job.job_id || file.analysis_job_id || root.dataset.analysisJobId || "");
    const active = Object.prototype.hasOwnProperty.call(payload, "active") ? Boolean(payload.active) : isActiveAnalysisStatus(status);
    const detailUrl = analysisDetailUrl(job, jobId);
    root.dataset.analysisStatus = status;
    root.dataset.analysisActive = active ? "true" : "false";
    root.dataset.analysisJobId = jobId;
    root.dataset.analysisDetailUrl = detailUrl;
    if (analysisStatusEl) analysisStatusEl.textContent = analysisStatusText(payload, status, progress);
    if (analysisButton) {
      analysisButton.textContent = analysisButtonLabel(status);
      analysisButton.disabled = active && isActiveAnalysisStatus(status);
    }
    updateAnalysisDetailLink(detailUrl);
    updateAnalysisHistoryRow(payload, jobId, status, progress, detailUrl);
    return { status, active, jobId };
  }

  function clearAnalysisPoll() {
    if (analysisPollTimer) {
      window.clearTimeout(analysisPollTimer);
      analysisPollTimer = 0;
    }
  }

  function shouldPollAnalysis() {
    return Boolean(analysisStatusUrl && isActiveAnalysisStatus(root.dataset.analysisStatus) && root.dataset.analysisActive !== "false");
  }

  function scheduleAnalysisPoll(delay) {
    clearAnalysisPoll();
    if (!shouldPollAnalysis()) return;
    analysisPollTimer = window.setTimeout(pollAnalysisStatus, delay == null ? ANALYSIS_POLL_INTERVAL_MS : delay);
  }

  async function pollAnalysisStatus() {
    analysisPollTimer = 0;
    if (!shouldPollAnalysis()) return;
    try {
      const data = await fetchAnalysisJson(analysisStatusUrl, { method: "GET" });
      const state = applyAnalysisStatus(data);
      if (state.active && isActiveAnalysisStatus(state.status)) scheduleAnalysisPoll();
    } catch (err) {
      if (shouldPollAnalysis()) scheduleAnalysisPoll();
    }
  }

  function consumptionSummary(data) {
    const payload = data || {};
    const consumption = payload.consumption || payload;
    const summary = consumption.summary || payload.summary || payload.totals || {};
    return {
      consumption,
      required: numberValue(summary.required_quantity),
      consumed: numberValue(summary.consumed_quantity),
      shortage: numberValue(summary.shortage_quantity),
      shortageCount: numberValue(summary.shortage_count),
      pendingPurchases: numberValue((consumption.purchase_status || payload.purchase_status || {}).pending_count),
    };
  }

  function renderConsumptionSummary(data) {
    if (!consumptionHost) return;
    const details = consumptionSummary(data);
    const items = Array.isArray(details.consumption.items) ? details.consumption.items : [];
    const shortageRows = items.filter((item) => numberValue(item.shortage_quantity) > 0).slice(0, 8);
    const rowsHtml = shortageRows
      .map((item) => {
        const inventoryNames = Array.isArray(item.inventory_names) ? item.inventory_names.filter(Boolean).join(", ") : item.inventory_name || "";
        return `<tr>
          <td>${escapeHtml(item.category || "BOM")}</td>
          <td>${escapeHtml(item.name || "")}</td>
          <td>${numberValue(item.required_quantity)}</td>
          <td>${numberValue(item.consumed_quantity)}</td>
          <td>${numberValue(item.shortage_quantity)}</td>
          <td>${escapeHtml(item.status || "")}</td>
          <td>${escapeHtml(item.match_key || "")}</td>
          <td>${escapeHtml(inventoryNames || "-")}</td>
          <td>${escapeHtml(item.bom_item_id == null ? "-" : item.bom_item_id)}</td>
        </tr>`;
      })
      .join("");
    const shortageHtml = rowsHtml
      ? `<div class="soldering-shortage-table"><table><thead><tr><th>类别</th><th>名称</th><th>需用</th><th>已耗</th><th>缺口</th><th>状态</th><th>匹配</th><th>库存</th><th>BOM</th></tr></thead><tbody>${rowsHtml}</tbody></table></div>`
      : '<div class="soldering-shortage-empty">无缺口。</div>';
    consumptionHost.innerHTML = `<div class="soldering-consumption-summary">
      <div class="soldering-consumption-head">
        <strong>消耗/缺口</strong>
        <span>领料 ${details.consumed} / 需用 ${details.required}</span>
      </div>
      <div class="soldering-consumption-metrics">
        <span>已耗 ${details.consumed}</span>
        <span>缺口 ${details.shortage}</span>
        <span>缺口项 ${details.shortageCount}</span>
        <span>采购待收 ${details.pendingPurchases}</span>
      </div>
      ${shortageHtml}
    </div>`;
  }

  let hoverStartAttempted = Boolean(root.dataset.activeJobId);

  async function startSoldering(feedbackMessage) {
    if (!bomId) return null;
    if (root.dataset.activeJobId) return null;
    const boardCount = Number(boardInput && boardInput.value) || 0;
    if (!boardCount || boardCount <= 0) {
      setFeedback("请先填写本次 PCB 制板数量，再联动 BOM 行和 PCB 位号。", true);
      return null;
    }
    try {
      if (startBtn) startBtn.disabled = true;
      const data = await postJson(`/api/boms/${encodeURIComponent(bomId)}/soldering/start`, {
        board_count: boardCount,
        pcb_file_id: pcbSelect && pcbSelect.value ? pcbSelect.value : "",
        notes: notesInput && notesInput.value ? notesInput.value : "",
      });
      root.dataset.activeJobId = data.job.job_id;
      if (statusEl) statusEl.textContent = data.job.status;
      if (finishBtn) finishBtn.disabled = false;
      setFeedback(feedbackMessage || (data.resumed ? "已继续进行中的焊接任务。" : "已开始焊接任务。"), false);
      return data;
    } catch (err) {
      hoverStartAttempted = false;
      setFeedback(err.message || "开始焊接失败。", true);
      return null;
    } finally {
      if (startBtn) startBtn.disabled = false;
    }
  }

  function syncHoverSolderingState() {
    if (hoverStartAttempted || root.dataset.activeJobId) return;
    hoverStartAttempted = true;
    setFeedback("正在用 BOM 行定位 PCB 器件，并同步焊接状态...", false);
    startSoldering("已同步：这个 BOM 已进入 PCB 焊接跟踪。");
  }

  rows.forEach((row) => {
    row.addEventListener("mouseenter", () => {
      setActiveRow(row.dataset.row);
      syncHoverSolderingState();
    });
    row.addEventListener("focusin", () => {
      setActiveRow(row.dataset.row);
      syncHoverSolderingState();
    });
    row.addEventListener("mouseleave", () => setActiveRow(""));
  });

  markers.forEach((marker) => {
    marker.addEventListener("mouseenter", () => setActiveRow(marker.dataset.row));
    marker.addEventListener("focus", () => setActiveRow(marker.dataset.row));
    marker.addEventListener("mouseleave", () => setActiveRow(""));
  });

  if (pcbSelect) {
    pcbSelect.addEventListener("change", () => {
      const next = new URL(window.location.href);
      if (pcbSelect.value) next.searchParams.set("pcb_file_id", pcbSelect.value);
      else next.searchParams.delete("pcb_file_id");
      window.location.href = next.toString();
    });
  }

  if (startBtn) {
    startBtn.addEventListener("click", () => startSoldering());
  }

  if (analysisForm) {
    analysisForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!analysisStatusUrl) {
        analysisStatusUrl = analysisForm.getAttribute("action") || analysisForm.action || "";
        root.dataset.analysisStatusUrl = analysisStatusUrl;
      }
      if (!analysisStatusUrl) return;
      try {
        if (analysisButton) analysisButton.disabled = true;
        const data = await fetchAnalysisJson(analysisStatusUrl, { method: "POST" });
        const state = applyAnalysisStatus(data);
        setFeedback(data.created ? "PDF 分析任务已排队。" : "PDF 分析任务已在进行中。", false);
        if (state.active && isActiveAnalysisStatus(state.status)) scheduleAnalysisPoll();
      } catch (err) {
        if (analysisButton) analysisButton.disabled = false;
        setFeedback(err.message || "PDF 分析排队失败。", true);
      }
    });
  }

  if (finishBtn) {
    finishBtn.addEventListener("click", async () => {
      const jobId = root.dataset.activeJobId || "";
      if (!jobId) {
        setFeedback("没有进行中的焊接任务。", true);
        return;
      }
      try {
        finishBtn.disabled = true;
        const data = await postJson(`/api/soldering/jobs/${encodeURIComponent(jobId)}/finish`, {
          notes: notesInput && notesInput.value ? notesInput.value : "",
        });
        root.dataset.activeJobId = "";
        if (statusEl) statusEl.textContent = data.job.status;
        renderConsumptionSummary(data);
        const totals = consumptionSummary(data);
        setFeedback(
          `焊接已结束。需用：${totals.required}；已耗：${totals.consumed}；缺口：${totals.shortage}；缺口项：${totals.shortageCount}。`,
          false
        );
      } catch (err) {
        finishBtn.disabled = false;
        setFeedback(err.message || "结束焊接失败。", true);
      }
    });
  }

  if (shouldPollAnalysis()) scheduleAnalysisPoll();
})();
