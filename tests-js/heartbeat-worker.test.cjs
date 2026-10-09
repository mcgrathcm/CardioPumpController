const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const { Worker } = require("node:worker_threads");
const { once } = require("node:events");

test("heartbeat survives a blocked main thread and stops when its worker terminates", async () => {
  const server = new Worker(`
    const { parentPort } = require("node:worker_threads");
    const http = require("node:http");
    let beats = 0;
    let maxGap = 0;
    let previous = 0;
    http.createServer((request, response) => {
      if (request.url === "/api/heartbeat") {
        const now = Date.now();
        if (previous) maxGap = Math.max(maxGap, now - previous);
        previous = now;
        beats += 1;
      }
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ ok: true, beats, maxGap }));
    }).listen(0, "127.0.0.1", function () {
      parentPort.postMessage("http://127.0.0.1:" + this.address().port);
    });
  `, { eval: true });
  let heartbeat;
  try {
    const [origin] = await once(server, "message");
    heartbeat = new Worker(path.join(__dirname, "../electron/heartbeat-worker.cjs"), {
      workerData: { origin },
    });
    let before;
    do {
      await new Promise((resolve) => setTimeout(resolve, 50));
      before = await (await fetch(origin + "/count")).json();
    } while (before.beats < 2);
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1100);
    const after = await (await fetch(origin + "/count")).json();
    assert.ok(after.beats - before.beats >= 8);
    assert.ok(after.maxGap < 500, `heartbeat gap was ${after.maxGap} ms`);
    await heartbeat.terminate();
    const stopped = await (await fetch(origin + "/count")).json();
    await new Promise((resolve) => setTimeout(resolve, 250));
    assert.equal((await (await fetch(origin + "/count")).json()).beats, stopped.beats);
  } finally {
    if (heartbeat) await heartbeat.terminate();
    await server.terminate();
  }
});
