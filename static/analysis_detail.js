(function () {
  const root = document.querySelector("[data-analysis-detail]");
  const forms = Array.from(document.querySelectorAll("[data-analysis-retry-form]"));
  if (!root && !forms.length) return;

  const ACTIVE_STATUSES = new Set(["queued", "running", "processing", "analyzing", "retrying"]);
  const TERMINAL_STATUSES = new Set(["completed", "failed", "canceled", "cancelled"]);
  const POLL_INTERVAL_MS = 5000;
  let pollTimer = 0;
  let polling = false;
  const STATUS_LABELS = {
    pending: "待分析",
    queued: "排队中",
    running: "分析中",
    processing: "处理中",
    analyzing: "分析中",
    retrying: "重试排队中",
    completed: "已完成",
    failed: "失败",
    canceled: "已取消",
    cancelled: "已取消",
    unknown: "未知",
  };

  function statusLabel(value) {
    const status = normalizeStatus(value);
    return STATUS_LABELS[status] || compactText(value) || "未知";
  }

  function compactText(value) {
    return String(value == null ? "" : value)
      .replace(/[\r\n\t]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function sanitizeMessage(value, sensitiveFallback) {
    let text = compactText(value);
    if (!text) return "";
    text = text
      .replace(/api[_-]?key\s*[:=]\s*[^,\s;]+/gi, "api_key=[redacted]")
      .replace(/(authorization|bearer|token)\s*[:=]\s*[^,\s;]+/gi, "$1=[redacted]")
      .replace(/[A-Za-z0-9_-]*secret[A-Za-z0-9_-]*/gi, "[redacted]")
      .replace(/sk-[A-Za-z0-9_-]{8,}/gi, "[redacted]");
    if (/\b(request_payload|stored_filename|relative_path|result_json)\b/i.test(text)) {
      return sensitiveFallback || "服务器返回了已脱敏的信息。";
    }
    if (/[A-Za-z]:\\|(^|[^a-z])data[\\/]|\/data\//i.test(text)) {
      return sensitiveFallback || "服务器返回了已脱敏的信息。";
    }
    if (/\b(model request|messages|prompt|raw json)\b/i.test(text)) {
      return sensitiveFallback || "服务器返回了已脱敏的信息。";
    }
    return text.slice(0, 220);
  }

  function normalizeStatus(value) {
    return compactText(value)
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80);
  }

  function retryJob(data) {
    if (!data || typeof data !== "object") return {};
    const job = data.job;
    return job && typeof job === "object" ? job : {};
  }

  function safeJobId(jobOrId) {
    const value = typeof jobOrId === "object" && jobOrId
      ? jobOrId.job_id
      : jobOrId;
    return compactText(value).replace(/[^A-Za-z0-9_.:-]/g, "");
  }

  function safeDetailUrl(jobOrUrl, fallbackJobId) {
    const job = jobOrUrl && typeof jobOrUrl === "object" ? jobOrUrl : {};
    const jobId = safeJobId(job.job_id || fallbackJobId);
    const detailUrl = compactText(job.detail_url || (typeof jobOrUrl === "string" ? jobOrUrl : ""));
    if (detailUrl.indexOf("/analysis-jobs/") === 0) return detailUrl;
    return jobId ? `/analysis-jobs/${encodeURIComponent(jobId)}` : "";
  }

  function safeStatusUrl(url, fallbackJobId) {
    const candidate = compactText(url);
    if (candidate.indexOf("/api/analysis-jobs/") === 0 && !/\/(retry|status)$/.test(candidate)) {
      return candidate;
    }
    const jobId = safeJobId(fallbackJobId);
    return jobId ? `/api/analysis-jobs/${encodeURIComponent(jobId)}` : "";
  }

  function clampProgress(value, fallback) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback == null ? 0 : fallback;
    return Math.max(0, Math.min(100, Math.round(parsed)));
  }

  function rootJobId() {
    return root ? safeJobId(root.dataset.analysisJobId) : "";
  }

  function rootDetailUrl() {
    return root ? safeDetailUrl(root.dataset.analysisDetailUrl, rootJobId()) : "";
  }

  function setFeedback(element, message, isError) {
    if (!element) return;
    const text = compactText(message);
    element.textContent = text;
    element.classList.toggle("error", Boolean(isError));
    if (text) element.removeAttribute("hidden");
    else element.setAttribute("hidden", "");
  }

  function setLink(link, url, label) {
    if (!link) return;
    const safeUrl = safeDetailUrl(url);
    if (!safeUrl) {
      link.setAttribute("hidden", "");
      link.removeAttribute("href");
      return;
    }
    link.textContent = label || "打开分析详情";
    link.setAttribute("href", safeUrl);
    link.removeAttribute("hidden");
  }

  function setResultLink(link, url) {
    setLink(link, url, "打开重试任务");
  }

  function setAll(selector, text) {
    if (!root) return;
    root.querySelectorAll(selector).forEach((node) => {
      node.textContent = text;
    });
  }

  function setHidden(node, hidden) {
    if (!node) return;
    if (hidden) node.setAttribute("hidden", "");
    else node.removeAttribute("hidden");
  }

  function payloadJob(data) {
    const job = retryJob(data);
    return job && typeof job === "object" ? job : {};
  }

  function payloadPcbFile(data) {
    const file = data && data.pcb_file;
    return file && typeof file === "object" ? file : {};
  }

  function payloadStatus(data) {
    const job = payloadJob(data);
    const file = payloadPcbFile(data);
    return normalizeStatus(job.status || (data && data.analysis_status) || file.analysis_status || (root && root.dataset.analysisStatus));
  }

  function payloadProgress(data) {
    const job = payloadJob(data);
    const file = payloadPcbFile(data);
    const fallback = root ? clampProgress(root.dataset.analysisProgress, 0) : 0;
    if (job.progress != null && job.progress !== "") return clampProgress(job.progress, fallback);
    if (file.analysis_progress != null && file.analysis_progress !== "") {
      return clampProgress(file.analysis_progress, fallback);
    }
    return fallback;
  }

  function payloadSummary(data) {
    const job = payloadJob(data);
    const file = payloadPcbFile(data);
    if (file.analysis_result_summary) return sanitizeMessage(file.analysis_result_summary);
    if (job.result_summary) return sanitizeMessage(job.result_summary);
    const result = job.result_json;
    if (result && typeof result === "object" && !Array.isArray(result) && result.summary) {
      return sanitizeMessage(result.summary);
    }
    return "";
  }

  function payloadError(data) {
    const job = payloadJob(data);
    const file = payloadPcbFile(data);
    return sanitizeMessage(
      job.error_summary || file.analysis_error_summary || "",
      "Analysis failed. The server returned a sanitized error."
    );
  }

  function shouldPollAnalysis() {
    if (!root) return false;
    return ACTIVE_STATUSES.has(normalizeStatus(root.dataset.analysisStatus));
  }

  function stopAnalysisPoll() {
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = 0;
  }

  function scheduleAnalysisPoll(immediate) {
    stopAnalysisPoll();
    if (!root) return;
    if (!immediate && !shouldPollAnalysis()) return;
    pollTimer = window.setTimeout(pollAnalysisStatus, immediate ? 250 : POLL_INTERVAL_MS);
  }

  function applyAnalysisStatus(data) {
    if (!root) return "";
    const job = payloadJob(data);
    const jobId = safeJobId(job) || rootJobId();
    const detailUrl = safeDetailUrl(job, jobId) || rootDetailUrl();
    const statusUrl = safeStatusUrl(job.status_url || (root && root.dataset.analysisStatusUrl), jobId);
    const status = payloadStatus(data) || "unknown";
    const progress = payloadProgress(data);
    const pollFeedback = root.querySelector("[data-analysis-poll-feedback]");
    const resultLink = root.querySelector("[data-analysis-result-link]");
    const errorLink = root.querySelector("[data-analysis-error-link]");
    const errorPanel = root.querySelector("[data-analysis-error-panel]");
    const errorText = root.querySelector("[data-analysis-error-text]");
    const resultSummary = root.querySelector("[data-analysis-result-summary]");
    const jobCode = root.querySelector("[data-analysis-job-code]");

    root.dataset.analysisJobId = jobId;
    root.dataset.analysisStatusUrl = statusUrl;
    root.dataset.analysisDetailUrl = detailUrl;
    root.dataset.analysisStatus = status;
    root.dataset.analysisProgress = String(progress);
    if (jobCode && jobId) jobCode.textContent = jobId;
    setAll("[data-analysis-status-text]", statusLabel(status));
    setAll("[data-analysis-progress-text]", `${progress}%`);

    if (status === "completed") {
      const summary = payloadSummary(data);
      if (summary && resultSummary) {
        resultSummary.textContent = summary;
        resultSummary.classList.remove("muted");
      }
      setHidden(errorPanel, true);
      setLink(resultLink, detailUrl, "打开最新结果");
      setHidden(errorLink, true);
      setFeedback(pollFeedback, "分析已完成。打开最新结果可查看完整预览。", false);
    } else if (status === "failed") {
      const error = payloadError(data) || "分析失败，未记录可展示的脱敏错误摘要。";
      if (errorText) errorText.textContent = error;
      setHidden(errorPanel, false);
      setHidden(resultLink, true);
      setLink(errorLink, detailUrl, "查看失败原因");
      setFeedback(pollFeedback, "分析失败。请查看脱敏后的错误摘要。", true);
    } else if (ACTIVE_STATUSES.has(status)) {
      setHidden(errorPanel, true);
      setHidden(resultLink, true);
      setHidden(errorLink, true);
      setFeedback(pollFeedback, `分析${statusLabel(status)}，当前 ${progress}%。`, false);
    } else {
      setHidden(resultLink, true);
      setHidden(errorLink, true);
      setFeedback(pollFeedback, "", false);
    }

    if (TERMINAL_STATUSES.has(status)) stopAnalysisPoll();
    return status;
  }

  async function fetchAnalysisStatus(url) {
    const response = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json", "Cache-Control": "no-store" },
      cache: "no-store",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = sanitizeMessage(
        data && data.error,
        "状态刷新失败，服务器返回了已脱敏的错误。"
      ) || `状态刷新失败，HTTP ${response.status}。`;
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  async function pollAnalysisStatus() {
    if (!root || polling) return;
    const statusUrl = safeStatusUrl(root.dataset.analysisStatusUrl, rootJobId());
    if (!statusUrl || !shouldPollAnalysis()) return;
    polling = true;
    try {
      const data = await fetchAnalysisStatus(statusUrl);
      const status = applyAnalysisStatus(data);
      if (ACTIVE_STATUSES.has(status)) scheduleAnalysisPoll(false);
    } catch (error) {
      const pollFeedback = root.querySelector("[data-analysis-poll-feedback]");
      setFeedback(
        pollFeedback,
        sanitizeMessage(error && error.message, "状态刷新失败，服务器返回了已脱敏的错误。") ||
          "状态刷新失败。",
        true
      );
      if (![401, 403, 404].includes(Number(error && error.status)) && shouldPollAnalysis()) {
        scheduleAnalysisPoll(false);
      }
    } finally {
      polling = false;
    }
  }

  async function postRetry(url) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: "{}",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = sanitizeMessage(
        data && data.error,
        "重试失败，服务器返回了已脱敏的错误。"
      ) || `重试失败，HTTP ${response.status}。`;
      throw new Error(message);
    }
    return data;
  }

  forms.forEach((form) => {
    const button = form.querySelector("[data-analysis-retry-button]");
    const feedback = form.querySelector("[data-analysis-retry-feedback]");
    const resultLink = form.querySelector("[data-analysis-retry-result-link]");
    const originalButtonText = button ? button.textContent : "";

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const action = form.getAttribute("action") || "";
      if (!action) {
        setFeedback(feedback, "重试失败：缺少重试接口。", true);
        return;
      }
      setResultLink(resultLink, "");
      setFeedback(feedback, "正在提交重试任务...", false);
      if (button) {
        button.disabled = true;
        button.textContent = "正在重试...";
      }
      try {
        const data = await postRetry(action);
        const job = retryJob(data);
        const jobId = safeJobId(job);
        const detailUrl = safeDetailUrl(job, jobId);
        const status = normalizeStatus(job.status);
        const queuedMessage = data && data.created === false
          ? "重试任务已在队列中。"
          : "重试任务已排队。";
        setFeedback(feedback, status ? `${queuedMessage} 状态：${statusLabel(status)}。` : queuedMessage, false);
        setResultLink(resultLink, detailUrl || (jobId ? `/analysis-jobs/${encodeURIComponent(jobId)}` : ""));
        if (button) button.textContent = "重试已排队";
        if (root) {
          applyAnalysisStatus(data);
          if (ACTIVE_STATUSES.has(normalizeStatus(root.dataset.analysisStatus))) {
            scheduleAnalysisPoll(true);
          }
        }
      } catch (error) {
        setFeedback(
          feedback,
          sanitizeMessage(error && error.message, "重试失败，服务器返回了已脱敏的错误。") ||
            "重试失败。",
          true
        );
        if (button) {
          button.disabled = false;
          button.textContent = originalButtonText || "重试分析";
        }
      }
    });
  });

  if (shouldPollAnalysis()) scheduleAnalysisPoll(true);
})();
