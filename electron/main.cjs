const { spawn } = require("node:child_process");
const net = require("node:net");
const path = require("node:path");
const { app, BrowserWindow, ipcMain } = require("electron");
const { BackendClient, startHeartbeat } = require("./backend-client.cjs");

let mainWindow = null;
let backendProcess = null;
let backendClient = null;
let stopHeartbeat = null;
let quitting = false;

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
}

function argumentValue(prefix) {
  const argument = process.argv.find((value) => value.startsWith(`${prefix}=`));
  return argument ? argument.slice(prefix.length + 1) : null;
}

function reserveBackendPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : null;
      server.close(() => port ? resolve(port) : reject(new Error("failed to reserve backend port")));
    });
  });
}

async function startBackend() {
  const projectRoot = process.env.CARDIOPUMP_PROJECT_ROOT || app.getAppPath();
  const port = await reserveBackendPort();
  const args = [
    "run",
    "--project",
    projectRoot,
    "cardiopump-backend",
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
  ];
  const serialPort = argumentValue("--serial-port") || process.env.CARDIOPUMP_SERIAL_PORT;
  if (serialPort) {
    args.push("--serial-port", serialPort);
  }
  if (process.argv.includes("--no-hardware")) {
    args.push("--no-hardware");
  }

  backendProcess = spawn(process.env.CARDIOPUMP_UV_PATH || "uv", args, {
    cwd: projectRoot,
    stdio: ["ignore", "pipe", "pipe"],
  });
  backendProcess.stdout.on("data", (chunk) => process.stdout.write(`[backend] ${chunk}`));
  backendProcess.stderr.on("data", (chunk) => process.stderr.write(`[backend] ${chunk}`));
  backendProcess.once("exit", (code, signal) => {
    if (!quitting) {
      console.error(`Cardiopump backend exited (${signal || code}).`);
      stopMainHeartbeat();
    }
  });

  backendClient = new BackendClient(`http://127.0.0.1:${port}`);
  await Promise.race([
    backendClient.waitUntilReady(),
    new Promise((_, reject) => {
      backendProcess.once("error", reject);
      backendProcess.once("exit", (code, signal) => {
        reject(new Error(`cardiopump backend exited before startup (${signal || code})`));
      });
    }),
  ]);
  console.log(`Cardiopump backend ready at ${backendClient.origin}.`);
}

function startMainHeartbeat() {
  stopMainHeartbeat();
  stopHeartbeat = startHeartbeat(backendClient, 100);
  console.log("Electron main-process heartbeat started.");
}

function stopMainHeartbeat() {
  if (stopHeartbeat) {
    stopHeartbeat();
    stopHeartbeat = null;
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 820,
    minWidth: 760,
    minHeight: 620,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    console.log("Cardiopump renderer ready.");
  });
  mainWindow.webContents.on("render-process-gone", () => stopMainHeartbeat());
  mainWindow.on("unresponsive", () => stopMainHeartbeat());
  mainWindow.on("responsive", () => {
    if (backendClient) {
      startMainHeartbeat();
    }
  });
  mainWindow.on("closed", () => {
    stopMainHeartbeat();
    mainWindow = null;
  });

  const devUrl = argumentValue("--dev-url");
  if (devUrl) {
    mainWindow.loadURL(devUrl);
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

function shutdownBackend() {
  quitting = true;
  stopMainHeartbeat();
  if (backendProcess && backendProcess.exitCode === null) {
    backendProcess.kill("SIGTERM");
  }
  backendProcess = null;
}

ipcMain.handle("backend:request", async (_event, requestPath, options) => {
  if (!backendClient) {
    throw new Error("cardiopump backend is not ready");
  }
  return backendClient.request(requestPath, options);
});

ipcMain.handle("backend:info", () => ({
  ready: backendClient !== null,
  mainOwnsHeartbeat: true,
}));

app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) {
      mainWindow.restore();
    }
    mainWindow.focus();
  }
});

app.whenReady().then(async () => {
  if (!hasSingleInstanceLock) {
    return;
  }
  try {
    await startBackend();
    createWindow();
    startMainHeartbeat();
  } catch (error) {
    console.error(error);
    shutdownBackend();
    app.quit();
  }
});

app.on("window-all-closed", () => app.quit());
app.on("before-quit", shutdownBackend);
