/* 桌面页面通过原生桥调用本机管理逻辑；这里处理窗口偏好和外部链接。 */
function markDesktopHost() {
  if (new URLSearchParams(window.location.search).get("desktop") === "1"
      || (window.pywebview && window.pywebview.api)) {
    document.body.classList.add("desktop-host");
  }
}

function syncWindowTheme() {
  const api = window.pywebview && window.pywebview.api;
  if (api && api.set_window_theme) {
    api.set_window_theme(document.body.classList.contains("theme-light") ? "light" : "dark").catch(function () {});
  }
}
window.addEventListener("pywebviewready", function () {
  markDesktopHost();
  syncWindowTheme();
});
new MutationObserver(syncWindowTheme).observe(document.body, { attributes: true, attributeFilter: ["class"] });
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
  const link = event.target.closest('a[target="_blank"]');
  if (link && window.pywebview && window.pywebview.api) {
    event.preventDefault();
    openExternal(link.href);
  }
});

// The native close handler reads this persisted preference immediately.
let savedCloseBehavior = "tray";
async function loadDesktopPreferences() {
  const select = document.getElementById("closeBehavior");
  const api = window.pywebview && window.pywebview.api;
  if (!select || !api || !api.get_desktop_preferences) return;
  try {
    const preferences = await api.get_desktop_preferences();
    savedCloseBehavior = preferences.close_behavior;
    select.value = savedCloseBehavior;
    select.disabled = false;
    select.removeAttribute("title");
  } catch (error) {
    select.title = "无法读取桌面偏好，请重新打开桌面端";
  }
}
window.addEventListener("pywebviewready", loadDesktopPreferences);
loadDesktopPreferences();
document.getElementById("closeBehavior").addEventListener("change", async function () {
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api.set_close_behavior) return;
  const select = this;
  select.disabled = true;
  try {
    const result = await api.set_close_behavior(select.value);
    if (!result.ok) throw new Error(result.msg || "无法保存关闭窗口偏好");
    savedCloseBehavior = result.close_behavior;
    select.value = savedCloseBehavior;
  } catch (error) {
    select.value = savedCloseBehavior;
    showAlert("保存失败", error.message || "无法保存关闭窗口偏好");
  } finally {
    select.disabled = false;
  }
});
