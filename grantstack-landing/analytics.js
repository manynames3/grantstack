const ANALYTICS_ENDPOINT = "https://rx967db2q9.execute-api.us-east-1.amazonaws.com/analytics";
const SESSION_STORAGE_KEY = "grantstack_session_id";

window.trackGrantStackEvent = function trackGrantStackEvent(eventName, properties = {}) {
  if (!eventName || typeof eventName !== "string") return;

  const payload = {
    event_name: eventName,
    page_path: window.location.pathname + window.location.search,
    page_title: document.title,
    referrer: document.referrer,
    session_id: getSessionId(),
    properties,
  };
  const body = JSON.stringify(payload);

  try {
    if (navigator.sendBeacon) {
      const blob = new Blob([body], { type: "application/json" });
      if (navigator.sendBeacon(ANALYTICS_ENDPOINT, blob)) return;
    }
  } catch {
    // Analytics should never block the product workflow.
  }

  fetch(ANALYTICS_ENDPOINT, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
    keepalive: true,
  }).catch(() => undefined);
};

document.addEventListener("DOMContentLoaded", () => {
  window.trackGrantStackEvent("page_view", {
    sample_report: new URLSearchParams(window.location.search).get("sample") === "true",
  });

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-analytics-event]") : null;
    if (!target) return;

    window.trackGrantStackEvent(target.dataset.analyticsEvent, {
      label: target.dataset.analyticsLabel || target.textContent.trim().slice(0, 80),
      href: target instanceof HTMLAnchorElement ? target.getAttribute("href") || "" : "",
    });
  });
});

function getSessionId() {
  try {
    const existingId = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (existingId) return existingId;
    const nextId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, nextId);
    return nextId;
  } catch {
    return "session-unavailable";
  }
}
