const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("cardiopump", {
  mainOwnsHeartbeat: true,
  request: (path, options) => ipcRenderer.invoke("backend:request", path, options),
  backendInfo: () => ipcRenderer.invoke("backend:info"),
});
