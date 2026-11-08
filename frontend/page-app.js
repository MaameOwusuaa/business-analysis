const $ = (selector, root = document) => root.querySelector(selector);

const escapeHtml = value =>
  String(value ?? "").replace(/[&<>"']/g, character => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    };

    return entities[character];
  });

const industries = [
  "Any industry",
  "Agriculture",
  "Arts & Entertainment",
  "Construction",
  "Education",
  "Energy & Utilities",
  "Financial Services",
  "Healthcare & Life Sciences",
  "Hospitality & Tourism",
  "Insurance",
  "Legal & Professional Services",
  "Logistics & Transportation",
  "Manufacturing",
  "Nonprofit & Public Sector",
  "Real Estate",
  "Retail & E-commerce",
  "Software & Technology",
  "Telecommunications",
  "Other"
];

const page = document.body.dataset.page;
const predictions = [];
let me;
let workspace;
let datasets = [];
let model;

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };

  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers
  });

  let data;
  try {
    data = await response.json();
  } catch (_) {
    data = null;
  }

  if (!response.ok) {
    throw new Error(data?.detail || "The request could not be completed.");
  }

  return data;
}

function renderTable(columns, rows, className = "profile-table") {
  if (!rows?.length) {
    return '<p class="empty-state">No results are available.</p>';
  }

  const headings = columns
    .map(column => {
      const label = String(column).replaceAll("_", " ");
      return `<th>${escapeHtml(label)}</th>`;
    })
    .join("");

  const body = rows
    .map(row => {
      const cells = columns
        .map(column => `<td>${escapeHtml(row[column] ?? "—")}</td>`)
        .join("");

      return `<tr>${cells}</tr>`;
    })
    .join("");

  return `
    <div class="overflow">
      <table class="${className}">
        <thead><tr>${headings}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `;
}

function renderKpi(label, value) {
  return `
    <article class="kpi-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </article>
  `;
}

function renderNavigation() {
  const items = [
    ["overview", "⌂", "Overview"],
    ["data", "◫", "Data"],
    ["analysis", "▤", "Analysis"],
    ["model", "⌁", "Model"],
    ["predictions", "◎", "Predictions"],
    ["monitoring", "◷", "Monitoring"],
    ["reports", "▧", "Reports"],
    ["ai", "✦", "AI analyst"],
    ["settings", "⚙", "Workspace settings"]
  ];

  const links = items
    .map(([id, icon, label]) => {
      const active = page === id ? "active" : "";
      const href = `/static/pages/${id}.html`;

      return `
        <a class="nav-item ${active}" href="${href}">
          <span>${icon}</span>${label}
        </a>
      `;
    })
    .join("");

  $("#sidebar").innerHTML = `
    <div class="sidebar-brand">
      <div class="brand-mark small">BI</div>
      <span>Business Insights</span>
    </div>
    <nav class="nav-list">${links}</nav>
    <div class="workspace-card">
      <p>Workspace</p>
      <strong id="workspace-name">—</strong>
      <span id="workspace-industry">—</span>
    </div>
    <button id="logout-button" class="logout-button">Log out</button>
  `;

  $("#logout-button").addEventListener("click", async () => {
    try {
      await api("/api/logout", {
        method: "POST",
        body: "{}"
      });
    } finally {
      location.replace("/");
    }
  });
}

function renderWorkspace(data) {
  workspace = data.workspace;
  datasets = data.datasets || [];
  model = data.model;

  $("#workspace-name").textContent = workspace.name || "Workspace";
  $("#workspace-industry").textContent = workspace.industry || "Any industry";
  $("#user-name").textContent = me.full_name;
  $("#user-role").textContent = me.role;
  $("#user-avatar").textContent = me.full_name.charAt(0).toUpperCase();
}

function modelMetric(metrics = {}) {
  if (metrics.roc_auc != null) {
    return `ROC-AUC ${Number(metrics.roc_auc).toFixed(3)}`;
  }

  if (metrics.r2 != null) {
    return `R² ${Number(metrics.r2).toFixed(3)}`;
  }

  return "Evaluation complete";
}

async function initOverview() {
  const datasetCount = datasets.length;
  const modelType = model?.task_type || "—";
  const industry = workspace.industry || "Any industry";

  $("#objective-text").textContent = workspace.objective ||
    "Define an objective and connect your business data.";

  $("#overview-kpis").innerHTML = [
    renderKpi("Datasets", datasetCount),
    renderKpi("Latest model", model ? "Ready" : "Not trained"),
    renderKpi("Model type", modelType),
    renderKpi("Industry", industry)
  ].join("");

  const readinessItems = [
    ["Workspace configured", true],
    ["Data connected", datasetCount > 0],
    ["Model trained", Boolean(model)],
    ["Predictions generated", Boolean(model)]
  ];

  $("#readiness-list").innerHTML = readinessItems
    .map(([label, ready]) => {
      const statusClass = ready ? "" : "pending";
      const statusText = ready ? "Ready" : "Pending";

      return `
        <div class="readiness-row">
          <span>${label}</span>
          <span class="status-pill ${statusClass}">${statusText}</span>
        </div>
      `;
    })
    .join("");

  $("#model-summary").innerHTML = model
    ? `<strong>${escapeHtml(model.model)}</strong><br>${modelMetric(model.metrics)}<br>Run ${escapeHtml(model.run_id)}`
    : "No model trained yet.";
}

