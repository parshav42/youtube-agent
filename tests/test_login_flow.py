"""Tests for tab-scoped admin password handling in the review dashboards."""

import subprocess
from pathlib import Path


def test_dashboard_pages_use_password_dialog_and_tab_storage() -> None:
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    for page in ("review", "production-review"):
        html = (static / f"{page}.html").read_text(encoding="utf-8")
        script = (static / f"{page}.js").read_text(encoding="utf-8")
        assert 'id="password-dialog"' in html
        assert 'id="password-input" type="password"' in html
        assert "admin-auth.js" in html
        assert "X-Admin-Password" not in script
        assert "localStorage" not in script
        assert "fetchWithPassword" in script
    auth_script = (static / "admin-auth.js").read_text(encoding="utf-8")
    assert '"admin_password"' in auth_script
    assert "sessionStorage" in auth_script
    assert "X-Admin-Password" in auth_script
    assert "motivationos:signout" in (
        static / "review.js"
    ).read_text(encoding="utf-8")


def test_session_storage_and_unauthorized_response_prompt_again() -> None:
    project_root = Path(__file__).resolve().parents[1]
    auth_script = project_root / "app" / "static" / "admin-auth.js"
    node_test = r"""
const assert = require("node:assert/strict");
const auth = require(process.argv[1]);
const values = new Map();
const storage = {
  getItem: (key) => values.has(key) ? values.get(key) : null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key)
};

(async () => {
  assert.equal(auth.readPassword(storage), null);
  auth.storePassword("correct-horse", storage);
  assert.equal(auth.readPassword(storage), "correct-horse");

  let promptCount = 0;
  let sentHeader = null;
  const response = await auth.fetchWithPassword(
    async (_path, options) => {
      sentHeader = options.headers["X-Admin-Password"];
      return { status: 401 };
    },
    "/api/auth/validate",
    {},
    {
      storage,
      onUnauthorized: () => { promptCount += 1; }
    }
  );
  assert.equal(response.status, 401);
  assert.equal(sentHeader, "correct-horse");
  assert.equal(auth.readPassword(storage), null);
  assert.equal(promptCount, 1);
  auth.clearPassword(storage);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    subprocess.run(
        ["node", "-e", node_test, str(auth_script)],
        check=True,
        capture_output=True,
        text=True,
    )
