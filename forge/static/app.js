const roles = ["brain", "planner", "reviewer", "tester", "whitebox"];
const roleMeta = {
  brain: ["Persistent brain", "Product direction · strongest model"],
  planner: ["Planner", "Repository-aware batch design"],
  reviewer: ["Reviewer", "Compares all candidates"],
  tester: ["Black-box tester", "Public behavior only"],
  whitebox: ["White-box reporter", "Interprets short and long tests"],
};
const defaults = {
  brain: "codex:gpt-5.6-sol:high",
  planner: "codex:gpt-5.6-sol:high",
  reviewer: "codex:gpt-5.6-terra:high",
  tester: "codex:gpt-5.6-terra:high",
  whitebox: "codex:gpt-5.6-terra:high",
};
const defaultCoder = "codex:gpt-5.6-luna:high";
const defaultCoderPool = [defaultCoder, defaultCoder, defaultCoder];
const maxCoderModels = 12;
const phases = ["preflight", "brain", "planning", "coding", "review", "winner-fix", "delivery", "whitebox", "black-box"];
const phaseLabels = ["Preflight", "Brain", "Plan", "Code ×3", "Review", "Fix", "Deliver", "White-box", "Black-box"];
const storageKey = "forge-control-room-v3";
const providerLabels = {codex: "Codex", opencode: "OpenCode"};
const familyLabels = {gpt: "GPT", grok: "Grok", qwen: "Qwen", deepseek: "DeepSeek", glm: "GLM"};
const effortLabels = {"": "Default", low: "Low", medium: "Medium", high: "High"};
const fallbackCatalog = {
  providers: ["codex", "opencode"],
  models: [
    {key: "gpt-5.6-sol", label: "GPT-5.6 Sol", family: "gpt", providers: ["codex", "opencode"], ids: {codex: "gpt-5.6-sol", opencode: "openai/gpt-5.6-sol"}, efforts: ["", "low", "medium", "high"]},
    {key: "gpt-5.6-terra", label: "GPT-5.6 Terra", family: "gpt", providers: ["codex", "opencode"], ids: {codex: "gpt-5.6-terra", opencode: "openai/gpt-5.6-terra"}, efforts: ["", "low", "medium", "high"]},
    {key: "gpt-5.6-luna", label: "GPT-5.6 Luna", family: "gpt", providers: ["codex", "opencode"], ids: {codex: "gpt-5.6-luna", opencode: "openai/gpt-5.6-luna"}, efforts: ["", "low", "medium", "high"]},
    {key: "gpt-5.5", label: "GPT-5.5", family: "gpt", providers: ["opencode"], ids: {opencode: "openai/gpt-5.5"}, efforts: ["", "low", "medium", "high"]},
    {key: "gpt-5.4", label: "GPT-5.4", family: "gpt", providers: ["opencode"], ids: {opencode: "openai/gpt-5.4"}, efforts: ["", "low", "medium", "high"]},
    {key: "grok-4.6", label: "Grok 4.6", family: "grok", providers: ["opencode"], ids: {opencode: "xai/grok-4.6"}, efforts: ["", "low", "medium", "high"]},
    {key: "qwen-3.8-max", label: "Qwen 3.8 Max", family: "qwen", providers: ["opencode"], ids: {opencode: "alibaba-token-plan/qwen3.8-max"}, efforts: ["", "low", "medium", "high"]},
    {key: "deepseek-v4-flash-0731", label: "DeepSeek Flash 0731", family: "deepseek", providers: ["opencode"], ids: {opencode: "alibaba-token-plan/deepseek-v4-flash-0731"}, efforts: ["", "low", "medium", "high"]},
    {key: "deepseek-v4-pro-0813", label: "DeepSeek Pro 0813", family: "deepseek", providers: ["opencode"], ids: {opencode: "alibaba-token-plan/deepseek-v4-pro-0813"}, efforts: ["", "low", "medium", "high"]},
    {key: "glm-5.3", label: "GLM 5.3", family: "glm", providers: ["opencode"], ids: {opencode: "zai-coding-plan/glm-5.3"}, efforts: ["", "low", "medium", "high"]},
  ],
};
let catalog = fallbackCatalog;
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

