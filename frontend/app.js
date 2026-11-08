const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = value => {
  const entities = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  };

  return String(value ?? "").replace(/[&<>"']/g, character => entities[character]);
};

const industries = ["Any industry", "Agriculture", "Arts & Entertainment", "Construction",
  "Education", "Energy & Utilities", "Financial Services", "Healthcare & Life Sciences",
  "Hospitality & Tourism", "Insurance", "Legal & Professional Services",
  "Logistics & Transportation", "Manufacturing", "Nonprofit & Public Sector", "Real Estate",
  "Retail & E-commerce", "Software & Technology", "Telecommunications", "Other"
];
let state = {
  me: null,
  workspace: null,
  datasets: [],
  model: null
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json"
    },
    credentials: "same-origin",
    ...options
  });
  let data = null;
  try {
    data = await response.json();
  } catch (_) {}
  if (!response.ok) throw new Error(data?.detail || "The request could not be completed.");
  return data;
}

function showApp() {
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
}

function showAuth() {
  $("#app-view").classList.add("hidden");
  $("#auth-view").classList.remove("hidden");
}

async function refreshWorkspace() {
  const data = await api("/api/workspace");
  state.workspace = data.workspace;
  state.datasets = data.datasets;
  state.model = data.model;
  renderWorkspace();
}

async function boot() {
  $("#industry-select").innerHTML = industries.map(v => `<option>${v}</option>`).join("");
  try {
    state.me = await api("/api/me");
    showApp();
    await refreshWorkspace();
    syncPageFromUrl();
  } catch (_) {
    showAuth();
  }
}

