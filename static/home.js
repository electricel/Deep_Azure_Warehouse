(function () {
  const img = document.getElementById("promo-image");
  const section = document.querySelector(".promo-scroll-section");
  if (!img || !section) return;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function update() {
    const rect = section.getBoundingClientRect();
    const total = Math.max(1, rect.height - window.innerHeight);
    const progress = clamp(-rect.top / total, 0, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    img.style.opacity = String(0.1 + eased * 0.9);
    img.style.transform = `scale(${1.08 - eased * 0.08}) translateY(${(1 - eased) * 18}px)`;
    section.style.setProperty("--reveal", eased.toFixed(3));
  }

  window.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);
  update();
})();

(function () {
  const section = document.getElementById("product-scroll");
  const track = document.getElementById("product-track");
  const viewport = track && track.parentElement;
  const birth = section && section.querySelector(".product-birth-stage");
  if (!section || !track || !viewport) return;
  let ticking = false;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function update() {
    ticking = false;
    const rect = section.getBoundingClientRect();
    const scrollable = Math.max(1, rect.height - window.innerHeight);
    const progress = clamp(-rect.top / scrollable, 0, 1);
    const overflow = Math.max(0, track.scrollWidth - viewport.clientWidth);
    const eased = progress < 0.5
      ? 4 * progress * progress * progress
      : 1 - Math.pow(-2 * progress + 2, 3) / 2;
    track.style.setProperty("--product-track-x", `${-overflow * eased}px`);
    section.style.setProperty("--product-flow", eased.toFixed(3));
    const birthProgress = clamp(progress / 0.42, 0, 1);
    const cardProgress = clamp((progress - 0.28) / 0.58, 0, 1);
    section.style.setProperty("--product-birth", birthProgress.toFixed(3));
    section.style.setProperty("--product-cards", cardProgress.toFixed(3));
    track.style.opacity = String(cardProgress);
    track.style.transform = `translate3d(${-overflow * eased}px, ${(1 - cardProgress) * 34}px, 0) rotateX(${(1 - cardProgress) * 9}deg) scale(${0.92 + cardProgress * 0.08})`;
    if (birth) {
      birth.style.opacity = String(1 - Math.max(0, (progress - 0.58) / 0.24));
      birth.style.transform = `translateY(${(1 - birthProgress) * 72}px) scale(${0.72 + birthProgress * 0.28})`;
    }
  }

  function requestUpdate() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(update);
  }

  window.addEventListener("scroll", requestUpdate, { passive: true });
  window.addEventListener("resize", update);
  update();
})();
