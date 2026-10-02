"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const form = byId("auth");
  const error = byId("error");
  const content = byId("content");
  const video = byId("video");
  let reviewToken = "";
  let productionId = "";

  function showError(message) {
    error.textContent = message;
  }

  function clearVideo() {
    if (video.dataset.objectUrl) URL.revokeObjectURL(video.dataset.objectUrl);
    delete video.dataset.objectUrl;
    video.removeAttribute("src");
    video.load();
  }

  function showAuth(message = "") {
    reviewToken = "";
    byId("token").value = "";
    form.hidden = false;
    content.hidden = true;
    byId("signout").hidden = true;
    byId("refresh-review").disabled = true;
    clearVideo();
    showError(message);
  }

  async function request(path, options = {}) {
    const { responseType, ...fetchOptions } = options;
    let response;
    try {
      response = await fetch(path, {
        ...fetchOptions,
        headers: {
          Authorization: `Bearer ${reviewToken}`,
          ...(fetchOptions.body ? { "Content-Type": "application/json" } : {}),
          ...fetchOptions.headers
        }
      });
    } catch {
      throw new Error("Could not reach the local API. Check that the server is running and try again.");
    }
    if (response.ok && responseType === "blob") return response;
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (response.status === 401 || response.status === 403) {
      const authError = new Error("Admin authorization failed. Sign in again to continue.");
      authError.authFailure = true;
      throw authError;
    }
    if (response.status === 503) {
      throw new Error("The administrative API is not configured. Set its admin token and restart the local server.");
    }
    if (!response.ok) {
      const detail = payload && typeof payload.detail === "string" ? payload.detail : "The request could not be completed.";
      if (response.status === 404) throw new Error("This production or its preview was not found. Refresh and try again.");
      if (response.status === 409) throw new Error(detail);
      if (response.status >= 500) throw new Error("The local API encountered an unexpected error. Try again or refresh the page.");
      throw new Error(detail);
    }
    return response.status === 204 ? null : payload;
  }

  function renderFindings(list, values) {
    list.replaceChildren();
    if (!Array.isArray(values) || values.length === 0) {
      const item = document.createElement("li");
      item.textContent = "No audio findings.";
      list.append(item);
      return;
    }
    for (const finding of values) {
      const item = document.createElement("li");
      item.textContent = String(finding);
      list.append(item);
    }
  }

  function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? "Date unavailable"
      : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
  }

  function renderDecisionMetadata(decision) {
    const list = byId("audio-decision-metadata");
    list.replaceChildren();
    if (!decision || typeof decision !== "object") {
      const empty = document.createElement("dd");
      empty.textContent = "No audio decision has been recorded.";
      list.append(empty);
      return;
    }
    for (const [label, value] of [
      ["Decision", decision.decision],
      ["Reviewer", decision.reviewer],
      ["Recorded", decision.reviewed_at]
    ]) {
      if (typeof value !== "string" || !value) continue;
      const term = document.createElement("dt");
      const detail = document.createElement("dd");
      term.textContent = label;
      detail.textContent = label === "Recorded" ? formatDate(value) : value;
      list.append(term, detail);
    }
  }

  function renderReview(data) {
    const audioQa = data.audio_qa && typeof data.audio_qa === "object" ? data.audio_qa : {};
    const audioStatus = typeof audioQa.technical_status === "string" ? audioQa.technical_status : "not run";
    const decision = data.audio_review_decision && typeof data.audio_review_decision === "object"
      ? data.audio_review_decision
      : null;
    const audioReviewRequired = data.status === "needs_review" && audioStatus === "needs_review" && !decision;

    byId("title").textContent = data.title || "Untitled production";
    byId("script").textContent = data.script?.narration || "Script unavailable";
    byId("description").textContent = data.description || "Description unavailable";
    byId("status").textContent = data.status || "Status unavailable";
    const manualUploadSection = byId("manual-upload-link");
    manualUploadSection.hidden = !["ready_for_manual_upload", "completed", "published"].includes(data.status);
    byId("youtube-url").value = data.youtube_url || "";
    byId("manual-upload-status").textContent = data.status === "published"
      ? "YouTube video linked."
      : "";
    byId("audio-status").textContent = audioStatus;
    byId("audio").textContent = JSON.stringify(audioQa, null, 2);
    byId("videoqa").textContent = JSON.stringify(data.video_qa || { technical_status: "not run" }, null, 2);
    byId("findings").textContent = Array.isArray(data.findings) && data.findings.length
      ? data.findings.map(String).join("\n")
      : "No validation findings.";
    renderFindings(byId("audio-findings"), audioQa.findings);
    renderDecisionMetadata(decision);
    byId("audio-review-required").textContent = audioReviewRequired
      ? "A separate human audio decision is required."
      : decision
        ? "A human audio decision has been recorded."
        : audioStatus === "failed"
          ? "Audio QA failed. Audio approval is unavailable."
          : "A separate human audio decision is not required.";
    byId("audio-approve").hidden = !audioReviewRequired;
    byId("audio-reject").hidden = !audioReviewRequired;
  }

  async function loadReview() {
    if (!reviewToken || !productionId) return;
    showError("");
    try {
      const data = await request(`/api/productions/${encodeURIComponent(productionId)}/review`);
      renderReview(data);
      content.hidden = false;
      try {
        const media = await request(data.video_url, { responseType: "blob" });
        if (video.dataset.objectUrl) URL.revokeObjectURL(video.dataset.objectUrl);
        video.dataset.objectUrl = URL.createObjectURL(await media.blob());
        video.src = video.dataset.objectUrl;
      } catch (cause) {
        if (cause.authFailure) throw cause;
        clearVideo();
        showError(cause instanceof Error ? cause.message : "Final video preview is unavailable.");
      }
    } catch (cause) {
      if (cause.authFailure) showAuth(cause.message);
      else showError(cause instanceof Error ? cause.message : "Review request failed.");
    }
  }

  async function recordDecision(kind, decision) {
    const reviewer = byId("reviewer").value.trim();
    if (!reviewer) {
      showError("Enter a reviewer name.");
      byId("reviewer").focus();
      return;
    }
    const isAudio = kind === "audio-review";
    const confirmation = isAudio
      ? `Confirm ${decision} for audio on production ${productionId}? This does not approve production or publish.`
      : `Confirm ${decision} for production ${productionId}?`;
    if (!window.confirm(confirmation)) return;
    try {
      await request(`/api/productions/${encodeURIComponent(productionId)}/${kind}/${decision}`, {
        method: "POST",
        body: JSON.stringify({ confirm_review: true, reviewer })
      });
      await loadReview();
    } catch (cause) {
      if (cause.authFailure) showAuth(cause.message);
      else showError(cause instanceof Error ? cause.message : "Review decision could not be recorded.");
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const tokenInput = byId("token");
    reviewToken = tokenInput.value.trim();
    productionId = byId("id").value.trim();
    tokenInput.value = "";
    form.hidden = true;
    byId("signout").hidden = false;
    byId("refresh-review").disabled = false;
    await loadReview();
  });

  byId("refresh-review").addEventListener("click", loadReview);
  byId("signout").addEventListener("click", () => showAuth());
  byId("audio-approve").addEventListener("click", () => recordDecision("audio-review", "approve"));
  byId("audio-reject").addEventListener("click", () => recordDecision("audio-review", "reject"));
  byId("approve").addEventListener("click", () => recordDecision("review", "approve"));
  byId("reject").addEventListener("click", () => recordDecision("review", "reject"));
  byId("manual-upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const saveButton = byId("save-youtube-url");
    saveButton.disabled = true;
    try {
      const result = await request(`/api/productions/${encodeURIComponent(productionId)}/youtube-link`, {
        method: "POST",
        body: JSON.stringify({ url: byId("youtube-url").value.trim() })
      });
      byId("manual-upload-status").textContent = `Linked ${result.video_url}`;
      await loadReview();
    } catch (cause) {
      if (cause.authFailure) showAuth(cause.message);
      else showError(cause instanceof Error ? cause.message : "YouTube URL could not be saved.");
    } finally {
      saveButton.disabled = false;
    }
  });
})();
