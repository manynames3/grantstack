const API_BASE_URL = "https://rx967db2q9.execute-api.us-east-1.amazonaws.com";

const params = new URLSearchParams(window.location.search);
const projectId = params.get("project_id");
const token = params.get("token");
const statusElement = document.querySelector("#report-status");
const reportCard = document.querySelector("#report-card");

let pollCount = 0;
const maxPolls = 60;
let latestCompletedPayload = null;

if (!projectId || !token) {
  renderError("This report link is missing a project ID or access token.");
} else {
  fetchReport();
}

async function fetchReport() {
  try {
    const response = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(projectId)}?token=${encodeURIComponent(token)}`, {
      headers: {
        accept: "application/json",
      },
    });
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(payload.message || "Report could not be loaded.");
    }

    if (payload.status === "COMPLETED") {
      renderCompleted(payload);
      return;
    }

    if (payload.status === "FAILED") {
      renderError(payload.failure?.message || "Project processing failed.");
      return;
    }

    renderPending(payload);
    pollCount += 1;
    if (pollCount < maxPolls) {
      window.setTimeout(fetchReport, 3000);
    } else {
      renderError("The report is taking longer than expected. Keep this private link and refresh shortly.");
    }
  } catch (error) {
    renderError(error instanceof Error ? error.message : "Unexpected report loading failure.");
  }
}

function renderPending(payload) {
  statusElement.textContent = `Status: ${payload.status || "PROCESSING"}`;
  reportCard.innerHTML = `
    <div class="loading-block">
      <strong>Analysis in progress</strong>
      <span>GrantStack accepted the project and is preparing the report. This page refreshes automatically.</span>
    </div>
    ${renderSpec(payload.input_spec || {})}
  `;
}

function renderCompleted(payload) {
  const report = payload.analysis_report || {};
  const spec = payload.input_spec || {};
  latestCompletedPayload = payload;
  statusElement.textContent = `Completed ${formatDate(payload.completed_at)}`;
  reportCard.innerHTML = `
    <div class="report-actions" aria-label="Report actions">
      <button class="button button-secondary report-button" type="button" id="print-report">Print</button>
      <button class="button button-secondary report-button" type="button" id="download-report">Download JSON</button>
    </div>
    <div class="completed-banner">
      <span>
        <strong>${escapeHtml(String(report.eligibility_score ?? "N/A"))}</strong>
        <em>Eligibility score</em>
      </span>
      <span>
        <strong>${escapeHtml(report.confidence || "Screening")}</strong>
        <em>Confidence</em>
      </span>
    </div>
    <section class="memo-section">
      <h2>Executive summary</h2>
      <p>${escapeHtml(report.summary || "No summary returned.")}</p>
    </section>
    ${renderSpec(spec)}
    ${renderPrograms(report.recommended_programs)}
    ${renderList("Strengths", report.strengths)}
    ${renderList("Risk flags", report.risk_flags)}
    ${renderList("Next actions", report.next_actions)}
    ${renderList("Buyer questions", report.buyer_questions)}
    ${renderEvidence(report.evidence_summary)}
    ${renderList("Assumptions", report.assumptions)}
    <section class="memo-section disclaimer">
      <h2>Validation note</h2>
      <p>${escapeHtml(report.validation_note || "This is a first-pass screening memo. Program rules, award availability, eligibility, timing, and local discretion must be validated before decisions or applications.")}</p>
    </section>
  `;
  wireReportActions();
}

function renderSpec(spec) {
  return `
    <section class="memo-section">
      <h2>Project inputs</h2>
      <div class="spec-grid">
        <div><span>Location</span><strong>${escapeHtml(spec.location || "Not provided")}</strong></div>
        <div><span>Facility</span><strong>${escapeHtml(spec.facility_type || "Not provided")}</strong></div>
        <div><span>Capex</span><strong>${formatCurrency(spec.capex)}</strong></div>
        <div><span>Jobs</span><strong>${escapeHtml(String(spec.jobs ?? "Not provided"))}</strong></div>
        ${renderOptionalSpec("Avg. wage", spec.average_wage ? formatCurrency(spec.average_wage) : "")}
        ${renderOptionalSpec("Timeline", spec.project_timeline)}
        ${renderOptionalSpec("Competing sites", spec.competing_locations)}
        ${renderOptionalSpec("Site control", spec.site_control)}
      </div>
    </section>
  `;
}

function renderOptionalSpec(label, value) {
  if (!value) return "";
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`;
}