function roleCard(role) {
  return document.querySelector(`.model-card[data-role="${role}"]`);
}

function resolveEntry(modelValue) {
  return catalog.models.find(entry =>
    entry.key === modelValue || Object.values(entry.ids || {}).includes(modelValue)
  ) || null;
}

function parseSelector(value) {
  const parts = String(value || "").trim().split(":");
  const provider = catalog.providers.includes(parts[0]) ? parts[0] : (catalog.providers[0] || "codex");
  return {provider, entry: resolveEntry(parts[1] || ""), effort: parts.length > 2 ? parts.slice(2).join(":") : ""};
}

function providerOptions(selectedProvider) {
  return catalog.providers.map(provider =>
    `<option value="${escapeHtml(provider)}"${provider === selectedProvider ? " selected" : ""}>${escapeHtml(providerLabels[provider] || provider)}</option>`
  ).join("");
}

function modelOptions(provider, selectedKey) {
  const available = catalog.models.filter(entry => entry.providers.includes(provider));
  const groups = [];
  available.forEach(entry => {
    const last = groups[groups.length - 1];
    if (!last || last.family !== entry.family) groups.push({family: entry.family, items: [entry]});
    else last.items.push(entry);
  });
  return groups.map(group =>
    `<optgroup label="${escapeHtml(familyLabels[group.family] || group.family)}">${group.items.map(entry =>
      `<option value="${escapeHtml(entry.key)}"${entry.key === selectedKey ? " selected" : ""}>${escapeHtml(entry.label)}</option>`
    ).join("")}</optgroup>`
  ).join("");
}

function effortOptions(entry, selectedEffort) {
  const efforts = entry?.efforts?.length ? entry.efforts : ["", "low", "medium", "high"];
  const chosen = efforts.includes(selectedEffort) ? selectedEffort : (efforts.includes("high") ? "high" : efforts[0]);
  return efforts.map(value =>
    `<option value="${escapeHtml(value)}"${value === chosen ? " selected" : ""}>${escapeHtml(effortLabels[value] || value || "Default")}</option>`
  ).join("");
}

function applySelector(card, value) {
  const parsed = parseSelector(value);
  let provider = parsed.provider;
  let entry = parsed.entry;
  if (entry && !entry.providers.includes(provider)) provider = entry.providers[0];
  if (!entry || !entry.providers.includes(provider)) {
    entry = catalog.models.find(item => item.providers.includes(provider)) || catalog.models[0];
  }
  const providerSelect = card.querySelector(".model-provider");
  const modelSelect = card.querySelector(".model-name");
  const effortSelect = card.querySelector(".model-effort");
  providerSelect.innerHTML = providerOptions(provider);
  providerSelect.value = provider;
  modelSelect.innerHTML = modelOptions(provider, entry?.key);
  if (entry) modelSelect.value = entry.key;
  effortSelect.innerHTML = effortOptions(entry, parsed.effort);
}

function selectorFromCard(card) {
  const provider = card.querySelector(".model-provider").value;
  const model = card.querySelector(".model-name").value;
  const effort = card.querySelector(".model-effort").value;
  return effort ? `${provider}:${model}:${effort}` : `${provider}:${model}`;
}

function syncModelCard(card) {
  const provider = card.querySelector(".model-provider").value;
  const currentModel = card.querySelector(".model-name").value;
  const currentEffort = card.querySelector(".model-effort").value;
  const entry = resolveEntry(currentModel);
  const nextKey = entry && entry.providers.includes(provider)
    ? entry.key
    : (catalog.models.find(item => item.providers.includes(provider)) || catalog.models[0])?.key;
  card.querySelector(".model-name").innerHTML = modelOptions(provider, nextKey);
  if (nextKey) card.querySelector(".model-name").value = nextKey;
  card.querySelector(".model-effort").innerHTML = effortOptions(resolveEntry(nextKey), currentEffort);
}

function coderCards() {
  return [...document.querySelectorAll("#coder-models .model-card")];
}