function renderWorkspace() {
  const ws = state.workspace || {};
  $("#workspace-name").textContent = ws.name || "Workspace";
  $("#workspace-industry").textContent = ws.industry || "Any industry";
  $("#objective-text").textContent = ws.objective ||
    "Define an objective and connect your business data.";
  $("#user-name").textContent = state.me.full_name;
  $("#user-role").textContent = state.me.role;
  $("#user-avatar").textContent = state.me.full_name.charAt(0).toUpperCase();
  $("#settings-name").value = ws.name || "";
  $("#settings-country").value = ws.country || "";
  $("#settings-industry").innerHTML = industries.map(v =>
    `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  $("#settings-industry").value = ws.industry || "Any industry";
  $("#settings-objective").value = ws.objective || "";
  $("#settings-user").textContent = state.me.full_name;
  $("#settings-email").textContent = state.me.email;
  $("#settings-role").textContent = `${state.me.role} · ${ws.country || "Global"}`;
  const dsCount = state.datasets.length;
  $("#overview-kpis").innerHTML =
    `
    <article class="kpi-card"><span>Datasets</span><strong>${dsCount}</strong></article>
    <article class="kpi-card"><span>Latest model</span><strong>${state.model ? "Ready" : "Not trained"}</strong></article>
    <article class="kpi-card"><span>Model type</span><strong>${state.model?.task_type || "—"}</strong></article>
    <article class="kpi-card"><span>Industry</span><strong>${String(ws.industry || "Any").split(" ")[0]}</strong></article>`;
  $("#readiness-list").innerHTML = [
    ["Workspace configured", true],
    ["Data connected", dsCount > 0],
    ["Model trained", !!state.model],
    ["Predictions generated", !!state.model]
  ].map(([name, ready]) =>
    `<div class="readiness-row"><span>${name}</span><span class="status-pill ${ready ? "" : "pending"}">${ready ? "Ready" : "Pending"}</span></div>`
    ).join("");
  renderModelSummary();
  renderDatasets();
  renderModelDatasetOptions();
  $$('[data-insight]').forEach(button => {
    button.disabled = !state.model;
  });
  $("#download-report").disabled = !state.model;
}

function renderModelSummary() {
  const el = $("#model-summary");
  if (!state.model) {
    el.innerHTML = "No model trained yet.";
    return;
  }
  const metrics = state.model.metrics || {};
  const metric = metrics.roc_auc != null ? `ROC-AUC ${Number(metrics.roc_auc).toFixed(3)}` : (
    metrics.r2 != null ? `R² ${Number(metrics.r2).toFixed(3)}` : "Evaluation complete");
  el.innerHTML = `<strong>${state.model.model}</strong><br>${metric}<br>Run ${state.model.run_id}`;
}

async function renderDatasets() {
  const list = $("#dataset-list");
  if (!state.datasets.length) {
    list.innerHTML =
      `<p class="empty-state">No datasets yet. Generate demo data or upload a CSV.</p>`;
    return;
  }
  list.innerHTML = state.datasets.map((dataset, index) => {
    const active = index === 0 ? "active" : "";
    const source = dataset.source === "synthetic_telco" ? "Demo dataset" : "CSV upload";

    return `
      <div
        class="dataset-row ${active}"
        data-dataset-id="${Number(dataset.id)}"
        tabindex="0"
        role="button"
      >
        <strong>${escapeHtml(dataset.name)}</strong>
        <span>${source} · Dataset ${Number(dataset.id)}</span>
      </div>
    `;
  }).join("");
  $$(".dataset-row", list).forEach(row => row.addEventListener("click", () => selectDataset(
    Number(row.dataset.datasetId), row)));
  $$(".dataset-row", list).forEach(row => row.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      row.click()
    }
  }));
  await selectDataset(state.datasets[0].id, $(".dataset-row", list));
}

async function selectDataset(id, row) {
  $$(".dataset-row", $("#dataset-list")).forEach(r => r.classList.remove("active"));
  row?.classList.add("active");
  try {
    const data = await api(`/api/use-cases?dataset_id=${id}`);
    $("#dataset-preview").innerHTML = renderPreview(data);
    if (data.kind === "synthetic") renderSyntheticUseCases(data.items, id);
    else renderCustomControls(data, id);
  } catch (e) {
    $("#dataset-preview").innerHTML = `<p class="form-error">${e.message}</p>`;
  }
}

function renderPreview(data) {
  if (data.profile) {
    const rows = data.profile.map(row => `
      <tr>
        <td>${escapeHtml(row.column)}</td>
        <td>${escapeHtml(row.type)}</td>
        <td>${escapeHtml(row.missing_pct)}%</td>
        <td>${escapeHtml(row.distinct)}</td>
      </tr>
    `).join("");

    return `
      <div class="overflow">
        <table class="profile-table">
          <thead><tr><th>Column</th><th>Type</th><th>Missing</th><th>Distinct</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  if (!data.preview?.length) return `<p class="empty-state">No preview rows available.</p>`;
  const cols = Object.keys(data.preview[0]);
  const headings = cols.slice(0, 7).map(column => `<th>${escapeHtml(column)}</th>`).join("");
  const rows = data.preview.slice(0, 12).map(row => {
    const cells = cols.slice(0, 7).map(column => `<td>${escapeHtml(row[column])}</td>`).join("");
    return `<tr>${cells}</tr>`;
  }).join("");

  return `
    <div class="overflow">
      <table class="profile-table">
        <thead><tr>${headings}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderModelDatasetOptions() {
  $("#model-dataset").innerHTML = state.datasets.map(d =>
    `<option value="${d.id}">${d.name}</option>`).join("");
}

async function renderSyntheticUseCases(items, id) {
  $("#synthetic-controls").classList.remove("hidden");
  $("#custom-controls").classList.add("hidden");
  $("#use-case-select").innerHTML = items.map(u =>
    `<option value="${u.id}" title="${u.description}">${u.name}</option>`).join("");
  $("#model-dataset").value = id;
}
async function renderCustomControls(data, id) {
  $("#synthetic-controls").classList.add("hidden");
  $("#custom-controls").classList.remove("hidden");
  $("#target-column").innerHTML = data.columns.map(c => `<option>${escapeHtml(c)}</option>`).join(
    "");
  $("#model-dataset").value = id;
  updatePositiveClass(data.preview, data.columns);
  updateTaskControls();
}
async function updatePositiveClass(preview, columns) {
  const col = $("#target-column").value;
  const vals = [...new Set(preview.map(r => r[col]).filter(v => v !== ""))];
  $("#positive-class").replaceChildren(...vals.map(v => new Option(String(v), String(v))));
}

function updateTaskControls() {
  const isClassification = $("#task-type").value === "classification";
  $("#positive-wrap").classList.toggle("hidden", !isClassification);
}

function navigate(page, updateUrl = true) {
  if (!$(`#${page}-page`)) return;
  $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.page === page));
  $$(".page").forEach(p => p.classList.remove("active"));
  $(`#${page}-page`).classList.add("active");
  const titles = {
    overview: ["Workspace overview", "Overview"],
    data: ["Data management", "Data"],
    analysis: ["SQL and data quality", "Analysis"],
    model: ["Machine learning", "Model"],
    predictions: ["Priorities and actions", "Predictions & actions"],
    monitoring: ["Model operations", "Monitoring"],
    reports: ["Management reporting", "Reports"],
    ai: ["AI workspace analyst", "AI analyst"],
    settings: ["Workspace profile", "Settings"]
  };
  $("#page-kicker").textContent = titles[page][0];
  $("#page-title").textContent = titles[page][1];
  if (updateUrl && location.hash !== `#${page}`) history.pushState({
    page
  }, "", `#${page}`);
  if (page === "predictions") loadPredictions();
  if (page === "analysis") loadAnalysis();
  if (page === "monitoring") loadMonitoring();
  if (page === "reports") loadReportSummary();
}

