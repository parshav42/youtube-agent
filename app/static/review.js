"use strict";

(() => {
  const elements = Object.fromEntries([
    "authView", "authForm", "reviewerInput", "dashboardView", "refreshButton",
    "signoutButton", "queueCount", "queueLoading", "queueList", "emptyQueue", "queueError",
    "detailPlaceholder", "detailLoading", "detailError", "scriptDetail", "notice", "decisionDialog",
    "dialogTitle", "dialogMessage", "dialogConfirm"
  ].map((name) => [name, document.getElementById(name.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`))]));

  const auth = window.MotivationOSAdminAuth;
  const passwordDialog = document.getElementById("password-dialog");
  const passwordForm = document.getElementById("password-form");
  const passwordInput = document.getElementById("password-input");
  const passwordError = document.getElementById("password-error");
  const labels = {
    language_consistency: "Language consistency",
    story_coherence: "Story coherence",
    originality_safeguards: "Originality safeguards",
    duration_feasibility: "Duration feasibility",
    repetition: "Repetition check",
    motivational_message: "Motivational message",
    scene_completeness: "Scene completeness",
    caption_scene_alignment: "Caption and scene alignment",
    structural_labels_absent: "Structural labels absent",
    unsupported_factual_assertions: "Unsupported factual assertions",
    unsupported_guarantees: "Unsupported guarantees"
  };
  let adminPassword = auth.readPassword() || "";
  let reviewer = "";
  let queue = [];
  let selectedId = null;
  let pendingDecision = null;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function addText(parent, tag, className, text) {
    const node = element(tag, className, text);
    parent.append(node);
    return node;
  }

  function statusLabel(record) {
    if (record.status === "completed" && !record.reviewed_at && !record.reviewed_by) {
      return "Legacy — Requires Fresh Review";
    }
    return ({ needs_review: "Needs Review", approved: "Approved", rejected: "Rejected" })[record.status] || "Unknown status";
  }

  function evaluationLabel(evaluation) {
    if (!evaluation || typeof evaluation !== "object") return "Not evaluated";
    if (evaluation.passed === true) return "Passed";
    if (evaluation.passed === false) return "Needs attention";
    return "Evaluation incomplete";
  }

  function showNotice(message, kind = "success") {
    elements.notice.textContent = message;
    elements.notice.dataset.kind = kind;
    elements.notice.hidden = false;
  }

  function hideNotice() {
    elements.notice.hidden = true;
    elements.notice.textContent = "";
  }

  function promptForPassword(message = "") {
    passwordError.textContent = message;
    if (!passwordDialog.open) passwordDialog.showModal();
    passwordInput.focus();
  }

  function showAuth(message = "") {
    auth.clearPassword();
    adminPassword = "";
    reviewer = "";
    elements.authView.hidden = false;
    elements.dashboardView.hidden = true;
    elements.refreshButton.disabled = true;
    elements.reviewerInput.value = "";
    promptForPassword(message);
  }

  async function request(path, options = {}, password = adminPassword) {
    let response;
    try {
      response = await auth.fetchWithPassword(fetch, path, {
        ...options,
        headers: {
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...options.headers
        }
      }, {
        password,
        onUnauthorized: () => showAuth("Your password was not accepted. Sign in again.")
      });
    } catch {
      throw new Error("Could not reach the local API. Check that the server is running and try again.");
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (response.status === 401 || response.status === 403) {
      const error = new Error("Your password was not accepted. Sign in again.");
      error.authFailure = true;
      throw error;
    }
    if (response.status === 503) {
      throw new Error("The administrative API is not configured correctly. Check ADMIN_PASSWORD and restart the local server.");
    }
    if (!response.ok) {
      const detail = payload && typeof payload.detail === "string" ? payload.detail : "The request could not be completed.";
      if (response.status === 404) throw new Error("This script was not found. Refresh the queue and select another script.");
      if (response.status === 409) throw new Error(detail);
      if (response.status === 422) throw new Error("The script ID or request was invalid. Refresh the queue and try again.");
      if (response.status >= 500) throw new Error("The local API encountered an unexpected error. Try again or refresh the page.");
      throw new Error(detail);
    }
    return payload;
  }

  function renderQueue() {
    elements.queueList.replaceChildren();
    elements.queueCount.textContent = `${queue.length} ${queue.length === 1 ? "script" : "scripts"}`;
    elements.emptyQueue.hidden = queue.length !== 0;
    for (const record of queue) {
      const script = record.script && typeof record.script === "object" ? record.script : {};
      const title = Array.isArray(script.title_candidates) && script.title_candidates[0]
        ? script.title_candidates[0]
        : record.topic || `Script ${record.id}`;
      const button = element("button", "queue-item");
      button.type = "button";
      button.setAttribute("aria-current", String(record.id === selectedId));
      button.setAttribute("aria-label", `Open script ${record.id}: ${title}`);
      const top = element("span", "queue-item-top");
      addText(top, "span", "queue-item-title", title);
      addText(top, "span", `status-pill ${record.status === "completed" ? "status-legacy" : `status-${record.status}`}`, statusLabel(record));
      button.append(top);
      const meta = element("span", "queue-item-meta");
      const topic = record.topic || script.topic;
      if (topic) addText(meta, "span", "queue-topic", topic);
      addText(meta, "span", "queue-id", `ID ${record.id}`);
      button.append(meta);
      const footer = element("span", "queue-item-meta");
      addText(footer, "span", "evaluation-pill", evaluationLabel(record.evaluation));
      if (record.created_at) addText(footer, "time", "queue-date", formatDate(record.created_at));
      button.append(footer);
      button.addEventListener("click", () => loadDetail(record.id));
      elements.queueList.append(button);
    }
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Date unavailable";
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
  }

  function appendBlock(parent, title, className = "") {
    const block = element("section", `section-block ${className}`.trim());
    addText(block, "h3", "", title);
    parent.append(block);
    return block;
  }

  function appendList(parent, values) {
    if (!Array.isArray(values) || values.length === 0) return;
    const list = element("ul", "detail-list");
    for (const value of values) addText(list, "li", "", value);
    parent.append(list);
  }

  function renderEvaluation(parent, evaluation) {
    const block = appendBlock(parent, "Quality evaluation and validation");
    if (!evaluation || typeof evaluation !== "object") {
      addText(block, "p", "", "No quality evaluation was returned for this script.");
      return;
    }
    const summary = element("div", "evaluation-summary");
    const status = evaluationLabel(evaluation);
    addText(summary, "span", `evaluation-pill ${evaluation.passed === true ? "evaluation-passed" : evaluation.passed === false ? "evaluation-failed" : ""}`, status);
    if (typeof evaluation.quality_score === "number" && Number.isFinite(evaluation.quality_score)) {
      addText(summary, "span", "evaluation-score", `Quality score: ${evaluation.quality_score}`);
    }
    if (typeof evaluation.threshold === "number" && Number.isFinite(evaluation.threshold)) {
      addText(summary, "span", "evaluation-score", `Threshold: ${evaluation.threshold}`);
    }
    block.append(summary);

    const checks = Object.entries(labels).filter(([key]) => typeof evaluation[key] === "boolean");
    if (checks.length) {
      const grid = element("div", "check-grid");
      for (const [key, label] of checks) {
        const row = element("div", "check-item");
        addText(row, "span", "", label);
        addText(row, "span", `check-result ${evaluation[key] ? "check-true" : "check-false"}`, evaluation[key] ? "Pass" : "Flag");
        grid.append(row);
      }
      block.append(grid);
    }
    if (Array.isArray(evaluation.feedback) && evaluation.feedback.length) {
      addText(block, "h3", "feedback-heading", "Evaluation feedback");
      appendList(block, evaluation.feedback);
    }
  }

  function canApprove(evaluation) {
    return Boolean(evaluation && evaluation.passed === true
      && Object.keys(labels).every((key) => evaluation[key] === true));
  }

  function renderDetail(record) {
    const script = record.script && typeof record.script === "object" ? record.script : {};
    const article = element("div", "detail-content");
    const header = element("div", "detail-header");
    const heading = element("div", "detail-title-area");
    addText(heading, "p", "eyebrow", `Script ${record.script_id}`);
    const title = Array.isArray(script.title_candidates) && script.title_candidates[0]
      ? script.title_candidates[0]
      : record.topic || `Script ${record.script_id}`;
    addText(heading, "h2", "", title);
    const meta = element("div", "detail-meta");
    if (record.topic) addText(meta, "span", "", `Topic: ${record.topic}`);
    if (record.language || script.language) addText(meta, "span", "", `Language: ${record.language || script.language}`);
    if (script.target_duration_seconds !== undefined) addText(meta, "span", "", `${script.target_duration_seconds} sec`);
    if (record.created_at) addText(meta, "time", "", `Created ${formatDate(record.created_at)}`);
    heading.append(meta);
    header.append(heading);

    const actions = element("div", "detail-actions");
    const status = element("span", `status-pill ${record.status === "completed" ? "status-legacy" : `status-${record.status}`}`, statusLabel(record));
    actions.append(status);
    const buttons = element("div", "decision-actions");
    const reviewable = record.status === "needs_review" || (record.status === "completed" && !record.reviewed_at && !record.reviewed_by);
    const approve = element("button", "button button-approve", "Approve");
    approve.type = "button";
    approve.disabled = !reviewable || !canApprove(record.evaluation);
    approve.title = approve.disabled && reviewable ? "Approval requires a passing evaluation and all required checks." : "Approve script";
    approve.addEventListener("click", () => confirmDecision("approved", record.script_id));
    const reject = element("button", "button button-reject", "Reject");
    reject.type = "button";
    reject.disabled = !reviewable;
    reject.addEventListener("click", () => confirmDecision("rejected", record.script_id));
    buttons.append(approve, reject);
    actions.append(buttons);
    header.append(actions);
    article.append(header);

    const hook = appendBlock(article, "Opening hook", "hook-block");
    addText(hook, "p", "", script.opening_hook || "Not provided");
    const narration = appendBlock(article, "Complete narration");
    addText(narration, "p", "", script.narration || "Not provided");

    if (Array.isArray(script.scenes) && script.scenes.length) {
      const scenesBlock = appendBlock(article, "Scenes");
      const sceneList = element("div", "scene-list");
      for (const scene of script.scenes) {
        const card = element("section", "scene");
        const sceneTitle = `Scene ${scene.scene_number ?? ""}`;
        const sceneHeading = element("div", "scene-heading");
        addText(sceneHeading, "span", "", sceneTitle);
        if (scene.duration_seconds !== undefined) addText(sceneHeading, "span", "", `${scene.duration_seconds} sec`);
        card.append(sceneHeading);
        for (const [label, value] of [
          ["Narration", scene.narration], ["Visual", scene.visual_description], ["Caption", scene.caption],
          ["Transition", scene.transition], ["Audio", scene.audio_direction]
        ]) {
          if (value !== undefined && value !== null) {
            const row = element("p", "");
            addText(row, "span", "scene-label", `${label}:`);
            row.append(document.createTextNode(String(value)));
            card.append(row);
          }
        }
        sceneList.append(card);
      }
      scenesBlock.append(sceneList);
    }

    const beats = appendBlock(article, "Story beats");
    appendList(beats, script.story_beats);
    const closing = appendBlock(article, "Closing message / call to action");
    addText(closing, "p", "", script.closing_message || "Not provided");
    const description = appendBlock(article, "Description and hashtags");
    addText(description, "p", "", script.description || "Not provided");
    if (Array.isArray(script.hashtags) && script.hashtags.length) addText(description, "p", "", script.hashtags.join(" "));
    const titles = appendBlock(article, "Title candidates");
    appendList(titles, script.title_candidates);
    if (Array.isArray(script.revision_notes) && script.revision_notes.length) {
      const notes = appendBlock(article, "Script notes");
      appendList(notes, script.revision_notes);
    }
    renderEvaluation(article, record.evaluation);

    const review = appendBlock(article, "Review metadata");
    const reviewMeta = element("div", "review-metadata");
    if (record.reviewed_by) addText(reviewMeta, "span", "", `Reviewer: ${record.reviewed_by}`);
    if (record.reviewed_at) addText(reviewMeta, "time", "", `Reviewed ${formatDate(record.reviewed_at)}`);
    if (!reviewMeta.childNodes.length) addText(reviewMeta, "span", "", "No review decision has been recorded.");
    review.append(reviewMeta);
    elements.scriptDetail.replaceChildren(article);
  }

  async function refreshQueue(successMessage = "") {
    elements.queueLoading.hidden = false;
    elements.queueError.hidden = true;
    elements.refreshButton.disabled = true;
    try {
      const data = await request("/api/scripts/review");
      if (!Array.isArray(data)) throw new Error("The review queue response was not valid.");
      queue = data;
      if (!queue.some((record) => record.id === selectedId)) selectedId = null;
      renderQueue();
      if (selectedId === null) {
        elements.scriptDetail.hidden = true;
        elements.detailPlaceholder.hidden = false;
      }
      if (successMessage) showNotice(successMessage);
    } catch (error) {
      if (error.authFailure) {
        showAuth(error.message);
      } else {
        elements.queueError.textContent = error.message;
        elements.queueError.hidden = false;
        showNotice(error.message, "error");
      }
    } finally {
      elements.queueLoading.hidden = true;
      elements.refreshButton.disabled = !adminPassword;
    }
  }

  async function loadDetail(scriptId) {
    selectedId = scriptId;
    renderQueue();
    elements.detailPlaceholder.hidden = true;
    elements.scriptDetail.hidden = true;
    elements.detailError.hidden = true;
    elements.detailLoading.hidden = false;
    try {
      const record = await request(`/api/scripts/${encodeURIComponent(scriptId)}/review`);
      renderDetail(record);
      elements.scriptDetail.hidden = false;
    } catch (error) {
      if (error.authFailure) {
        showAuth(error.message);
      } else {
        elements.detailError.textContent = error.message;
        elements.detailError.hidden = false;
        if (error.message.startsWith("This script was not found")) await refreshQueue();
      }
    } finally {
      elements.detailLoading.hidden = true;
    }
  }

  function confirmDecision(decision, scriptId) {
    pendingDecision = { decision, scriptId };
    const approved = decision === "approved";
    elements.dialogTitle.textContent = approved ? "Approve this script?" : "Reject this script?";
    elements.dialogMessage.textContent = approved
      ? "Approval makes this script eligible for a separate production workflow. It will not create media or publish anything."
      : "This script will not be eligible for production. This decision is recorded and cannot be changed here.";
    elements.dialogConfirm.textContent = approved ? "Confirm approval" : "Confirm rejection";
    elements.decisionDialog.showModal();
  }

  async function submitDecision() {
    if (!pendingDecision) return;
    const { decision, scriptId } = pendingDecision;
    pendingDecision = null;
    const button = elements.dialogConfirm;
    button.disabled = true;
    try {
      await request(`/api/scripts/${encodeURIComponent(scriptId)}/${decision === "approved" ? "approve" : "reject"}`, {
        method: "POST",
        body: JSON.stringify({ confirm_review: true, reviewer })
      });
      await refreshQueue(`Script ${scriptId} ${decision}.`);
    } catch (error) {
      if (error.authFailure) showAuth(error.message);
      else showNotice(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  passwordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const suppliedPassword = passwordInput.value;
    passwordInput.value = "";
    passwordError.textContent = "";
    try {
      await request("/api/auth/validate", {}, suppliedPassword);
      auth.storePassword(suppliedPassword);
      adminPassword = suppliedPassword;
      passwordDialog.close();
      reviewer = elements.reviewerInput.value.trim() || "Admin";
      elements.authView.hidden = true;
      elements.dashboardView.hidden = false;
      await refreshQueue();
    } catch (error) {
      if (error.authFailure) return;
      passwordError.textContent = error instanceof Error
        ? error.message
        : "Sign in could not be completed.";
    }
  });

  elements.authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const suppliedReviewer = elements.reviewerInput.value.trim();
    if (!suppliedReviewer) return;
    hideNotice();
    reviewer = suppliedReviewer;
    elements.authView.hidden = true;
    elements.dashboardView.hidden = false;
    elements.refreshButton.disabled = false;
    await refreshQueue();
  });
  elements.refreshButton.addEventListener("click", () => refreshQueue());
  elements.signoutButton.addEventListener("click", () => {
    queue = [];
    selectedId = null;
    elements.queueList.replaceChildren();
    elements.scriptDetail.replaceChildren();
    hideNotice();
    showAuth();
  });
  window.addEventListener("motivationos:signout", () => showAuth());
  elements.decisionDialog.addEventListener("close", () => {
    if (elements.decisionDialog.returnValue === "confirm") submitDecision();
    else pendingDecision = null;
  });
  if (adminPassword) {
    reviewer = elements.reviewerInput.value.trim() || "Admin";
    elements.authView.hidden = true;
    elements.dashboardView.hidden = false;
    refreshQueue();
  } else promptForPassword();
})();
