const API_BASE_URL = "https://rx967db2q9.execute-api.us-east-1.amazonaws.com";

const params = new URLSearchParams(window.location.search);
const projectId = params.get("project_id");
const token = params.get("token");
const statusElement = document.querySelector("#report-status");
const reportCard = document.querySelector("#report-card");
const isSampleReport = params.get("sample") === "true";

let pollCount = 0;
const maxPolls = 60;
let latestCompletedPayload = null;
let pendingTracked = false;
let completedTracked = false;

const SAMPLE_REPORT_PAYLOAD = {
  project_id: "sample-raleigh-manufacturing",
  status: "COMPLETED",
  completed_at: "2026-05-22T02:15:00Z",
  input_spec: {
    location: "Raleigh, NC",
    facility_type: "Advanced manufacturing",
    capex: 12500000,
    jobs: 82,
    average_wage: 72000,
    project_timeline: "Site decision in 90 days",
    competing_locations: "SC, TN, Mexico",
  },
  analysis_report: {
    eligibility_score: 82,
    confidence: "Screening only",
    summary:
      "The project appears worth deeper incentive diligence because the job count, capital investment, and advanced-manufacturing use case align with common North Carolina economic-development and workforce-support categories. The report is not advisor-reviewed; wage, county-tier, timing, site-control, and agency-discretion details should be validated before relying on the result.",
    recommended_programs: [
      {
        name: "Job Development Investment Grant",
        fit: "High fit",
        score: 86,
        category: "Discretionary job creation incentive",
        jurisdiction: "North Carolina",
        why_it_matters:
          "Competitive job creation and investment projects can justify early agency outreach when wage, county, and timing facts support eligibility.",
        source_url: "https://edpnc.com/incentives/job-development-investment-grant/",
        diligence_questions: [
          "Are the jobs net-new and above the county wage threshold?",
          "Is the project competitive with another state or country?",
        ],
      },
      {
        name: "One North Carolina Fund",
        fit: "Medium fit",
        score: 74,
        category: "Discretionary cash grant",
        jurisdiction: "North Carolina",
        why_it_matters:
          "A fast-moving expansion with local participation may warrant review, but award availability and local match requirements need confirmation.",
        source_url: "https://www.commerce.nc.gov/grants-incentives/one-north-carolina-fund",
        diligence_questions: [
          "Will the locality participate in a matching incentive?",
          "Does the hiring timeline match program requirements?",
        ],
      },
      {
        name: "NCWorks Customized Training",
        fit: "Validate",
        score: 68,
        category: "Workforce training",
        jurisdiction: "North Carolina",
        why_it_matters:
          "Training support may fit if the hiring plan includes new skills, onboarding, or incumbent-worker upskilling tied to the expansion.",
        source_url: "https://www.nccommunitycolleges.edu/business-and-industry/customized-training/",
        diligence_questions: [
          "What roles require training support?",
          "Which community college partner would support delivery?",
        ],
      },
    ],
    strengths: [
      "Meaningful capex and job creation support an incentive conversation.",
      "Advanced manufacturing maps to common state economic-development priorities.",
      "Competing locations create a plausible discretionary-incentive context.",
    ],
    risk_flags: [
      "County-tier and wage thresholds are not confirmed.",
      "Site control, final hiring timeline, and local match details are missing.",
      "Award amounts and eligibility remain subject to agency discretion.",
    ],
    rule_summary: {
      programs_checked: 3,
      passed_checks: 8,
      failed_checks: 0,
      unknown_checks: 4,
      blocking_failures: 0,
      material_unknowns: 4,
      review_status: "Needs missing facts before advisory use",
    },
    eligibility_checks: [
      {
        program_name: "North Carolina Job Development Investment Grant",
        jurisdiction: "North Carolina",
        checks: [
          {
            label: "North Carolina project location",
            status: "PASS",
            severity: "blocking",
            message: "Project location is in North Carolina.",
          },
          {
            label: "Competitive job creation",
            status: "PASS",
            severity: "material",
            message: "Job creation clears the GrantStack JDIG screening threshold.",
          },
          {
            label: "Competitive location decision",
            status: "PASS",
            severity: "blocking",
            message: "Competing-location context was provided.",
          },
          {
            label: "Average wage context",
            status: "PASS",
            severity: "material",
            message: "Average wage was provided for county-wage diligence.",
          },
        ],
      },
      {
        program_name: "One North Carolina Fund",
        jurisdiction: "North Carolina",
        checks: [
          {
            label: "North Carolina project location",
            status: "PASS",
            severity: "blocking",
            message: "Project location is in North Carolina.",
          },
          {
            label: "Local participation",
            status: "UNKNOWN",
            severity: "material",
            message: "OneNC diligence should confirm local participation or matching support.",
          },
        ],
      },
    ],
    next_actions: [
      "Confirm county, wage, NAICS/activity, site-control status, and hiring schedule.",
      "Validate official program rules with the issuing agencies or qualified advisors.",
      "Prepare a reviewed incentive memo before representing savings to finance leadership.",
    ],
    buyer_questions: [
      "Is the project truly competitive with another jurisdiction?",
      "Which costs are already committed versus still influenceable?",
      "Who owns compliance tracking if an award is pursued?",
    ],
    evidence_summary: [
      {
        program: "Job Development Investment Grant",
        jurisdiction: "North Carolina",
        evidence:
          "JDIG is positioned for competitive job-creation and investment projects, but eligibility depends on project-specific thresholds and approval.",
        source_url: "https://edpnc.com/incentives/job-development-investment-grant/",
      },
      {
        program: "One North Carolina Fund",
        jurisdiction: "North Carolina",
        evidence:
          "OneNC is a discretionary grant program used for competitive projects and typically requires local participation.",
        source_url: "https://www.commerce.nc.gov/grants-incentives/one-north-carolina-fund",
      },
    ],
    assumptions: [
      "Project inputs are representative and have not been independently validated.",
      "The company has not made irreversible site commitments before agency engagement.",
      "The project may qualify for further review but is not guaranteed funding.",
    ],
    validation_note:
      "This is a first-pass screening memo, not legal, tax, accounting, lobbying, grant-writing, or site-selection advice. Paid advisory use requires qualified human review, official source validation, and agency confirmation before decisions, applications, public claims, or financial forecasts.",
  },
};