function syncPageFromUrl() {
  if (!state.me) return;
  const page = location.hash.slice(1);
  navigate($(`#${page}-page`) ? page : "overview", false)
}

function renderTable(columns, rows, className = "profile-table") {
  if (!rows?.length) return '<p class="empty-state">No results are available.</p>';
  const headings = columns.map(column => {
    const label = String(column).replaceAll("_", " ");
    return `<th>${escapeHtml(label)}</th>`;
  }).join("");
  const body = rows.map(row => {
    const cells = columns.map(column => `<td>${escapeHtml(row[column] ?? "—")}</td>`).join("");
    return `<tr>${cells}</tr>`;
  }).join("");

  return `
    <div class="overflow">
      <table class="${className}">
        <thead><tr>${headings}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `;
}

async function loadAnalysis() {
  const output = $("#analysis-results");
  output.innerHTML = '<p class="empty-state">Loading analysis…</p>';
  try {
    const data = await api("/api/analysis");
    $("#analysis-as-of").textContent = data.as_of ? `Data through ${data.as_of}` : "";
    $("[data-analysis-insight='kpi_explainer']").disabled = data.kind !== "relational";
    $("[data-analysis-insight='data_quality_brief']").disabled = data.kind !== "relational";
    if (data.kind !== "relational") {
      $("#analysis-kpis").innerHTML = "";
      output.innerHTML = `<p class="empty-state">${escapeHtml(data.message)}</p>`;
      return;
    }
    const kpis = data.sections.find(s => s.id === "kpi_summary")?.rows?.[0] || {};
    const labels = {
      active_customers: "Active customers",
      mrr_ghs: "Monthly recurring revenue (GHS)",
      arpu_ghs: "Average revenue per user (GHS)",
      churn_rate_90d_pct: "90-day churn rate (%)",
      net_adds_90d: "Net adds, last 90 days"
    };
    $("#analysis-kpis").innerHTML = Object.entries(labels).map(([key, label]) => {
      const value = kpis[key] == null
        ? "—"
        : Number(kpis[key]).toLocaleString(undefined, { maximumFractionDigits: 2 });

      return `
        <article class="kpi-card">
          <span>${label}</span>
          <strong>${escapeHtml(value)}</strong>
        </article>
      `;
    }).join("");
    const quality = data.quality || [];
    const issueCount = quality.filter(row => Number(row.issues) > 0).length;
    const sections = [`
      <section class="analysis-section">
        <div class="section-heading">
          <div><p>Data validation</p><h3>Quality checks</h3></div>
          <span>${issueCount} checks with issues</span>
        </div>
        ${renderTable(
          ["description", "table", "severity", "issues", "rows_checked", "issue_pct", "handling"],
          quality
        )}
      </section>
    `];

    data.sections.filter(section => section.id !== "kpi_summary").forEach(section => {
      sections.push(`
        <section class="analysis-section">
          <div class="section-heading">
            <div><p>SQL finding</p><h3>${escapeHtml(section.title)}</h3></div>
          </div>
          <p class="section-intro">${escapeHtml(section.question)}</p>
          ${renderTable(section.columns, section.rows.slice(0, 20))}
        </section>
      `);
    });

    output.innerHTML = sections.join("");
  } catch (error) {
    output.textContent = error.message;
  }
}