function renderPrograms(programs) {
  const items = Array.isArray(programs) && programs.length ? programs : [];
  if (!items.length) {
    return renderList("Recommended program categories", []);
  }

  return `
    <section class="memo-section">
      <h2>Recommended programs</h2>
      <div class="program-grid">
        ${items.map(renderProgramCard).join("")}
      </div>
    </section>
  `;
}

function renderProgramCard(program) {
  const sourceUrl = safeUrl(program.source_url);
  const diligenceQuestions = Array.isArray(program.diligence_questions) ? program.diligence_questions : [];
  return `
    <article class="program-card">
      <div class="program-card-top">
        <span>${escapeHtml(program.fit || "Screen")}</span>
        <strong>${escapeHtml(String(program.score ?? ""))}${program.score ? "%" : ""}</strong>
      </div>
      <h3>${escapeHtml(program.name || "Program")}</h3>
      <p>${escapeHtml(program.why_it_matters || program.source_note || "Validate fit with the issuing agency.")}</p>
      <dl>
        <div><dt>Category</dt><dd>${escapeHtml(program.category || "Incentive strategy")}</dd></div>
        <div><dt>Jurisdiction</dt><dd>${escapeHtml(program.jurisdiction || "Unspecified")}</dd></div>
      </dl>
      ${diligenceQuestions.length ? `<ul>${diligenceQuestions.map((item) => `<li>${escapeHtml(String(item))}</li>`).join("")}</ul>` : ""}
      ${sourceUrl ? `<a class="source-link" href="${sourceUrl}" target="_blank" rel="noreferrer">Official source</a>` : ""}
    </article>
  `;
}

function renderList(title, values) {
  const items = Array.isArray(values) && values.length ? values : ["No items returned."];
  return `
    <section class="memo-section">
      <h2>${escapeHtml(title)}</h2>
      <ul class="memo-list">
        ${items.map((item) => `<li>${escapeHtml(String(item))}</li>`).join("")}
      </ul>
    </section>
  `;
}

function renderEvidence(values) {
  const items = Array.isArray(values) && values.length ? values : [];
  if (!items.length) return "";

  return `
    <section class="memo-section">
      <h2>Evidence used</h2>
      <div class="evidence-list">
        ${items
          .map((item) => {
            const sourceUrl = safeUrl(item.source_url);
            return `
              <article>
                <strong>${escapeHtml(item.program || "Source")}</strong>
                <span>${escapeHtml(item.jurisdiction || "")}</span>
                <p>${escapeHtml(item.evidence || "")}</p>
                ${sourceUrl ? `<a class="source-link" href="${sourceUrl}" target="_blank" rel="noreferrer">Review source</a>` : ""}
              </article>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
}

function renderError(message) {
  latestCompletedPayload = null;
  statusElement.textContent = "Report unavailable";
  reportCard.innerHTML = `
    <div class="error-block">
      <strong>Something needs attention</strong>
      <span>${escapeHtml(message)}</span>
    </div>
  `;
}

function wireReportActions() {
  document.querySelector("#print-report")?.addEventListener("click", () => window.print());
  document.querySelector("#download-report")?.addEventListener("click", () => {
    if (!latestCompletedPayload) return;
    const blob = new Blob([JSON.stringify(latestCompletedPayload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `grantstack-${projectId}.json`;
    document.body.appendChild(link);
    link.click();
    URL.revokeObjectURL(link.href);
    link.remove();
  });
}

function formatCurrency(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "Not provided";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(numeric);
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(String(value));
    if (url.protocol !== "https:") return "";
    return escapeHtml(url.href);
  } catch {
    return "";
  }
}