function coderSelectors() {
  return coderCards().map(selectorFromCard);
}

function relabelCoderCards() {
  const cards = coderCards();
  cards.forEach((card, index) => {
    card.querySelector(".model-label").textContent = `Coder ${index + 1}`;
    const remove = card.querySelector(".model-remove");
    if (remove) remove.disabled = cards.length <= 1;
  });
  const add = document.querySelector("#add-coder");
  if (add) add.disabled = cards.length >= maxCoderModels;
}

function addCoderCard(value = defaultCoder) {
  if (coderCards().length >= maxCoderModels) return;
  const template = document.querySelector("#model-template");
  const fragment = template.content.cloneNode(true);
  const card = fragment.querySelector(".model-card");
  card.dataset.role = "coder";
  fragment.querySelector(".model-help").textContent = "Drawn into TDD / explore / classic";
  const remove = fragment.querySelector(".model-remove");
  remove.classList.remove("hidden");
  remove.addEventListener("click", event => {
    event.preventDefault();
    removeCoderCard(card);
  });
  applySelector(card, value);
  document.querySelector("#coder-models").appendChild(fragment);
  relabelCoderCards();
}

function removeCoderCard(card) {
  if (coderCards().length <= 1) return;
  card.remove();
  relabelCoderCards();
  saveForm();
}

function replaceCoderPool(values) {
  document.querySelector("#coder-models").innerHTML = "";
  const pool = values.length ? values : defaultCoderPool;
  pool.slice(0, maxCoderModels).forEach(value => addCoderCard(value));
}

function savedCoderPool(value) {
  if (Array.isArray(value.coder_models) && value.coder_models.length) return value.coder_models;
  const previous = ["coder_tdd", "coder_explore", "coder_classic"]
    .map(role => value.models?.[role])
    .filter(Boolean);
  return previous.length ? previous : defaultCoderPool;
}

function buildModelFields() {
  const box = document.querySelector("#models");
  const template = document.querySelector("#model-template");
  roles.forEach(role => {
    const fragment = template.content.cloneNode(true);
    const card = fragment.querySelector(".model-card");
    card.dataset.role = role;
    fragment.querySelector(".model-label").textContent = roleMeta[role][0];
    fragment.querySelector(".model-help").textContent = roleMeta[role][1];
    applySelector(card, defaults[role]);
    box.appendChild(fragment);
  });
  replaceCoderPool(defaultCoderPool);
}

function restoreRecommended() {
  roles.forEach(role => applySelector(roleCard(role), defaults[role]));
  replaceCoderPool(defaultCoderPool);
  saveForm();
}

function saveForm() {
  const value = {
    repo: document.querySelector("#repo").value,
    branch: document.querySelector("#branch").value,
    briefPath: document.querySelector("#brief-path").value,
    brief: document.querySelector("#brief").value,
    push: document.querySelector("#push").checked,
    models: Object.fromEntries(roles.map(role => [role, selectorFromCard(roleCard(role))])),
    coder_models: coderSelectors(),
  };
  localStorage.setItem(storageKey, JSON.stringify(value));
}

function restoreForm() {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) || localStorage.getItem("forge-control-room-v2"));
    if (!value) return;
    document.querySelector("#repo").value = value.repo || "";
    document.querySelector("#brief-path").value = value.briefPath || "";
    document.querySelector("#brief").value = value.brief || "";
    document.querySelector("#push").checked = value.push !== false;
    roles.forEach(role => applySelector(roleCard(role), value.models?.[role] || defaults[role]));
    replaceCoderPool(savedCoderPool(value));
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

const explorer = {mode: "dir", current: "", parent: null, home: "", onSelect: null};

function closeExplorer() {
  document.querySelector("#fs-explorer").close();
}