async function loadMonitoring() {
  const output = $("#monitoring-content");
  output.innerHTML = '<p class="empty-state">Loading monitoring summary…</p>';
  try {
    const data = await api("/api/monitoring");
    if (!data.available) {
      output.innerHTML = `<p class="empty-state">${escapeHtml(data.message)}</p>`;
      return;
    }
    const score = data.score_drift || {};
    const drivers = data.feature_drift || [];
    const cards = [
      ["Model", data.model || "—"],
      ["Scored snapshot", data.snapshot || "—"],
      ["Score PSI", score.psi == null ? "—" : Number(score.psi).toFixed(3)],
      ["Drift status", score.status || "Unknown"]
    ].map(([label, value]) => `
      <article class="kpi-card">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
      </article>
    `).join("");

    output.innerHTML = `
      <div class="kpi-grid">${cards}</div>
      <section class="analysis-section">
        <div class="section-heading">
          <div><p>Feature stability</p><h3>Training baseline comparison</h3></div>
        </div>
        ${renderTable(["feature", "psi", "status"], drivers)}
      </section>
      <p class="monitoring-note">
        PSI compares the current scoring distribution with the training reference. This is a
        baseline drift check; it does not measure live model accuracy because outcomes arrive later.
      </p>
    `;
  } catch (error) {
    output.textContent = error.message;
  }
}

function loadReportSummary() {
  const output = $("#report-summary");
  if (!state.model) {
    output.innerHTML = '<p class="empty-state">Train a model to prepare a business report.</p>';
    return;
  }
  const metrics = state.model.metrics || {};
  const entries = Object.entries(metrics).filter(([key]) => [
    "roc_auc", "pr_auc", "r2", "rmse", "mae", "recall_at_top", "precision_at_top", "lift_at_top"
  ].includes(key));
  const metricBoxes = entries.map(([key, value]) => {
    const label = key.replaceAll("_", " ");
    const displayValue = typeof value === "number" ? value.toFixed(3) : value;

    return `
      <div class="metric-box">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(displayValue)}</strong>
      </div>
    `;
  }).join("");

  output.innerHTML = `
    <div class="report-model">
      <div><span>Latest model</span><strong>${escapeHtml(state.model.model)}</strong></div>
      <div><span>Run</span><strong>${escapeHtml(state.model.run_id)}</strong></div>
    </div>
    <div class="metrics-grid">${metricBoxes}</div>
  `;
}

$$('.auth-tab').forEach(tab => tab.addEventListener('click', () => {
  $$('.auth-tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  $$('.auth-form').forEach(f => f.classList.remove('active'));
  $(`#${tab.dataset.authTab}-form`).classList.add('active')
}));

$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    state.me = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({
        email: f.get("email"),
        password: f.get("password")
      })
    });
    showApp();
    await refreshWorkspace();
    syncPageFromUrl();
  } catch (err) {
    $("#login-error").textContent = err.message
  }
});
$("#signup-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    state.me = await api("/api/signup", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(f))
    });
    showApp();
    await refreshWorkspace();
    syncPageFromUrl();
  } catch (err) {
    $("#signup-error").textContent = err.message
  }
});
$("#demo-button").addEventListener("click", async () => {
  try {
    state.me = await api("/api/demo", {
      method: "POST",
      body: '{"customers":8000}'
    });
    showApp();
    await refreshWorkspace();
    syncPageFromUrl();
  } catch (err) {
    $("#login-error").textContent = err.message
  }
});
$("#logout-button").addEventListener("click", async () => {
  await api("/api/logout", {
    method: "POST",
    body: "{}"
  });
  state = {
    me: null,
    workspace: null,
    datasets: [],
    model: null
  };
  showAuth();
});
$$('.nav-item').forEach(n => n.addEventListener("click", () => navigate(n.dataset.page)));
$$('[data-go]').forEach(b => b.addEventListener('click', () => navigate(b.dataset.go)));
window.addEventListener("popstate", syncPageFromUrl);
window.addEventListener("hashchange", syncPageFromUrl);

