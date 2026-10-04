/* Route desktop management requests through the Python bridge, without HTTP. */
(function () {
  const isDesktopHost = window.location.protocol === "file:"
    || (window.pywebview && window.pywebview.api);
  if (!isDesktopHost) return;
  const originalFetch = window.fetch.bind(window);
  let bridgeReady;
  let capabilityReady;
  function encode(bytes) {
    let value = "";
    for (let offset = 0; offset < bytes.length; offset += 16384) {
      value += String.fromCharCode.apply(null, bytes.subarray(offset, offset + 16384));
    }
    return btoa(value);
  }
  function decode(value) {
    const binary = atob(value || "");
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }
  function desktopApi() {
    if (window.pywebview && window.pywebview.api) return Promise.resolve(window.pywebview.api);
    if (!bridgeReady) {
      bridgeReady = new Promise(function (resolve, reject) {
        const timeout = setTimeout(function () {
          window.removeEventListener("pywebviewready", onReady);
          reject(new Error("桌面管理功能未就绪，请重新打开 WRelay"));
        }, 8000);
        function onReady() {
          clearTimeout(timeout);
          if (window.pywebview && window.pywebview.api) resolve(window.pywebview.api);
          else reject(new Error("桌面管理功能未就绪，请重新打开 WRelay"));
        }
        window.addEventListener("pywebviewready", onReady, {once: true});
      });
    }
    return bridgeReady;
  }
  function bridgeCapability(api) {
    if (!capabilityReady) {
      if (typeof api.get_bridge_capability !== "function") {
        return Promise.reject(new Error("桌面管理凭证未就绪，请重新打开 WRelay"));
      }
      capabilityReady = Promise.resolve(api.get_bridge_capability());
    }
    return capabilityReady;
  }
  window.fetch = async function (resource, options) {
    const path = String(resource);
    if (!path.startsWith("/api/")) return originalFetch(resource, options);
    const request = options || {};
    const headers = Object.fromEntries(new Headers(request.headers || {}).entries());
    const body = request.body == null ? "" : encode(new Uint8Array(await new Response(request.body).arrayBuffer()));
    const api = await desktopApi();
    const capability = await bridgeCapability(api);
    const result = await api.request_api((request.method || "GET").toUpperCase(), path, headers, body, capability);
    return new Response(decode(result.body), {status: result.status, headers: result.headers});
  };
})();
