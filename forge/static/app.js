const roles = ["brain", "planner", "coder_tdd", "coder_explore", "coder_classic", "reviewer", "tester"];
const roleMeta = {
  brain: ["Persistent brain", "Product direction · strongest model"],
  planner: ["Planner", "Repository-aware batch design"],
  coder_tdd: ["Coder · TDD", "Test-first competitor"],
  coder_explore: ["Coder · exploratory", "Prototype, refactor, test"],
  coder_classic: ["Coder · classic", "Independent conventional approach"],
  reviewer: ["Reviewer", "Compares all candidates"],
  tester: ["Black-box tester", "Public behavior only"],
};
const defaults = {
  brain: "codex:gpt-5.6-sol:high",
  planner: "codex:gpt-5.6-sol:high",
  coder_tdd: "codex:gpt-5.6-luna:high",
  coder_explore: "codex:gpt-5.6-luna:high",
  coder_classic: "codex:gpt-5.6-luna:high",
  reviewer: "codex:gpt-5.6-terra:high",
  tester: "codex:gpt-5.6-terra:high",
};
const phases = ["preflight", "brain", "planning", "coding", "review", "winner-fix", "delivery", "black-box"];
const phaseLabels = ["Preflight", "Brain", "Plan", "Code ×3", "Review", "Fix", "Deliver", "Black-box"];
const storageKey = "forge-control-room-v2";
let selected = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function formatTokens(value) {
  const number = Number(value || 0);
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`;
  if (number >= 1_000) return `${(number / 1_000).toFixed(1)}K`;
  return number.toLocaleString();
}

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || response.statusText);
  return value;
}

function buildModelFields() {
  const box = document.querySelector("#models");
  const template = document.querySelector("#model-template");
  roles.forEach(role => {
    const fragment = template.content.cloneNode(true);
    fragment.querySelector(".model-label").textContent = roleMeta[role][0];
    fragment.querySelector(".model-help").textContent = roleMeta[role][1];
    const input = fragment.querySelector("input");
    input.id = `model-${role}`;
    input.value = defaults[role];
    box.appendChild(fragment);
  });
}

function restoreRecommended() {
  roles.forEach(role => { document.querySelector(`#model-${role}`).value = defaults[role]; });
  saveForm();
}

function saveForm() {
  const value = {
    repo: document.querySelector("#repo").value,
    branch: document.querySelector("#branch").value,
    briefPath: document.querySelector("#brief-path").value,
    brief: document.querySelector("#brief").value,
    push: document.querySelector("#push").checked,
    models: Object.fromEntries(roles.map(role => [role, document.querySelector(`#model-${role}`).value])),
  };
  localStorage.setItem(storageKey, JSON.stringify(value));
}

function restoreForm() {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey));
    if (!value) return;
    document.querySelector("#repo").value = value.repo || "";
    document.querySelector("#brief-path").value = value.briefPath || "";
    document.querySelector("#brief").value = value.brief || "";
    document.querySelector("#push").checked = value.push !== false;
    roles.forEach(role => {
      document.querySelector(`#model-${role}`).value = value.models?.[role] || defaults[role];
    });
    setBranches([value.branch || "main"], value.branch || "main");
  } catch (error) {
    console.warn("Could not restore Forge form", error);
  }
}

function setBranches(branches, selectedBranch) {
  const select = document.querySelector("#branch");
  const values = branches.length ? branches : [selectedBranch || "main"];
  select.innerHTML = values.map(branch => `<option${branch === selectedBranch ? " selected" : ""}>${escapeHtml(branch)}</option>`).join("");
}

async function inspectRepository() {
  const error = document.querySelector("#form-error");
  const button = document.querySelector("#inspect-repo");
  button.disabled = true;
  button.textContent = "Inspecting…";
  try {
    const repo = document.querySelector("#repo").value.trim();
    const value = await api(`/api/repository?repo=${encodeURIComponent(repo)}`);
    setBranches(value.branches, value.current_branch || "main");
    if (value.brief_path) {
      document.querySelector("#brief-path").value = value.brief_path;
      document.querySelector("#brief").value = value.brief_text || "";
      document.querySelector("#brief-hint").textContent = `Detected ${value.brief_path}. Editing the preview switches to pasted text.`;
    }
    const summary = document.querySelector("#repo-summary");
    const blocking = (value.status || []).filter(line => !value.brief_path || !line.endsWith(value.brief_path.split("/").pop()));
    summary.innerHTML = [
      `<span class="summary-chip good">Git repository ready</span>`,
      `<span class="summary-chip ${value.has_head ? "good" : "warn"}">${value.has_head ? "Existing history" : "Empty · Forge will initialize it"}</span>`,
      `<span class="summary-chip ${blocking.length ? "warn" : "good"}">${blocking.length ? `${blocking.length} uncommitted item(s)` : "Clean working tree"}</span>`,
      value.brief_path ? `<span class="summary-chip good">Brief detected</span>` : `<span class="summary-chip warn">No goal.md / brief.md detected</span>`,
    ].join("");
    summary.classList.remove("hidden");
    error.textContent = "";
    saveForm();
  } catch (exception) {
    error.textContent = exception.message;
  } finally {
    button.disabled = false;
    button.textContent = "Inspect";
  }
}

