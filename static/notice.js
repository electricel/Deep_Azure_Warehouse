(function () {
  const notice = document.querySelector("[data-inventory-release-notice]");
  if (!notice) return;
  const version = notice.dataset.version || "empty";
  const versionStorageKey = "warehouse.inventory.releaseNoticeSeen";
  const todayStorageKey = "warehouse.inventory.releaseNoticeToday";
  const today = new Date().toISOString().slice(0, 10);
  let seenVersion = "";
  let mutedToday = "";
  try {
    seenVersion = localStorage.getItem(versionStorageKey) || "";
    mutedToday = localStorage.getItem(todayStorageKey) || "";
  } catch (_) {
    seenVersion = "";
    mutedToday = "";
  }
  if (seenVersion === version || mutedToday === `${version}:${today}`) {
    notice.remove();
    return;
  }
  notice.classList.add("is-visible");
  document.body.classList.add("has-inventory-release-notice");

  function closeNotice(mode) {
    try {
      if (mode === "today") {
        localStorage.setItem(todayStorageKey, `${version}:${today}`);
      } else {
        localStorage.setItem(versionStorageKey, version);
      }
    } catch (_) {
      // Ignore private-mode storage failures; the close action should still work.
    }
    notice.classList.remove("is-visible");
    document.body.classList.remove("has-inventory-release-notice");
    window.setTimeout(() => notice.remove(), 180);
  }

  notice.querySelectorAll("[data-inventory-notice-close]").forEach((button) => {
    button.addEventListener("click", () => closeNotice(button.dataset.closeMode || "version"));
  });
  notice.addEventListener("click", (event) => {
    if (event.target === notice) closeNotice("today");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && notice.isConnected) closeNotice("today");
  });
})();
