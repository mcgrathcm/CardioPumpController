const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { File } = require("node:buffer");
const { startHeartbeat } = require("../electron/backend-client.cjs");

function fileHandler(showOpenDialog, readFile) {
  const handlers = new Map();
  const context = vm.createContext({
    require: (name) => {
      if (name === "electron") {
        return {
          app: {
            requestSingleInstanceLock: () => true,
            whenReady: () => new Promise(() => {}),
            on: () => {},
          },
          dialog: { showOpenDialog },
          ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
        };
      }
      if (name === "node:fs/promises") {
        return { readFile };
      }
      if (name === "./backend-client.cjs") {
        return require("../electron/backend-client.cjs");
      }
      return require(name);
    },
    __dirname: path.join(__dirname, "../electron"),
    process,
  });
  vm.runInContext(readFileSync(path.join(__dirname, "../electron/main.cjs"), "utf8"), context);
  vm.runInContext("mainWindow = { testWindow: true };", context);
  return handlers.get("file:open-text");
}

test("all desktop pickers read only the chosen file with the correct filters", async () => {
  for (const [inputId, extensions] of [
    ["waveform-file", ["txt", "csv"]],
    ["curve-designer-file", ["txt", "csv", "json"]],
    ["calibration-file", ["json"]],
  ]) {
    const handler = fileHandler(async (window, options) => {
      assert.equal(window.testWindow, true);
      assert.deepEqual(Array.from(options.filters[0].extensions), extensions);
      assert.deepEqual(Array.from(options.properties), ["openFile"]);
      return { canceled: false, filePaths: ["/chosen/sample.csv"] };
    }, async (filePath, encoding) => {
      assert.equal(filePath, "/chosen/sample.csv");
      assert.equal(encoding, "utf8");
      return "0,1\n";
    });
    const selected = await handler({}, inputId);
    assert.equal(selected.name, "sample.csv");
    assert.equal(selected.text, "0,1\n");
    await assert.rejects(handler({}, "/arbitrary/path"), /not allowed/);
  }
});

test("heartbeat continues while a picker is pending; cancellation reads nothing", async () => {
  let cancel;
  const handler = fileHandler(() => new Promise((resolve) => { cancel = resolve; }), () => {
    assert.fail("canceling must not read a file");
  });
  let beats = 0;
  const stop = startHeartbeat({ request: async () => { beats += 1; } }, 100);
  try {
    const pending = handler({}, "waveform-file");
    await new Promise((resolve) => setTimeout(resolve, 650));
    assert.ok(beats >= 5);
    cancel({ canceled: true, filePaths: [] });
    assert.equal(await pending, null);
  } finally {
    stop();
  }
});

test("desktop selection bypasses the HTML picker; browser selection remains available", async () => {
  const source = readFileSync(path.join(__dirname, "../src/cardiopump_backend/web/app.js"), "utf8");
  const binding = source.slice(source.indexOf("function bindFileInput("), source.indexOf("bindFileInput(elements.waveformFile"));
  const window = {};
  const elements = { controlMessage: {} };
  const context = vm.createContext({ window, elements, File });
  vm.runInContext(binding, context);
  const listeners = {};
  const input = { id: "waveform-file", addEventListener: (name, callback) => { listeners[name] = callback; } };
  const loaded = [];
  context.bindFileInput(input, async (file) => { loaded.push(file); });
  let prevented = false;
  const event = { preventDefault: () => { prevented = true; } };
  await listeners.click(event);
  assert.equal(prevented, false);
  await listeners.change();
  assert.equal(loaded.length, 1);
  loaded.length = 0;
  window.cardiopump = { openTextFile: async (id) => {
    assert.equal(id, input.id);
    return { name: "flow.csv", text: "0,1\n" };
  } };
  await listeners.click(event);
  assert.equal(prevented, true);
  assert.equal(loaded[0].name, "flow.csv");
  assert.equal(await loaded[0].text(), "0,1\n");
  window.cardiopump.openTextFile = async () => null;
  await listeners.click(event);
  assert.equal(loaded.length, 1);
  window.cardiopump.openTextFile = async () => { throw new Error("cannot read file"); };
  await listeners.click(event);
  assert.equal(elements.controlMessage.textContent, "cannot read file");
});
