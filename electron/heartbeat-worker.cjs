const { workerData } = require("node:worker_threads");
const { BackendClient, startHeartbeat } = require("./backend-client.cjs");

startHeartbeat(new BackendClient(workerData.origin), 100);
