/* LeadGen Engine — Frontend */

const API = "";   // same origin
let currentJobId = null;
let pollTimer = null;

// ── Prompt helpers ────────────────────────────────────────────────────────────
function setPrompt(text) {
  document.getElementById("prompt").value = text;
}

// ── Generate ──────────────────────────────────────────────────────────────────
async function generate() {
  const prompt = document.getElementById("prompt").value.trim();
  if (!prompt) { alert("Please enter a prompt."); return; }

  const minScore = parseInt(document.getElementById("min-score").value) || 20;
  const btn = document.getElementById("generate-btn");
  btn.disabled = true;
  btn.textContent = "Starting…";

  clearResults();
  showStatus("running", "Starting pipeline…");

  try {
    const res = await fetch(`${API}/api/leads/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, min_score: minScore }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    currentJobId = data.job_id;
    startPolling(currentJobId, minScore);
  } catch (e) {
    showStatus("failed", "Error: " + e.message);
    btn.disabled = false;
    btn.textContent = "Generate";
  }
}

// ── Polling ───────────────────────────────────────────────────────────────────
function startPolling(jobId, minScore) {
  clearTimeout(pollTimer);
  pollTimer = setInterval(() => pollJob(jobId, minScore), 3000);
  pollJob(jobId, minScore);
}

async function pollJob(jobId, minScore) {
  try {
    const res = await fetch(`${API}/api/jobs/${jobId}`);
    if (!res.ok) return;
    const job = await res.json();

    updateStats(job);

    if (job.status === "running" || job.status === "pending") {
      const pages = job.stats?.pages_crawled || 0;
      const queries = job.stats?.queries_generated || 0;
      showStatus("running", `Crawling… ${queries} queries · ${pages} pages crawled`);
    } else if (job.status === "completed") {
      clearInterval(pollTimer);
      showStatus("completed", `Done in ${job.duration_seconds?.toFixed(1) || "?"}s — loading leads…`);
      await loadLeads(jobId, minScore);
      document.getElementById("generate-btn").disabled = false;
      document.getElementById("generate-btn").textContent = "Generate";
      loadHistory();
    } else if (job.status === "failed") {
      clearInterval(pollTimer);
      showStatus("failed", "Pipeline failed: " + (job.error_message || "unknown error"));
      document.getElementById("generate-btn").disabled = false;
      document.getElementById("generate-btn").textContent = "Generate";
    }
  } catch (e) {
    console.error("poll error", e);
  }
}

// ── Load leads ────────────────────────────────────────────────────────────────
async function loadLeads(jobId, minScore = 0) {
  const res = await fetch(`${API}/api/leads/${jobId}?min_score=${minScore}&limit=500`);
  if (!res.ok) return;
  const data = await res.json();

  const tbody = document.getElementById("leads-body");
  tbody.innerHTML = "";
  document.getElementById("result-count").textContent = data.total;

  if (!data.leads.length) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:30px">No leads found. Try a broader prompt or lower the min score.</td></tr>';
  } else {
    data.leads.forEach((lead, i) => {
      tbody.insertAdjacentHTML("beforeend", renderRow(lead, i + 1));
    });
  }

  document.getElementById("results-section").classList.remove("hidden");
  document.getElementById("stat-found").textContent = data.total;
}

function renderRow(lead, n) {
  const score = lead.score || 0;
  const scoreClass = score >= 60 ? "score-high" : score >= 35 ? "score-mid" : "score-low";
  const web = lead.website ? `<a href="${lead.website}" target="_blank" rel="noopener">link</a>` : "—";
  const email = lead.email ? `<a href="mailto:${lead.email}">${lead.email}</a>` : "—";
  const source = lead.source_type?.replace("_", " ") || "—";

  return `<tr>
    <td style="color:var(--text-muted)">${n}</td>
    <td><strong>${esc(lead.business_name || lead.name || "—")}</strong></td>
    <td>${email}</td>
    <td>${esc(lead.phone || "—")}</td>
    <td>${esc(lead.city || "—")}</td>
    <td>${web}</td>
    <td>${esc(lead.industry || "—")}</td>
    <td><span class="source-tag">${source}</span></td>
    <td><span class="score-pill ${scoreClass}">${score}</span></td>
  </tr>`;
}

// ── Export CSV ────────────────────────────────────────────────────────────────
function exportCSV() {
  if (!currentJobId) return;
  const minScore = parseInt(document.getElementById("min-score").value) || 0;
  window.location.href = `${API}/api/leads/export/csv?job_id=${currentJobId}&min_score=${minScore}`;
}

// ── Stats ─────────────────────────────────────────────────────────────────────
function updateStats(job) {
  document.getElementById("stats-panel").classList.remove("hidden");
  const s = job.stats || {};
  document.getElementById("stat-queries").textContent  = s.queries_generated || 0;
  document.getElementById("stat-crawled").textContent  = s.pages_crawled || 0;
  document.getElementById("stat-found").textContent    = s.leads_above_threshold || 0;
  if (job.duration_seconds) {
    document.getElementById("stat-duration").textContent = job.duration_seconds.toFixed(1) + "s";
  }
}

// ── Status bar ────────────────────────────────────────────────────────────────
function showStatus(type, msg) {
  const bar = document.getElementById("status-bar");
  bar.className = `status-bar ${type}`;
  bar.innerHTML = type === "running"
    ? `<div class="spinner"></div><span>${msg}</span>`
    : `<span>${type === "completed" ? "✓" : "✗"} ${msg}</span>`;
  bar.classList.remove("hidden");
}

function clearResults() {
  document.getElementById("results-section").classList.add("hidden");
  document.getElementById("stats-panel").classList.add("hidden");
  document.getElementById("leads-body").innerHTML = "";
  document.getElementById("result-count").textContent = "0";
  currentJobId = null;
}

// ── History ───────────────────────────────────────────────────────────────────
async function loadHistory() {
  try {
    const res = await fetch(`${API}/api/jobs`);
    if (!res.ok) return;
    const jobs = await res.json();
    const list = document.getElementById("job-list");

    if (!jobs.length) { list.textContent = "No jobs yet."; return; }

    list.innerHTML = jobs.slice(0, 10).map(j => {
      const dotClass = `dot-${j.status}`;
      const leads = j.stats?.leads_above_threshold || 0;
      const ts = new Date(j.created_at).toLocaleTimeString();
      return `<div class="job-item" onclick="viewJob('${j.id}')">
        <div class="job-status-dot ${dotClass}"></div>
        <div class="job-prompt">${esc(j.prompt)}</div>
        <div class="job-meta">${leads} leads · ${ts}</div>
      </div>`;
    }).join("");
  } catch(e) { /* silent */ }
}

async function viewJob(jobId) {
  currentJobId = jobId;
  const minScore = parseInt(document.getElementById("min-score").value) || 0;
  await loadLeads(jobId, minScore);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ── Utils ─────────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.getElementById("prompt").addEventListener("keydown", e => {
  if (e.key === "Enter") generate();
});

loadHistory();
setInterval(loadHistory, 10000);