$("#settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const status = $("#settings-status"),
    button = $("#settings-form button[type=submit]");
  button.disabled = true;
  status.textContent = "Saving workspace settings…";
  try {
    await api("/api/workspace", {
      method: "PUT",
      body: JSON.stringify({
        name: $("#settings-name").value,
        country: $("#settings-country").value,
        industry: $("#settings-industry").value,
        objective: $("#settings-objective").value
      })
    });
    await refreshWorkspace();
    status.textContent = "Workspace settings saved."
  } catch (error) {
    status.textContent = error.message
  } finally {
    button.disabled = false
  }
});

$("#generate-demo").addEventListener("click", async () => {
  const b = $("#generate-demo");
  b.disabled = true;
  b.textContent = "Generating…";
  try {
    await api("/api/demo/data", {
      method: "POST",
      body: "{}"
    });
    await refreshWorkspace();
    navigate("data");
  } catch (e) {
    alert(e.message)
  } finally {
    b.disabled = false;
    b.textContent = "Generate demo data"
  }
});
$("#csv-upload").addEventListener("change", async e => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    $("#dataset-preview").innerHTML = "<p>Uploading…</p>";
    const res = await fetch("/api/datasets/upload", {
      method: "POST",
      body: fd,
      credentials: "same-origin"
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Upload failed.");
    await refreshWorkspace();
    navigate("data");
  } catch (err) {
    alert(err.message)
  }
});

$("#model-dataset").addEventListener("change", async e => {
  await selectDataset(Number(e.target.value), null)
});
$("#target-column").addEventListener("change", async () => {
  const id = Number($("#model-dataset").value);
  if (!id) return;
  const data = await api(`/api/use-cases?dataset_id=${id}`);
  updatePositiveClass(data.preview, data.columns)
});
$("#task-type").addEventListener("change", updateTaskControls);
$("#train-button").addEventListener("click", async () => {
  const id = Number($("#model-dataset").value);
  if (!id) return;
  const body = {
    dataset_id: id
  };
  if (!$("#synthetic-controls").classList.contains("hidden")) {
    body.use_case_id = $("#use-case-select").value
  } else {
    body.target_column = $("#target-column").value;
    body.task_type = $("#task-type").value;
    body.positive_class = $("#positive-class").value
  }
  $("#training-status").textContent = "Training and evaluating the model…";
  try {
    const out = await api("/api/model/train", {
      method: "POST",
      body: JSON.stringify(body)
    });
    await refreshWorkspace();
    const metrics = out.metrics || {};
    const metricNames = [
      "roc_auc", "pr_auc", "r2", "rmse", "mae", "recall_at_top", "precision_at_top", "lift_at_top"
    ];
    const metricCards = Object.entries(metrics)
      .filter(([key]) => metricNames.includes(key))
      .map(([key, value]) => {
        const label = key.replaceAll("_", " ");
        const displayValue = typeof value === "number" ? value.toFixed(3) : value;

        return `
          <div class="metric-box">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(displayValue)}</strong>
          </div>
        `;
      })
      .join("");
    const driverRows = (out.details?.drivers || []).slice(0, 10).map(driver => {
      const importance = Number(driver.importance);
      const width = Math.max(0, Math.min(100, importance * 100));

      return `
        <div class="driver-row">
          <span>${escapeHtml(driver.label)}</span>
          <div class="driver-bar"><div style="width: ${width}%"></div></div>
          <strong>${importance.toFixed(3)}</strong>
        </div>
      `;
    }).join("");

    $("#run-results").innerHTML = `
      <div class="metrics-grid">${metricCards}</div>
      <div class="driver-list">${driverRows}</div>
    `;
    $("#training-status").textContent = `Run ${out.run_id} complete — ${out.model}`;
  } catch (e) {
    $("#training-status").innerHTML = `<span class="form-error">${e.message}</span>`
  }
});

