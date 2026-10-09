const ALLOWED_PATH = /^\/(?:api\/|health$)/;

class BackendClient {
  constructor(origin, fetchImpl = globalThis.fetch) {
    this.origin = origin;
    this.fetchImpl = fetchImpl;
  }

  async request(path, options = {}) {
    if (typeof path !== "string" || !ALLOWED_PATH.test(path)) {
      throw new Error("backend request path is not allowed");
    }
    const method = options.method || "GET";
    if (method !== "GET" && method !== "POST") {
      throw new Error("backend request method is not allowed");
    }
    const response = await this.fetchImpl(`${this.origin}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: method === "POST" ? options.body : undefined,
    });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `Backend request failed: ${response.status}`);
    }
    return payload;
  }

  async waitUntilReady(timeoutMs = 10000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try {
        await this.request("/health");
        return;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
    }
    throw new Error("cardiopump backend did not become ready");
  }
}

function startHeartbeat(client, intervalMs = 100) {
  let inFlight = false;
  const timer = setInterval(async () => {
    if (inFlight) {
      return;
    }
    inFlight = true;
    try {
      await client.request("/api/heartbeat", { method: "POST", body: "{}" });
    } catch {
      // The firmware owns heartbeat-loss handling. A failed request must not be retried in a tight loop.
    } finally {
      inFlight = false;
    }
  }, intervalMs);
  return () => clearInterval(timer);
}

module.exports = { BackendClient, startHeartbeat };