function renderExplorer(value) {
  explorer.current = value.path;
  explorer.parent = value.parent;
  explorer.home = value.home;
  document.querySelector("#fs-path").value = value.path;
  document.querySelector("#fs-up").disabled = !value.parent;
  const error = document.querySelector("#fs-error");
  error.textContent = value.truncated ? "Showing the first 1000 entries." : "";
  const list = document.querySelector("#fs-list");
  if (!value.entries.length) {
    list.innerHTML = `<li class="empty" style="padding:18px 20px">Empty directory.</li>`;
    return;
  }
  list.innerHTML = value.entries.map(entry => {
    const inert = explorer.mode === "dir" && entry.kind === "file";
    const icon = entry.kind === "dir" ? (entry.is_repo ? "repo" : "dir") : "file";
    return `<li class="fs-item ${entry.kind}${inert ? " inert" : ""}" tabindex="${inert ? "-1" : "0"}" data-path="${escapeHtml(entry.path)}" data-kind="${entry.kind}">
      <span class="fs-icon ${icon}"></span>
      <span class="fs-name">${escapeHtml(entry.name)}</span>
      ${entry.is_repo ? `<span class="fs-tag">git</span>` : ""}
    </li>`;
  }).join("");
}

async function loadExplorer(path) {
  const error = document.querySelector("#fs-error");
  const list = document.querySelector("#fs-list");
  list.innerHTML = `<li class="empty" style="padding:18px 20px">Loading…</li>`;
  try {
    const value = await api(`/api/browse?path=${encodeURIComponent(path || "")}`);
    renderExplorer(value);
  } catch (exception) {
    error.textContent = exception.message;
    if (path) {
      try {
        renderExplorer(await api("/api/browse"));
      } catch {
        /* keep the original error */
      }
    }
  }
}

async function openExplorer({mode, start, title, onSelect}) {
  explorer.mode = mode;
  explorer.onSelect = onSelect;
  document.querySelector("#fs-title").textContent = title;
  document.querySelector("#fs-select").classList.toggle("hidden", mode !== "dir");
  document.querySelector("#fs-error").textContent = "";
  document.querySelector("#fs-explorer").showModal();
  await loadExplorer(start);
}

async function chooseExplorerEntry(path, kind) {
  if (kind === "dir") {
    await loadExplorer(path);
    return;
  }
  if (explorer.mode !== "file" || !explorer.onSelect) return;
  explorer.onSelect(path);
  closeExplorer();
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

function coderDraw(run) {
  const models = run.config?.models || {};
  const labels = {coder_tdd: "TDD", coder_explore: "Explore", coder_classic: "Classic"};
  const rows = Object.entries(labels).map(([role, label]) => {
    const spec = models[role];
    const text = spec
      ? `${spec.provider}:${spec.model}${spec.effort ? `:${spec.effort}` : ""}`
      : "—";
    return `<li><strong>${escapeHtml(label)}</strong> ${escapeHtml(text)}</li>`;
  }).join("");
  return `<h3>Coder draw</h3><ul class="coder-draw">${rows}</ul>`;
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
    ${coderDraw(run)}
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

async function boot() {
  const button = document.querySelector("#run-form button[type=submit]");
  button.disabled = true;
  try { catalog = await api("/api/catalog"); }
  catch (error) { console.warn("Could not load model catalog", error); }
  buildModelFields();
  restoreForm();
  button.disabled = false;
}

function showRestartConfirm(visible) {
  document.querySelector("#restart-confirm").classList.toggle("hidden", !visible);
  document.querySelector("#restart").classList.toggle("hidden", visible);
}

async function waitForRestart() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 400));
    try {
      await api("/api/health");
      location.reload();
      return;
    } catch {
      /* server is bouncing */
    }
  }
  document.querySelector("#form-error").textContent = "Forge did not come back after restart.";
}

async function restartForge(confirm = false) {
  const error = document.querySelector("#form-error");
  const button = document.querySelector("#restart");
  button.disabled = true;
  try {
    const result = await api("/api/restart", {method: "POST", body: JSON.stringify({confirm})});
    if (result.needs_confirm) {
      showRestartConfirm(true);
      return;
    }
    showRestartConfirm(false);
    button.textContent = "Restarting…";
    await waitForRestart();
  } catch (exception) {
    error.textContent = exception.message;
  } finally {
    button.disabled = false;
  }
}

