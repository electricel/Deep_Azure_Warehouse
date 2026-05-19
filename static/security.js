(function () {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const token = meta ? meta.getAttribute("content") || "" : "";

  if (token) {
    document.querySelectorAll('form[method="post" i]').forEach((form) => {
      if (form.querySelector('input[name="csrf_token"]')) return;
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = token;
      form.prepend(input);
    });
  }

  const originalFetch = window.fetch;
  if (!originalFetch || !token) return;

  window.fetch = function secureFetch(input, init) {
    const nextInit = init ? Object.assign({}, init) : {};
    const method = String(
      nextInit.method || (input && input.method) || "GET"
    ).toUpperCase();
    if (!["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
      return originalFetch(input, init);
    }
    const headers = new Headers(nextInit.headers || (input && input.headers) || {});
    if (!headers.has("X-CSRF-Token")) {
      headers.set("X-CSRF-Token", token);
    }
    nextInit.headers = headers;
    if (!nextInit.credentials) {
      nextInit.credentials = "same-origin";
    }
    return originalFetch(input, nextInit);
  };
})();
