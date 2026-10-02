"use strict";

(function (root) {
  const storageKey = "admin_password";

  function readPassword(storage = root.sessionStorage) {
    return storage.getItem(storageKey);
  }

  function storePassword(password, storage = root.sessionStorage) {
    storage.setItem(storageKey, password);
  }

  function clearPassword(storage = root.sessionStorage) {
    storage.removeItem(storageKey);
  }

  async function fetchWithPassword(fetcher, path, options = {}, settings = {}) {
    const storage = settings.storage || root.sessionStorage;
    const password = settings.password === undefined
      ? readPassword(storage)
      : settings.password;
    const headers = { ...options.headers };
    if (password !== null && password !== undefined) {
      headers["X-Admin-Password"] = password;
    }
    const response = await fetcher(path, { ...options, headers });
    if (response.status === 401) {
      clearPassword(storage);
      if (settings.onUnauthorized) settings.onUnauthorized();
    }
    return response;
  }

  const api = { readPassword, storePassword, clearPassword, fetchWithPassword };
  root.MotivationOSAdminAuth = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);
