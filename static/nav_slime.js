(function () {
  const nav = document.querySelector(".top-nav");
  if (!nav) return;
  const items = Array.from(nav.querySelectorAll(".nav-item"));
  if (!items.length) return;

  let current = items.find((item) => item.classList.contains("active")) || items[0];
  let raf = 0;

  function moveTo(item, instant) {
    if (!item) return;
    const navRect = nav.getBoundingClientRect();
    const rect = item.getBoundingClientRect();
    nav.style.setProperty("--slime-x", `${rect.left - navRect.left}px`);
    nav.style.setProperty("--slime-y", `${rect.top - navRect.top}px`);
    nav.style.setProperty("--slime-w", `${rect.width}px`);
    nav.style.setProperty("--slime-h", `${rect.height}px`);
    nav.classList.toggle("slime-instant", Boolean(instant));
    nav.classList.add("slime-ready");
  }

  function schedule(item, instant) {
    window.cancelAnimationFrame(raf);
    raf = window.requestAnimationFrame(() => moveTo(item, instant));
  }

  items.forEach((item) => {
    item.addEventListener("mouseenter", () => schedule(item, false));
    item.addEventListener("focus", () => schedule(item, false));
  });
  nav.addEventListener("mouseleave", () => schedule(current, false));
  window.addEventListener("resize", () => schedule(current, true));
  window.addEventListener("load", () => schedule(current, true));
  schedule(current, true);
})();