let predictionItems = [];
async function loadPredictions() {
  try {
    const data = await api("/api/predictions");
    predictionItems = data.items || [];
    $("#predictions-table").innerHTML = renderTable(predictionItems[0] ? Object.keys(
      predictionItems[0]).slice(0, 8) : [], predictionItems.slice(0, 100), "prediction-table")
  } catch (e) {
    $("#predictions-table").innerHTML = `<p class="empty-state">${escapeHtml(e.message)}</p>`
  }
}
$("#download-predictions").addEventListener("click", () => {
  if (!predictionItems.length) return;
  const cols = Object.keys(predictionItems[0]);
  const csv = [cols.join(","), ...predictionItems.map(row => cols.map(c =>
    `"${String(row[c]??"").replaceAll('"','""')}"`).join(","))].join("\n");
  const url = URL.createObjectURL(new Blob([csv], {
    type: "text/csv"
  }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "business-insights-predictions.csv";
  a.click();
  URL.revokeObjectURL(url)
});

$$('[data-insight]').forEach(button => button.addEventListener('click', async () => {
  const output = $("#model-insight");
  button.disabled = true;
  output.textContent = "Preparing a grounded insight from this model run…";
  try {
    const result = await api("/api/insights", {
      method: "POST",
      body: JSON.stringify({
        task: button.dataset.insight
      })
    });
    output.textContent =
      `${result.status === "verified" ? "Verified insight" : "Insight for review"}\n\n${result.text}`;
  } catch (error) {
    output.textContent = error.message;
  } finally {
    button.disabled = !state.model;
  }
}));

$$('[data-analysis-insight]').forEach(button => button.addEventListener('click', async () => {
  const output = $("#analysis-results");
  button.disabled = true;
  button.textContent = "Preparing insight…";
  try {
    const result = await api("/api/insights", {
      method: "POST",
      body: JSON.stringify({
        task: button.dataset.analysisInsight
      })
    });
    const card = document.createElement("article");
    card.className = "insight-card";
    card.textContent =
      `${result.status === "verified" ? "Verified insight" : "Insight for review"}\n${result.text}`;
    output.prepend(card);
  } catch (error) {
    const card = document.createElement("article");
    card.className = "insight-card error-card";
    card.textContent = error.message;
    output.prepend(card);
  } finally {
    button.textContent = button.dataset.analysisInsight === "kpi_explainer" ?
      "Explain KPI changes" : "Summarize data quality";
    button.disabled = false;
  }
}));

$("#refresh-monitoring").addEventListener("click", loadMonitoring);

$("#download-report").addEventListener("click", async () => {
  const button = $("#download-report");
  const status = $("#report-status");
  button.disabled = true;
  button.textContent = "Preparing report…";
  status.textContent = "Building the report and preparing grounded summaries…";
  try {
    const response = await fetch("/api/report", {
      method: "POST",
      credentials: "same-origin"
    });
    if (!response.ok) {
      let data = {};
      try {
        data = await response.json()
      } catch (_) {}
      throw new Error(data.detail || "Could not generate the report.")
    }
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "business-insights-report.pdf";
    a.click();
    URL.revokeObjectURL(url);
    status.textContent = "Your report is ready and has been downloaded.";
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Generate PDF report";
  }
});

$("#ask-button").addEventListener("click", async () => {
  const question = $("#ai-question").value.trim();
  if (!question) return;
  $("#ai-output").textContent = "Analyzing workspace results…";
  try {
    const out = await api("/api/insights", {
      method: "POST",
      body: JSON.stringify({
        task: "ask",
        question
      })
    });
    $("#ai-output").textContent =
      `${out.status === "verified" ? "Verified insight" : "Insight for review"}\n\n${out.text}`;
  } catch (e) {
    $("#ai-output").textContent = e.message
  }
});

boot();
