(function () {
  const forms = document.querySelectorAll("[data-inventory-audit-form]");
  if (!forms.length) return;

  function setStatus(form, text, mode) {
    const node = form.querySelector(".inventory-audit-status");
    if (!node) return;
    node.textContent = text || "";
    node.classList.toggle("is-ok", mode === "ok");
    node.classList.toggle("is-error", mode === "error");
  }

  function cleanError(error, fallback) {
    const text = error && error.message ? String(error.message).trim() : "";
    if (!text) return fallback || "操作失败。";
    return text.length > 180 ? `${text.slice(0, 180)}...` : text;
  }

  function parseQuantity(value) {
    const text = String(value || "").trim();
    if (!/^\d+$/.test(text)) return null;
    const parsed = Number.parseInt(text, 10);
    return Number.isSafeInteger(parsed) ? parsed : null;
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function formPayload(form) {
    const quantity = parseQuantity(form.elements.quantity ? form.elements.quantity.value : "");
    if (quantity === null) throw new Error("数量必须是非负整数。");
    const payload = {
      category: form.elements.category ? form.elements.category.value.trim() : "",
      name: form.elements.name ? form.elements.name.value.trim() : "",
      quantity,
      location: form.elements.location ? form.elements.location.value.trim() : "",
      note: form.elements.note ? form.elements.note.value.trim() : "",
      reason: form.elements.reason ? form.elements.reason.value.trim() : "",
    };
    if (!payload.category || !payload.name || !payload.location) {
      throw new Error("类别、名称、仓位都必须填写。");
    }
    if (!payload.reason) {
      throw new Error("请填写修正原因。");
    }
    return payload;
  }

  forms.forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      let payload;
      try {
        payload = formPayload(form);
      } catch (error) {
        setStatus(form, cleanError(error, "请检查输入。"), "error");
        return;
      }
      const button = form.querySelector('button[type="submit"]');
      if (button) button.disabled = true;
      setStatus(form, "正在保存修正...", "");
      try {
        const data = await postJson(form.action, payload);
        setStatus(form, data.message || "已保存，刷新页面可重新体检。", "ok");
        const row = form.closest("[data-inventory-audit-row]");
        if (row) row.classList.add("is-updated");
        if (form.elements.reason) form.elements.reason.value = "";
      } catch (error) {
        setStatus(form, cleanError(error, "保存失败。"), "error");
      } finally {
        if (button) button.disabled = false;
      }
    });

    const deleteButton = form.querySelector("[data-inventory-audit-delete]");
    if (!deleteButton) return;
    deleteButton.addEventListener("click", async () => {
      const reason = form.elements.reason ? form.elements.reason.value.trim() : "";
      if (!reason) {
        setStatus(form, "删除前必须填写原因。", "error");
        return;
      }
      const row = form.closest("[data-inventory-audit-row]");
      const inventoryId = row ? row.dataset.inventoryId || "" : "";
      if (!window.confirm(`确认删除库存记录 #${inventoryId} 吗？此操作会写入台账。`)) return;
      deleteButton.disabled = true;
      setStatus(form, "正在删除记录...", "");
      try {
        const data = await postJson(deleteButton.dataset.deleteUrl || "", { reason });
        setStatus(form, data.message || "记录已删除。", "ok");
        if (row) {
          row.classList.add("is-deleted");
          window.setTimeout(() => row.remove(), 450);
        }
      } catch (error) {
        setStatus(form, cleanError(error, "删除失败。"), "error");
      } finally {
        deleteButton.disabled = false;
      }
    });
  });
})();