document.querySelector("#recommended").addEventListener("click", restoreRecommended);
document.querySelector("#browse-repo").addEventListener("click", () => {
  openExplorer({
    mode: "dir",
    start: document.querySelector("#repo").value.trim(),
    title: "Choose a repository",
    onSelect: path => {
      document.querySelector("#repo").value = path;
      saveForm();
      inspectRepository();
    },
  });
});
document.querySelector("#browse-brief").addEventListener("click", () => {
  const briefPath = document.querySelector("#brief-path").value.trim();
  openExplorer({
    mode: "file",
    start: briefPath || document.querySelector("#repo").value.trim(),
    title: "Choose a brief",
    onSelect: async path => {
      document.querySelector("#brief-path").value = path;
      try {
        const file = await api(`/api/file?path=${encodeURIComponent(path)}`);
        document.querySelector("#brief").value = file.text || "";
        document.querySelector("#brief-hint").textContent = `Using ${path}. Editing the preview switches to pasted text.`;
      } catch (exception) {
        document.querySelector("#form-error").textContent = exception.message;
      }
      saveForm();
    },
  });
});
document.querySelector("#inspect-repo").addEventListener("click", inspectRepository);
document.querySelector("#fs-close").addEventListener("click", closeExplorer);
document.querySelector("#fs-cancel").addEventListener("click", closeExplorer);
document.querySelector("#fs-home").addEventListener("click", () => loadExplorer(explorer.home));
document.querySelector("#fs-up").addEventListener("click", () => {
  if (explorer.parent) loadExplorer(explorer.parent);
});
document.querySelector("#fs-select").addEventListener("click", () => {
  if (explorer.mode !== "dir" || !explorer.current || !explorer.onSelect) return;
  explorer.onSelect(explorer.current);
  closeExplorer();
});
document.querySelector("#fs-path").addEventListener("keydown", event => {
  if (event.key === "Enter") {
    event.preventDefault();
    loadExplorer(event.target.value.trim());
  }
});
document.querySelector("#fs-list").addEventListener("click", event => {
  const item = event.target.closest(".fs-item");
  if (!item || item.classList.contains("inert")) return;
  chooseExplorerEntry(item.dataset.path, item.dataset.kind);
});
document.querySelector("#fs-list").addEventListener("keydown", event => {
  if (event.key !== "Enter") return;
  const item = event.target.closest(".fs-item");
  if (!item || item.classList.contains("inert")) return;
  chooseExplorerEntry(item.dataset.path, item.dataset.kind);
});
document.querySelector("#add-coder").addEventListener("click", () => {
  addCoderCard();
  saveForm();
});
document.querySelector("#restart").addEventListener("click", () => restartForge(false));
document.querySelector("#restart-yes").addEventListener("click", () => restartForge(true));
document.querySelector("#restart-no").addEventListener("click", () => showRestartConfirm(false));
document.querySelector("#brief").addEventListener("input", () => {
  if (document.querySelector("#brief-path").value) {
    document.querySelector("#brief-path").value = "";
    document.querySelector("#brief-hint").textContent = "Using the pasted brief below.";
  }
  saveForm();
});
document.querySelector("#run-form").addEventListener("change", event => {
  if (event.target.classList.contains("model-provider") || event.target.classList.contains("model-name")) {
    syncModelCard(event.target.closest(".model-card"));
  }
  saveForm();
});
document.querySelector("#run-form").addEventListener("submit", async event => {
  event.preventDefault();
  const error = document.querySelector("#form-error");
  const button = event.submitter;
  const models = Object.fromEntries(roles.map(role => [role, selectorFromCard(roleCard(role))]));
  const payload = {
    repo: document.querySelector("#repo").value.trim(),
    branch: document.querySelector("#branch").value,
    brief_path: document.querySelector("#brief-path").value.trim(),
    brief_text: document.querySelector("#brief").value.trim(),
    push: document.querySelector("#push").checked,
    models,
    coder_models: coderSelectors(),
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

boot();
refresh();
setInterval(refresh, 3000);
