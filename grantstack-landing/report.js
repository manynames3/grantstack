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
  project_id: "sample-columbus-semiconductor",
  status: "COMPLETED",
  completed_at: "2026-05-22T02:15:00Z",
  input_spec: {
    location: "Columbus, OH",
    facility_type: "Semiconductor supplier expansion",
    capex: 420000000,
    jobs: 180,
    average_wage: 92000,
    project_timeline: "Construction start before December 31, 2026",
    competing_locations: "IN, KY, Germany",
  },
  analysis_report: {
    eligibility_score: 84,
    confidence: "Screening only",
    summary:
      "The project appears worth deeper incentive diligence because the capital investment, semiconductor-supply-chain use case, Ohio location, and job creation profile map to federal, state, and local incentive categories. This sample is not advisor-reviewed; qualified-property basis, construction timing, ownership/controlled-entity status, site control, wage commitments, and agency discretion must be validated before relying on the result.",
    recommended_programs: [
      {
        name: "Section 48D Advanced Manufacturing Investment Credit",
        fit: "Validate",
        score: 84,
        category: "Federal semiconductor manufacturing tax credit",
        jurisdiction: "United States",
        why_it_matters:
          "Semiconductor manufacturing and semiconductor-equipment projects can create large tax-credit stakes, but qualified property, facility activity, construction timing, and controlled-entity rules require tax review.",
        source_url: "https://www.irs.gov/credits-deductions/advanced-manufacturing-investment-credit",
        diligence_questions: [
          "Which capital expenditures are qualified property integral to an advanced manufacturing facility?",
          "Can beginning of construction be established before the post-2026 termination rule applies?",
        ],
      },
      {
        name: "JobsOhio Economic Development Grant",
        fit: "High fit",
        score: 82,
        category: "Discretionary economic-development grant",
        jurisdiction: "Ohio",
        why_it_matters:
          "The fixed-asset investment, infrastructure needs, job creation, payroll, and competitive-location context justify early Ohio agency diligence if the project remains influenceable.",
        source_url: "https://www.jobsohio.com/images/incentives/jog-jobsohio-economic-development-grant-guidelines-final.pdf",
        diligence_questions: [
          "Is the Ohio site decision still competitive against the listed alternatives?",
          "What fixed-asset and infrastructure costs can be documented for reimbursement-style review?",
        ],
      },
      {
        name: "Ohio Enterprise Zone Program",
        fit: "Local review",
        score: 76,
        category: "Negotiated local property-tax incentive",
        jurisdiction: "Ohio / local",
        why_it_matters:
          "Large new investment may support local property-tax diligence, but zone status, local approvals, timing, school-board notice, and agreement terms are material.",
        source_url: "https://dam.assets.ohio.gov/image/upload/development.ohio.gov/business/stateincentives/ez_OhioEnterpriseZoneProgram.pdf",
        diligence_questions: [
          "Is the project site inside an active zone or eligible for a local abatement path?",
          "Has any work begun before required local agreements or notices?",
        ],
      },
    ],
    strengths: [
      "$420M projected capex creates material tax-credit and negotiated-incentive stakes.",
      "Semiconductor supply-chain activity maps to federal and state industrial-policy priorities.",
      "Competing locations create a plausible discretionary-incentive context if the site decision is still open.",
    ],
    risk_flags: [
      "Qualified-property basis has not been separated from non-qualifying costs.",
      "Beginning-of-construction evidence, site control, and local approval timing are not confirmed.",
      "Ownership, supply-chain, and controlled-entity diligence must be reviewed before CHIPS reliance.",
      "Award amounts, approvals, and negotiated terms remain subject to agency discretion.",
    ],
    rule_summary: {
      programs_checked: 3,
      passed_checks: 9,
      failed_checks: 0,
      unknown_checks: 6,
      blocking_failures: 0,
      material_unknowns: 6,
      review_status: "Advisor review required before reliance",
    },
    eligibility_checks: [
      {
        program_name: "Section 48D Advanced Manufacturing Investment Credit",
        jurisdiction: "United States",
        checks: [
          {
            label: "United States project location",
            status: "PASS",
            severity: "blocking",
            message: "Project location is in the United States.",
          },
          {
            label: "Beginning of construction before 2027",
            status: "UNKNOWN",
            severity: "material",
            message: "Construction timing was provided but documentary evidence has not been reviewed.",
          },
          {
            label: "Qualified property basis",
            status: "UNKNOWN",
            severity: "blocking",
            message: "Capex has not been split between potentially qualified and non-qualifying property.",
          },
          {
            label: "Controlled entity and supply-chain review",
            status: "UNKNOWN",
            severity: "material",
            message: "Ownership and supply-chain diligence requires qualified tax/legal review.",
          },
        ],
      },
      {
        program_name: "JobsOhio Economic Development Grant",
        jurisdiction: "Ohio",
        checks: [
          {
            label: "Ohio project location",
            status: "PASS",
            severity: "blocking",
            message: "Project location is in Ohio.",
          },
          {
            label: "Fixed-asset investment",
            status: "PASS",
            severity: "material",
            message: "Capex is large enough to justify deeper grant diligence.",
          },
          {
            label: "Job creation and wage context",
            status: "PASS",
            severity: "material",
            message: "Job count and average wage were provided for screening.",
          },
          {
            label: "Competitive location decision",
            status: "PASS",
            severity: "material",
            message: "Competing jurisdictions were provided.",
          },
        ],
      },
      {
        program_name: "Ohio Enterprise Zone Program",
        jurisdiction: "Ohio / local",
        checks: [
          {
            label: "Substantial local investment",
            status: "PASS",
            severity: "material",
            message: "Capex supports local property-tax diligence.",
          },
          {
            label: "Zone status and local approvals",
            status: "UNKNOWN",
            severity: "material",
            message: "Project site, local authority, school-board notice, and agreement timing need confirmation.",
          },
        ],
      },
    ],
    next_actions: [
      "Split capex into land, buildings, clean rooms, machinery, equipment, utility, and non-qualifying categories.",
      "Collect construction-start evidence, site-control documents, ownership charts, and supplier-risk materials.",
      "Validate official program rules with issuing agencies or qualified tax, legal, grants, and economic-development advisors.",
      "Prepare a reviewed incentive memo before using the opportunity in board materials or financial forecasts.",
    ],
    buyer_questions: [
      "Is the Ohio site still genuinely competitive with Indiana, Kentucky, or Germany?",
      "Which capital costs are already committed versus still influenceable?",
      "Who will own post-award reporting, job-count validation, wage commitments, and clawback monitoring?",
      "Which incentive assumptions are safe for the board model before expert review?",
    ],
    evidence_summary: [
      {
        program: "Section 48D Advanced Manufacturing Investment Credit",
        jurisdiction: "United States",
        evidence:
          "IRS guidance describes the advanced manufacturing investment credit for semiconductor and semiconductor-equipment manufacturing facilities, with qualified property and eligibility requirements.",
        source_url: "https://www.irs.gov/credits-deductions/advanced-manufacturing-investment-credit",
      },
      {
        program: "Section 48D beginning-of-construction rule",
        jurisdiction: "United States",
        evidence:
          "The eCFR construction-timing rule provides that the 48D credit does not apply if beginning of construction starts after December 31, 2026.",
        source_url: "https://www.law.cornell.edu/cfr/text/26/1.48D-5",
      },
      {
        program: "JobsOhio Economic Development Grant",
        jurisdiction: "Ohio",
        evidence:
          "JobsOhio guidance ties grant decisions to job creation, payroll, fixed-asset investment, project return on investment, and project location.",
        source_url: "https://www.jobsohio.com/images/incentives/jog-jobsohio-economic-development-grant-guidelines-final.pdf",
      },
      {
        program: "Ohio Enterprise Zone Program",
        jurisdiction: "Ohio / local",
        evidence:
          "Ohio enterprise-zone materials describe negotiated local tax exemptions on eligible new investments, subject to local agreements and timing requirements.",
        source_url: "https://dam.assets.ohio.gov/image/upload/development.ohio.gov/business/stateincentives/ez_OhioEnterpriseZoneProgram.pdf",
      },
    ],
    assumptions: [
      "Project inputs are representative and have not been independently validated.",
      "The company has not made irreversible site commitments before agency engagement.",
      "The project may qualify for further review but is not guaranteed funding, tax credits, abatements, or agency approval.",
      "The sample report does not include a tax opinion, legal opinion, grant application, lobbying activity, or agency negotiation.",
    ],
    validation_note:
      "This is a first-pass screening memo, not legal, tax, accounting, lobbying, grant-writing, or site-selection advice. Paid advisory use requires qualified human review, official source validation, current agency guidance, and agency confirmation before decisions, applications, public claims, board materials, tax filings, or financial forecasts.",
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
