/* 原生窗口与浏览器备用入口共用页面；桌面桥只负责窗口和外部链接。 */
function markDesktopHost() {
  if (new URLSearchParams(window.location.search).get("desktop") === "1"
      || (window.pywebview && window.pywebview.api)) {
    document.body.classList.add("desktop-host");
  }
}

window.addEventListener("pywebviewready", markDesktopHost);
markDesktopHost();

function openExternal(url) {
  if (!/^https?:\/\//i.test(String(url || ""))) return;
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.open_external(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

document.addEventListener("click", function (event) {
  const control = event.target.closest("[data-window-action]");
  if (control) {
    const api = window.pywebview && window.pywebview.api;
    const action = control.dataset.windowAction === "maximize" ? "toggle_maximize" : control.dataset.windowAction;
    if (api && ["minimize", "toggle_maximize", "hide"].includes(action)) api[action]();
    return;
  }
  const link = event.target.closest('a[target="_blank"]');
  if (link && window.pywebview && window.pywebview.api) {
    event.preventDefault();
    openExternal(link.href);
  }
});
