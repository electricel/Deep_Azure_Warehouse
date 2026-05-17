(function () {
  const root = document.querySelector("[data-display-tuner]");
  if (!root) return;

  function syncInput(input) {
    const cssVar = input.dataset.cssVar;
    const unit = input.dataset.unit || "";
    const value = `${input.value}${unit}`;
    if (cssVar) {
      document.documentElement.style.setProperty(cssVar, value);
    }
    const output = root.querySelector(`[data-output-for="${input.name}"]`);
    if (output) {
      output.textContent = value;
    }
  }

  root.querySelectorAll("input[data-css-var]").forEach((input) => {
    syncInput(input);
    input.addEventListener("input", () => syncInput(input));
  });
})();
