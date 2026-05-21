const API_ENDPOINT = "https://rx967db2q9.execute-api.us-east-1.amazonaws.com/projects";

const form = document.querySelector("#project-form");
const statusElement = document.querySelector("#form-status");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusElement.className = "form-status";
  statusElement.textContent = "Submitting project for analysis...";

  const formData = new FormData(form);
  const payload = {
    location: String(formData.get("location") || "").trim(),
    facility_type: String(formData.get("facility_type") || "").trim(),
    capex: Number(formData.get("capex")),
    jobs: Number(formData.get("jobs")),
    contact_email: String(formData.get("contact_email") || "").trim(),
    metadata: {
      source: "cloudflare-pages-landing",
      submitted_at: new Date().toISOString(),
    },
  };
  addOptionalText(payload, formData, "company_name");
  addOptionalText(payload, formData, "project_timeline");
  addOptionalText(payload, formData, "competing_locations");
  addOptionalNumber(payload, formData, "average_wage");

  try {
    const response = await fetch(API_ENDPOINT, {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const responseBody = await response.json().catch(() => ({}));

    if (!response.ok) {
      const message = responseBody.message || "The project could not be queued.";
      throw new Error(message);
    }

    statusElement.className = "form-status success";
    statusElement.textContent = "Accepted. Opening your private report page...";
    form.reset();
    const reportUrl = `/report.html?project_id=${encodeURIComponent(responseBody.project_id)}&token=${encodeURIComponent(
      responseBody.access_token,
    )}`;
    window.location.assign(reportUrl);
  } catch (error) {
    statusElement.className = "form-status error";
    statusElement.textContent = error instanceof Error ? error.message : "Unexpected submission failure.";
  }
});

function addOptionalText(payload, formData, fieldName) {
  const value = String(formData.get(fieldName) || "").trim();
  if (value) {
    payload[fieldName] = value;
  }
}

function addOptionalNumber(payload, formData, fieldName) {
  const rawValue = String(formData.get(fieldName) || "").trim();
  if (!rawValue) return;
  const value = Number(rawValue);
  if (Number.isFinite(value) && value > 0) {
    payload[fieldName] = value;
  }
}