function phaseRail(run) {
  const current = phases.indexOf(run.phase);
  return `<div class="phase-rail">${phaseLabels.map((label, index) => {
    const className = index < current ? "done" : index === current ? "current" : "";
    return `<div class="phase-step ${className}">${label}</div>`;
  }).join("")}</div>`;
}

function runCard(run) {
  const index = phases.indexOf(run.phase);
  const progress = run.status === "complete" ? 100 : Math.max(4, ((index + 1) / phases.length) * 100);
  const active = Object.keys(run.active_agents || {}).length;
  return `<article class="run-card ${selected === run.run_id ? "selected" : ""}" data-id="${escapeHtml(run.run_id)}">
    <div class="run-top"><span class="run-id">${escapeHtml(run.run_id)}</span><span class="badge ${escapeHtml(run.status)}">${escapeHtml(run.status)}</span></div>
    <p class="phase">${escapeHtml(run.phase)} · batch ${run.cycle}${active ? ` · ${active} active` : ""}</p>
    <div class="run-progress ${run.alive ? "animated" : ""}"><span style="width:${progress}%"></span></div>
  </article>`;
}

function usageSummary(run) {
  const rows = (run.usage || []).map(line => { try { return JSON.parse(line); } catch { return null; } }).filter(Boolean);
  return rows.reduce((total, row) => {
    total.input += row.usage?.input_tokens || 0;
    total.cached += row.usage?.cached_input_tokens || 0;
    total.output += row.usage?.output_tokens || 0;
    total.elapsed += row.elapsed_seconds || 0;
    return total;
  }, {input: 0, cached: 0, output: 0, elapsed: 0});
}

function activeAgents(run) {
  const entries = Object.entries(run.active_agents || {});
  if (!entries.length) return "";
  return `<h3>Active agents</h3><div class="active-grid">${entries.map(([key, agent]) => {
    const elapsed = Math.max(0, Math.round((Date.now() - Date.parse(agent.started_at)) / 1000));
    const progress = agent.tasks_total ? `${agent.tasks_completed || 0}/${agent.tasks_total} tasks` : `attempt ${agent.attempt}`;
    return `<article class="agent-card"><strong>${escapeHtml(agent.role || key)}</strong><span>${escapeHtml(agent.model)}</span><div class="agent-meta"><b>${progress}</b><b>${agent.changed_files ?? 0} files · ${elapsed}s</b></div></article>`;
  }).join("")}</div>`;
}

function candidateRows(metrics = {}) {
  return Object.entries(metrics).map(([name, metric]) => `<tr>
    <td><strong>${escapeHtml(name)}${metric.selected ? " ★" : ""}</strong></td><td>${escapeHtml(metric.status)}</td>
    <td>${metric.tasks_completed}/${metric.tasks_total}</td><td>${metric.review_score ?? "—"}</td>
    <td>${formatTokens(metric.total_tokens)}</td><td>${metric.validation_passed}/${metric.validation_total}</td>
  </tr>`).join("");
}

function batches(run) {
  if (!(run.batches || []).length) return `<p class="empty">No batch has been delivered yet.</p>`;
  return run.batches.map(batch => `<article class="batch">
    <div class="batch-head"><div><h3>Batch ${batch.cycle}: ${escapeHtml(batch.objective)}</h3><p>Commit <code>${escapeHtml(batch.commit).slice(0, 12)}</code></p></div><strong class="winner">${escapeHtml(batch.winner)} won</strong></div>
    <table class="candidate-table"><thead><tr><th>Candidate</th><th>Status</th><th>Tasks</th><th>Review</th><th>Tokens</th><th>Checks</th></tr></thead><tbody>${candidateRows(batch.candidate_metrics)}</tbody></table>
    <p><strong>Black-box:</strong> ${escapeHtml(batch.black_box?.summary || "No report")}</p>
  </article>`).join("");
}

function eventRows(run) {
  return (run.events || []).map(line => {
    try {
      const event = JSON.parse(line);
      const time = event.at ? new Date(event.at).toLocaleTimeString() : "";
      return `<div class="event"><time>${escapeHtml(time)}</time><span class="event-kind">${escapeHtml(event.kind)}</span><span>${escapeHtml(event.message)}</span></div>`;
    } catch {
      return `<div class="event"><span></span><span>raw</span><span>${escapeHtml(line)}</span></div>`;
    }
  }).join("");
}

