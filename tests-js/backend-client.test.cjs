const assert = require("node:assert/strict");
const test = require("node:test");

const { BackendClient, startHeartbeat } = require("../electron/backend-client.cjs");

test("backend client restricts renderer requests to the local API", async () => {
  const client = new BackendClient("http://127.0.0.1:8765", async () => {
    throw new Error("fetch should not run");
  });

  await assert.rejects(() => client.request("https://example.com"), /not allowed/);
  await assert.rejects(() => client.request("/api/start", { method: "DELETE" }), /not allowed/);
});

test("backend client returns JSON and preserves backend errors", async () => {
  const okClient = new BackendClient("http://127.0.0.1:8765", async () => ({
    ok: true,
    json: async () => ({ ok: true, value: 7 }),
  }));
  assert.deepEqual(await okClient.request("/api/telemetry"), { ok: true, value: 7 });

  const errorClient = new BackendClient("http://127.0.0.1:8765", async () => ({
    ok: false,
    status: 400,
    json: async () => ({ ok: false, error: "fault latched" }),
  }));
  await assert.rejects(() => errorClient.request("/api/start"), /fault latched/);
});

test("main heartbeat never overlaps requests", async () => {
  let calls = 0;
  let concurrent = 0;
  let maxConcurrent = 0;
  const client = {
    request: async () => {
      calls += 1;
      concurrent += 1;
      maxConcurrent = Math.max(maxConcurrent, concurrent);
      await new Promise((resolve) => setTimeout(resolve, 15));
      concurrent -= 1;
    },
  };
  const stop = startHeartbeat(client, 5);
  await new Promise((resolve) => setTimeout(resolve, 45));
  stop();

  assert.ok(calls >= 2);
  assert.equal(maxConcurrent, 1);
});