async function loadDatasetPreview(datasetId) {
  const data = await api(`/api/use-cases?dataset_id=${datasetId}`);
  const preview = $("#dataset-preview");

  if (data.profile) {
    const rows = data.profile.map(item => ({
      ...item,
      missing_pct: `${item.missing_pct}%`
    }));

    preview.innerHTML = renderTable(
      ["column", "type", "missing_pct", "distinct"],
      rows
    );
    return;
  }

  const columns = Object.keys(data.preview?.[0] || {}).slice(0, 7);
  preview.innerHTML = renderTable(columns, data.preview || []);
}

function renderDatasetList() {
  const list = $("#dataset-list");

  if (!datasets.length) {
    list.innerHTML =
      '<p class="empty-state">No datasets yet. Generate demo data or upload a CSV.</p>';
    return;
  }

  list.innerHTML = datasets
    .map((dataset, index) => {
      const active = index === 0 ? "active" : "";
      const type = dataset.source === "synthetic_telco" ? "Demo dataset" : "CSV upload";

      return `
        <div
          class="dataset-row ${active}"
          data-id="${Number(dataset.id)}"
          tabindex="0"
          role="button"
        >
          <strong>${escapeHtml(dataset.name)}</strong>
          <span>${type} · Dataset ${Number(dataset.id)}</span>
        </div>
      `;
    })
    .join("");

  list.querySelectorAll(".dataset-row").forEach(row => {
    const select = () => {
      list.querySelectorAll(".dataset-row").forEach(item => item.classList.remove("active"));
      row.classList.add("active");

      loadDatasetPreview(row.dataset.id).catch(error => {
        $("#dataset-preview").textContent = error.message;
      });
    };

    row.addEventListener("click", select);
    row.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select();
      }
    });
  });
}

async function initData() {
  renderDatasetList();

  if (datasets.length) {
    await loadDatasetPreview(datasets[0].id);
  }

  $("#generate-demo").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "Generating…";

    try {
      await api("/api/demo/data", {
        method: "POST",
        body: "{}"
      });
      location.reload();
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
      button.textContent = "Generate demo data";
    }
  });

  $("#csv-upload").addEventListener("change", async event => {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    try {
      await api("/api/datasets/upload", {
        method: "POST",
        body: formData
      });
      location.reload();
    } catch (error) {
      alert(error.message);
    }
  });
}

function selectedPositiveClass(preview, column) {
  const values = [...new Set(preview.map(row => row[column]).filter(value => value !== ""))];

  $("#positive-class").innerHTML = values
    .map(value => `<option>${escapeHtml(value)}</option>`)
    .join("");
}

