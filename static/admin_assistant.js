(function () {
  const endpoint = document.getElementById("assistant-endpoint");
  const model = document.getElementById("assistant-model");
  const key = document.getElementById("assistant-api-key");
  const options = document.getElementById("assistant-model-options");
  const fetchBtn = document.getElementById("assistant-fetch-models");
  const testBtn = document.getElementById("assistant-test-api");
  const status = document.getElementById("assistant-config-status");
  if (!endpoint || !model || !fetchBtn || !testBtn || !status) return;

  function setStatus(text, mode) {
    status.textContent = text;
    status.classList.toggle("error", mode === "error");
  }

  function keyValue() {
    const value = (key && key.value || "").trim();
    return value === "********" ? "" : value;
  }

  function normalizeEndpointText() {
    const value = endpoint.value.trim().replace(/\/+$/, "");
    if (value.endsWith("/chat/completions")) {
      endpoint.value = value.slice(0, -"/chat/completions".length);
      setStatus("已自动转换为基础 API 地址，聊天接口会由后端追加 /chat/completions。");
    }
  }

  async function fetchModels() {
    normalizeEndpointText();
    fetchBtn.disabled = true;
    setStatus("正在读取模型列表...");
    try {
      const url = `/api/assistant/models?endpoint=${encodeURIComponent(endpoint.value.trim())}`;
      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || "模型列表读取失败");
      options.innerHTML = "";
      data.models.forEach((item) => {
        const opt = document.createElement("option");
        opt.value = item.id;
        opt.label = item.name || item.id;
        options.appendChild(opt);
      });
      const current = data.models.find((item) => item.id === model.value);
      if (!model.value && data.models[0]) model.value = data.models[0].id;
      setStatus(`已获取 ${data.count} 个模型。Chat: ${data.chat_url}${current ? `；当前模型：${current.name}` : ""}`);
    } catch (err) {
      setStatus(`模型列表获取失败：${err.message}`, "error");
    } finally {
      fetchBtn.disabled = false;
    }
  }

  async function testApi() {
    normalizeEndpointText();
    testBtn.disabled = true;
    setStatus("正在检测 API 连通性...");
    try {
      const res = await fetch("/api/assistant/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint: endpoint.value.trim(),
          model: model.value.trim(),
          api_key: keyValue(),
        }),
      });
      const data = await res.json();
      if (!res.ok || data.error || data.ok === false) throw new Error(data.error || "检测失败");
      setStatus(`检测成功。模型 ${data.model} 可用，聊天接口：${data.chat_url}`);
    } catch (err) {
      setStatus(`检测失败：${err.message}`, "error");
    } finally {
      testBtn.disabled = false;
    }
  }

  endpoint.addEventListener("change", normalizeEndpointText);
  fetchBtn.addEventListener("click", fetchModels);
  testBtn.addEventListener("click", testApi);
  if (endpoint.value.trim()) {
    window.setTimeout(fetchModels, 250);
  }
})();