function detail(run) {
  const usage = usageSummary(run);
  const warnings = (run.warnings || []).slice(-20).map(item => `<li>${escapeHtml(item)}</li>`).join("");
  const controllable = ["running", "paused"].includes(run.status);
  return `${phaseRail(run)}
    <div class="stats">
      <div class="stat"><small>Status</small><strong>${escapeHtml(run.status)}</strong></div>
      <div class="stat"><small>Phase</small><strong>${escapeHtml(run.phase)}</strong></div>
      <div class="stat"><small>Batch</small><strong>${run.cycle}</strong></div>
      <div class="stat"><small>Input / cached</small><strong>${formatTokens(usage.input)} / ${formatTokens(usage.cached)}</strong></div>
      <div class="stat"><small>Output tokens</small><strong>${formatTokens(usage.output)}</strong></div>
    </div>
    <p class="run-message">${escapeHtml(run.message)}</p>
    <div class="artifact-path"><code>${escapeHtml(run.artifact_dir)}</code></div>
    <div class="run-actions">
      <button class="button ghost" data-action="pause" ${run.status !== "running" ? "disabled" : ""}>Pause</button>
      <button class="button ghost" data-action="resume" ${run.status !== "paused" ? "disabled" : ""}>Resume</button>
      <button class="button ghost" data-action="cancel" ${!controllable ? "disabled" : ""}>Cancel</button>
      <button class="button secondary" data-action="recover" ${run.status !== "failed" || run.alive ? "disabled" : ""}>Recover same run</button>
    </div>
    ${activeAgents(run)}
    ${warnings ? `<ul class="warnings">${warnings}</ul>` : ""}
    ${run.error ? `<details><summary>Process error</summary><pre class="error-detail">${escapeHtml(run.error)}</pre></details>` : ""}
    ${batches(run)}
    <h3>Recent events</h3><div class="events">${eventRows(run) || "No events yet."}</div>`;
}

async function refresh() {
  try {
    const runs = await api("/api/runs");
    document.querySelector("#run-count").textContent = runs.length;
    document.querySelector("#run-list").innerHTML = runs.length ? runs.map(runCard).join("") : `<p class="empty">No runs in this process yet.</p>`;
    document.querySelectorAll(".run-card").forEach(card => card.addEventListener("click", () => { selected = card.dataset.id; refresh(); }));
    if (!selected && runs.length) selected = runs[0].run_id;
    if (!selected) return;
    const run = await api(`/api/runs/${selected}`);
    document.querySelector("#detail").classList.remove("hidden");
    const indicator = document.querySelector("#live-indicator");
    indicator.textContent = run.alive ? "Live" : "Stopped";
    indicator.classList.toggle("live", run.alive);
    document.querySelector("#detail-body").innerHTML = detail(run);
    document.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", async () => {
      button.disabled = true;
      try { await api(`/api/runs/${selected}/${button.dataset.action}`, {method: "POST", body: "{}"}); }
      catch (error) { document.querySelector("#form-error").textContent = error.message; }
      await refresh();
    }));
  } catch (error) {
    console.error(error);
  }
}

buildModelFields();
restoreForm();
document.querySelector("#recommended").addEventListener("click", restoreRecommended);
document.querySelector("#inspect-repo").addEventListener("click", inspectRepository);
document.querySelector("#brief").addEventListener("input", () => {
  if (document.querySelector("#brief-path").value) {
    document.querySelector("#brief-path").value = "";
    document.querySelector("#brief-hint").textContent = "Using the pasted brief below.";
  }
  saveForm();
});
document.querySelector("#run-form").addEventListener("change", saveForm);
document.querySelector("#run-form").addEventListener("submit", async event => {
  event.preventDefault();
  const error = document.querySelector("#form-error");
  const button = event.submitter;
  const models = Object.fromEntries(roles.map(role => [role, document.querySelector(`#model-${role}`).value.trim()]));
  const payload = {
    repo: document.querySelector("#repo").value.trim(),
    branch: document.querySelector("#branch").value,
    brief_path: document.querySelector("#brief-path").value.trim(),
    brief_text: document.querySelector("#brief").value.trim(),
    push: document.querySelector("#push").checked,
    models,
  };
  if (!payload.brief_path && !payload.brief_text) { error.textContent = "Choose a brief file or paste the product brief."; return; }
  button.disabled = true;
  button.firstElementChild.textContent = "Starting…";
  try {
    const run = await api("/api/runs", {method: "POST", body: JSON.stringify(payload)});
    selected = run.run_id;
    error.textContent = "";
    saveForm();
    await refresh();
  } catch (exception) {
    error.textContent = exception.message;
  } finally {
    button.disabled = false;
    button.firstElementChild.textContent = "Start Forge";
  }
});

refresh();
setInterval(refresh, 3000);