async function initModel() {
  const datasetSelect = $("#model-dataset");

  datasetSelect.innerHTML = datasets
    .map(dataset => `<option value="${Number(dataset.id)}">${escapeHtml(dataset.name)}</option>`)
    .join("");

  if (!datasets.length) {
    $("#training-status").textContent = "Add a dataset before training a model.";
    return;
  }

  let selectedDataset;

  const updateTargetOptions = () => {
    if (!selectedDataset || selectedDataset.kind === "synthetic") return;

    selectedPositiveClass(selectedDataset.preview || [], $("#target-column").value);
  };

  const updateDatasetOptions = async () => {
    selectedDataset = await api(`/api/use-cases?dataset_id=${datasetSelect.value}`);

    const isSynthetic = selectedDataset.kind === "synthetic";
    $("#synthetic-controls").classList.toggle("hidden", !isSynthetic);
    $("#custom-controls").classList.toggle("hidden", isSynthetic);

    if (isSynthetic) {
      $("#use-case-select").innerHTML = selectedDataset.items
        .map(useCase => `<option value="${escapeHtml(useCase.id)}">${escapeHtml(useCase.name)}</option>`)
        .join("");
      return;
    }

    $("#target-column").innerHTML = selectedDataset.columns
      .map(column => `<option>${escapeHtml(column)}</option>`)
      .join("");
    updateTargetOptions();
  };

  datasetSelect.addEventListener("change", updateDatasetOptions);
  $("#target-column").addEventListener("change", updateTargetOptions);
  $("#task-type").addEventListener("change", () => {
    const isClassification = $("#task-type").value === "classification";
    $("#positive-wrap").classList.toggle("hidden", !isClassification);
  });

  await updateDatasetOptions();

  $("#train-button").addEventListener("click", async () => {
    const body = { dataset_id: Number(datasetSelect.value) };
    const isSynthetic = !$("#synthetic-controls").classList.contains("hidden");

    if (isSynthetic) {
      body.use_case_id = $("#use-case-select").value;
    } else {
      body.target_column = $("#target-column").value;
      body.task_type = $("#task-type").value;
      body.positive_class = $("#positive-class").value;
    }

    const status = $("#training-status");
    const button = $("#train-button");
    button.disabled = true;
    status.textContent = "Training and evaluating the model…";

    try {
      const result = await api("/api/model/train", {
        method: "POST",
        body: JSON.stringify(body)
      });

      model = {
        run_id: result.run_id,
        model: result.model,
        metrics: result.metrics
      };
      status.textContent = `Run ${result.run_id} complete — ${result.model}.`;
      $("#run-results").innerHTML = renderMetrics(result.metrics || {});
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}

function renderMetrics(metrics) {
  const entries = Object.entries(metrics).map(([key, value]) => {
    const label = key.replaceAll("_", " ");
    const displayValue = typeof value === "number" ? value.toFixed(3) : value;

    return `
      <div class="metric-box">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(displayValue)}</strong>
      </div>
    `;
  });

  return `<div class="metrics-grid">${entries.join("")}</div>`;
}

function downloadCsv(rows, filename) {
  if (!rows.length) return;

  const columns = Object.keys(rows[0]);
  const csvRows = rows.map(row =>
    columns
      .map(column => `"${String(row[column] ?? "").replaceAll('"', '""')}"`)
      .join(",")
  );
  const csv = [columns.join(","), ...csvRows].join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const link = document.createElement("a");

  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function initPredictions() {
  try {
    const data = await api("/api/predictions");
    predictions.push(...(data.items || []));

    const columns = predictions[0] ? Object.keys(predictions[0]).slice(0, 8) : [];
    $("#predictions-table").innerHTML = renderTable(
      columns,
      predictions.slice(0, 100),
      "prediction-table"
    );
  } catch (error) {
    $("#predictions-table").textContent = error.message;
  }

  $("#download-predictions").addEventListener("click", () => {
    downloadCsv(predictions, "business-insights-predictions.csv");
  });
}

async function initAnalysis() {
  const output = $("#analysis-results");

  try {
    const data = await api("/api/analysis");
    $("#analysis-as-of").textContent = data.as_of ? `Data through ${data.as_of}` : "";

    if (data.kind !== "relational") {
      output.innerHTML = `<p class="empty-state">${escapeHtml(data.message)}</p>`;
      return;
    }

    const kpis = data.sections.find(section => section.id === "kpi_summary")?.rows?.[0] || {};
    const labels = {
      active_customers: "Active customers",
      mrr_ghs: "Monthly recurring revenue (GHS)",
      arpu_ghs: "Average revenue per user (GHS)",
      churn_rate_90d_pct: "90-day churn rate (%)",
      net_adds_90d: "Net adds, last 90 days"
    };

    $("#analysis-kpis").innerHTML = Object.entries(labels)
      .map(([key, label]) => {
        const value = kpis[key] == null
          ? "—"
          : Number(kpis[key]).toLocaleString(undefined, { maximumFractionDigits: 2 });

        return renderKpi(label, value);
      })
      .join("");

    const qualityRows = data.quality || [];
    const issues = qualityRows.filter(row => Number(row.issues) > 0).length;
    const sections = [
      `<section class="analysis-section">
        <div class="section-heading">
          <div><p>Data validation</p><h3>Quality checks</h3></div>
          <span>${issues} checks with issues</span>
        </div>
        ${renderTable(
          ["description", "table", "severity", "issues", "rows_checked", "issue_pct", "handling"],
          qualityRows
        )}
      </section>`
    ];

    data.sections
      .filter(section => section.id !== "kpi_summary")
      .forEach(section => {
        sections.push(`<section class="analysis-section">
          <div class="section-heading">
            <div><p>SQL finding</p><h3>${escapeHtml(section.title)}</h3></div>
          </div>
          <p class="section-intro">${escapeHtml(section.question)}</p>
          ${renderTable(section.columns, section.rows.slice(0, 20))}
        </section>`);
      });

    output.innerHTML = sections.join("");
    addAnalysisInsightHandlers(output);
  } catch (error) {
    output.textContent = error.message;
  }
}

function addAnalysisInsightHandlers(output) {
  document.querySelectorAll("[data-analysis-insight]").forEach(button => {
    button.addEventListener("click", async () => {
      button.disabled = true;

      try {
        const result = await api("/api/insights", {
          method: "POST",
          body: JSON.stringify({ task: button.dataset.analysisInsight })
        });
        const card = document.createElement("article");

        card.className = "insight-card";
        card.textContent = `${result.status}\n${result.text}`;
        output.prepend(card);
      } catch (error) {
        const card = document.createElement("article");

        card.className = "insight-card error-card";
        card.textContent = error.message;
        output.prepend(card);
      } finally {
        button.disabled = false;
      }
    });
  });
}

async function initMonitoring() {
  const output = $("#monitoring-content");

  const loadMonitoring = async () => {
    output.innerHTML = '<p class="empty-state">Loading monitoring summary…</p>';

    try {
      const data = await api("/api/monitoring");
      if (!data.available) {
        output.innerHTML = `<p class="empty-state">${escapeHtml(data.message)}</p>`;
        return;
      }

      const scoreDrift = data.score_drift || {};
      const cards = [
        renderKpi("Model", data.model || "—"),
        renderKpi("Scored snapshot", data.snapshot || "—"),
        renderKpi("Score PSI", scoreDrift.psi == null ? "—" : Number(scoreDrift.psi).toFixed(3)),
        renderKpi("Drift status", scoreDrift.status || "Unknown")
      ];

      output.innerHTML = `
        <div class="kpi-grid">${cards.join("")}</div>
        <section class="analysis-section">
          <div class="section-heading">
            <div><p>Feature stability</p><h3>Training baseline comparison</h3></div>
          </div>
          ${renderTable(["feature", "psi", "status"], data.feature_drift || [])}
        </section>
        <p class="monitoring-note">
          PSI compares the current scoring distribution with the training reference. It does not
          measure live model accuracy because outcomes arrive later.
        </p>
      `;
    } catch (error) {
      output.textContent = error.message;
    }
  };

  $("#refresh-monitoring").addEventListener("click", loadMonitoring);
  await loadMonitoring();
}

function initReports() {
  const summary = $("#report-summary");
  const button = $("#download-report");

  if (!model) {
    summary.innerHTML = '<p class="empty-state">Train a model to prepare a business report.</p>';
    button.disabled = true;
    return;
  }

  const metrics = renderMetrics(model.metrics || {});
  summary.innerHTML = `
    <div class="report-model">
      <div><span>Latest model</span><strong>${escapeHtml(model.model)}</strong></div>
      <div><span>Run</span><strong>${escapeHtml(model.run_id)}</strong></div>
    </div>
    ${metrics}
  `;

  button.addEventListener("click", async () => {
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
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || "Report generation failed.");
      }

      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");

      link.href = url;
      link.download = "business-insights-report.pdf";
      link.click();
      URL.revokeObjectURL(url);
      status.textContent = "Your report is ready and has been downloaded.";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
      button.textContent = "Generate PDF report";
    }
  });
}

function initAiAnalyst() {
  $("#ask-button").addEventListener("click", async () => {
    const question = $("#ai-question").value.trim();
    if (!question) return;

    const output = $("#ai-output");
    output.textContent = "Analyzing workspace results…";

    try {
      const result = await api("/api/insights", {
        method: "POST",
        body: JSON.stringify({ task: "ask", question })
      });
      const label = result.status === "verified" ? "Verified insight" : "Insight for review";

      output.textContent = `${label}\n\n${result.text}`;
    } catch (error) {
      output.textContent = error.message;
    }
  });
}

function initSettings() {
  $("#settings-name").value = workspace.name || "";
  $("#settings-country").value = workspace.country || "";
  $("#settings-user").textContent = me.full_name;
  $("#settings-email").textContent = me.email;
  $("#settings-role").textContent = `${me.role} · ${workspace.country || "Global"}`;

  $("#settings-industry").innerHTML = industries
    .map(industry => `<option>${escapeHtml(industry)}</option>`)
    .join("");
  $("#settings-industry").value = workspace.industry || "Any industry";
  $("#settings-objective").value = workspace.objective || "";

  $("#settings-form").addEventListener("submit", async event => {
    event.preventDefault();

    const status = $("#settings-status");
    const button = $("#settings-form button[type=submit]");
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
      status.textContent = "Workspace settings saved.";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}

const pageInitializers = {
  overview: initOverview,
  data: initData,
  analysis: initAnalysis,
  model: initModel,
  predictions: initPredictions,
  monitoring: initMonitoring,
  reports: initReports,
  ai: initAiAnalyst,
  settings: initSettings
};

async function boot() {
  renderNavigation();

  try {
    me = await api("/api/me");
    const data = await api("/api/workspace");

    renderWorkspace(data);

    if (pageInitializers[page]) {
      await pageInitializers[page]();
    }
  } catch (_) {
    location.replace("/");
  }
}

boot();
