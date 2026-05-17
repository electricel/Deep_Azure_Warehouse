(function () {
  const forms = Array.from(document.querySelectorAll(".pcb-attach-form"));
  const analysisForms = Array.from(document.querySelectorAll(".pcb-analysis-form"));
  if (!forms.length && !analysisForms.length) return;
  const status = document.querySelector("[data-attachment-status], [data-soldering-status]");

  function setStatus(text, isError) {
    if (!status) return;
    status.textContent = text;
    status.classList.toggle("error", Boolean(isError));
  }

  forms.forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const fileInput = form.querySelector('input[type="file"]');
      if (!fileInput || !fileInput.files.length) {
        setStatus("请选择 PCB、Gerber、HTML 或 PDF 文件。", true);
        return;
      }
      setStatus("正在关联 PCB/Gerber/HTML/PDF 文件...");
      try {
        const res = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        window.location.reload();
      } catch (err) {
        setStatus(err.message || "PCB/Gerber/HTML/PDF 文件关联失败。", true);
      }
    });
  });

  function analysisStatusText(data) {
    const file = data && data.pcb_file ? data.pcb_file : {};
    if (file.status_text) return file.status_text;
    const job = data && data.job ? data.job : {};
    if (!job.status) return "分析状态不可用。";
    const progress = Number.isFinite(Number(job.progress)) ? ` ${Number(job.progress)}%` : "";
    const labels = {
      pending: "待分析",
      queued: "排队中",
      running: "分析中",
      completed: "已完成",
      failed: "失败",
    };
    return `分析 ${labels[String(job.status).toLowerCase()] || job.status}${progress}`;
  }

  analysisForms.forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button");
      const item = form.closest("li");
      const inlineStatus = item ? item.querySelector("[data-analysis-status]") : null;
      if (button) button.disabled = true;
      setStatus("正在提交 PDF 原理图分析...");
      try {
        const res = await fetch(form.action, { method: "POST", headers: { Accept: "application/json" } });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        const text = analysisStatusText(data);
        if (inlineStatus) inlineStatus.textContent = text;
        const job = data.job || {};
        if (button) {
          button.textContent = job.status === "running" ? "分析中" : "已排队";
          button.disabled = job.status === "queued" || job.status === "running";
        }
        setStatus(text);
      } catch (err) {
        if (button) button.disabled = false;
        setStatus(err.message || "PDF 分析排队失败。", true);
      }
    });
  });
})();