if (isSampleReport) {
  renderCompleted(SAMPLE_REPORT_PAYLOAD, { sample: true });
  trackEvent("sample_report_view");
} else if (!projectId || !token) {
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
      trackEvent("report_failed", { project_id: projectId });
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
    trackEvent("report_error", { project_id: projectId });
    renderError(error instanceof Error ? error.message : "Unexpected report loading failure.");
  }
}

function renderPending(payload) {
  statusElement.textContent = `Status: ${payload.status || "PROCESSING"}`;
  if (!pendingTracked) {
    pendingTracked = true;
    trackEvent("report_pending", { project_id: projectId, status: payload.status || "PROCESSING" });
  }
  reportCard.innerHTML = `
    <div class="loading-block">
      <strong>Analysis in progress</strong>
      <span>GrantStack accepted the project and is preparing the report. This page refreshes automatically.</span>
    </div>
    ${renderSpec(payload.input_spec || {})}
  `;
}

function renderCompleted(payload, options = {}) {
  const report = payload.analysis_report || {};
  const spec = payload.input_spec || {};
  latestCompletedPayload = payload;
  statusElement.textContent = options.sample
    ? "Sample report, representative output only"
    : `Completed ${formatDate(payload.completed_at)}`;
  if (!completedTracked) {
    completedTracked = true;
    trackEvent(options.sample ? "sample_report_rendered" : "report_completed", {
      project_id: payload.project_id || projectId || "sample",
    });
  }
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
    <section class="review-banner">
      <strong>Review status: Automated screen only</strong>
      <p>
        This report has not been reviewed by an advisor. Treat it as an evidence packet for diligence, not as a final
        eligibility opinion or application recommendation.
      </p>
    </section>
    <section class="memo-section">
      <h2>Executive summary</h2>
      <p>${escapeHtml(report.summary || "No summary returned.")}</p>
    </section>
    ${renderSpec(spec)}
    ${renderEligibilityChecks(report.rule_summary, report.eligibility_checks)}
    ${renderPrograms(report.recommended_programs)}
    ${renderList("Strengths", report.strengths)}
    ${renderList("Risk flags", report.risk_flags)}
    ${renderList("Next actions", report.next_actions)}
    ${renderList("Buyer questions", report.buyer_questions)}
    ${renderEvidence(report.evidence_summary)}
    ${renderList("Assumptions", report.assumptions)}
    ${renderReviewWorkflow()}
    <section class="memo-section disclaimer">
      <h2>Advisory disclaimer</h2>
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

function renderEligibilityChecks(summary, checks) {
  const programs = Array.isArray(checks) && checks.length ? checks : [];
  if (!programs.length && !summary) return "";
  const safeSummary = summary && typeof summary === "object" ? summary : {};

  return `
    <section class="memo-section">
      <h2>Eligibility rule checks</h2>
      <div class="rule-summary-grid">
        ${renderRuleMetric("Programs checked", safeSummary.programs_checked)}
        ${renderRuleMetric("Passed", safeSummary.passed_checks)}
        ${renderRuleMetric("Failed", safeSummary.failed_checks)}
        ${renderRuleMetric("Unknown", safeSummary.unknown_checks)}
      </div>
      ${safeSummary.review_status ? `<p class="rule-review-status">${escapeHtml(safeSummary.review_status)}</p>` : ""}
      ${
        programs.length
          ? `<div class="eligibility-list">
              ${programs.map(renderEligibilityProgram).join("")}
            </div>`
          : ""
      }
    </section>
  `;
}

function renderRuleMetric(label, value) {
  return `
    <div>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(value ?? 0))}</strong>
    </div>
  `;
}

function renderEligibilityProgram(program) {
  const checks = Array.isArray(program.checks) ? program.checks : [];
  return `
    <article class="eligibility-program">
      <div>
        <strong>${escapeHtml(program.program_name || "Program")}</strong>
        <span>${escapeHtml(program.jurisdiction || "")}</span>
      </div>
      <ul>
        ${checks.map(renderEligibilityCheck).join("")}
      </ul>
    </article>
  `;
}

function renderEligibilityCheck(check) {
  const status = String(check.status || "UNKNOWN").toLowerCase();
  return `
    <li>
      <span class="rule-status rule-status-${escapeHtml(status)}">${escapeHtml(check.status || "UNKNOWN")}</span>
      <div>
        <strong>${escapeHtml(check.label || "Eligibility check")}</strong>
        <p>${escapeHtml(check.message || "Needs validation.")}</p>
      </div>
    </li>
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

function renderReviewWorkflow() {
  const steps = [
    ["Automated screen", "Generated from project inputs and the current source catalog."],
    ["Fact validation", "Confirm wage, county, job definitions, timing, site control, and competing-location context."],
    ["Advisor review", "Have a qualified advisor review program fit, compliance risk, and application strategy."],
    ["Agency confirmation", "Confirm final eligibility, award terms, deadlines, and documentation with issuing agencies."],
  ];

  return `
    <section class="memo-section">
      <h2>Review workflow before paid advisory use</h2>
      <div class="review-workflow">
        ${steps
          .map(
            ([title, body], index) => `
              <article>
                <span>${String(index + 1).padStart(2, "0")}</span>
                <strong>${escapeHtml(title)}</strong>
                <p>${escapeHtml(body)}</p>
              </article>
            `,
          )
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
  document.querySelector("#print-report")?.addEventListener("click", () => {
    trackEvent("report_print", { project_id: latestCompletedPayload?.project_id || projectId || "sample" });
    window.print();
  });
  document.querySelector("#download-report")?.addEventListener("click", () => {
    if (!latestCompletedPayload) return;
    trackEvent("report_download_json", { project_id: latestCompletedPayload.project_id || projectId || "sample" });
    const blob = new Blob([JSON.stringify(latestCompletedPayload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `grantstack-${latestCompletedPayload.project_id || projectId || "sample"}.json`;
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

function trackEvent(eventName, properties = {}) {
  if (typeof window.trackGrantStackEvent === "function") {
    window.trackGrantStackEvent(eventName, properties);
  }
}
