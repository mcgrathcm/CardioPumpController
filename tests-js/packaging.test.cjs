const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const projectRoot = path.join(__dirname, "..");
const main = readFileSync(path.join(projectRoot, "electron", "main.cjs"), "utf8");
const packageJson = require(path.join(projectRoot, "package.json"));

test("packaged app launches its bundled backend", () => {
  assert.match(main, /app\.isPackaged/);
  assert.match(main, /process\.resourcesPath, "backend", "cardiopump-backend"/);
  assert.equal(packageJson.build.extraResources[0].from, "build/backend/cardiopump-backend");
  assert.equal(packageJson.build.extraResources[0].to, "backend/cardiopump-backend");
  assert.equal(packageJson.build.mac.icon, "build-resources/icon.svg");
});
