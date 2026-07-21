# Arduino Cardiopump

Local control system for a gear pump driven by a Parker Gemini GV servo
controller through an Arduino Uno R4 WiFi.

Current scope:

- Electron desktop GUI built with Vite and connected to the Arduino over USB
  serial through the existing Python control backend. Electron launches and
  owns the backend process, proxies renderer API requests through a restricted
  preload bridge, and sends the 100 ms heartbeat from its main process. Losing
  renderer focus or minimizing the window therefore does not pause heartbeats.
- The browser GUI remains available as a development fallback. In browser mode
  only, the renderer sends the heartbeat because no Electron main process exists.
- Constant flow commands use non-negative `mL/s` values and are mapped to `+-10 V`.
  The Arduino owns the active flow-to-voltage mapping. Before all four
  calibration points are loaded, the `1.0 mL/s` nominal fallback is capped at
  a conservative `2.5 V`.
- Pulsatile waveform files use two numeric columns, `time_ms` and signed
  `flow_ml_s`, in either comma-delimited CSV or whitespace-delimited text.
- Curve Designer is a separate Flow mode that uses the same waveform execution
  path. It starts as a two-point constant curve or fits 2–20 adaptively placed
  control points from a waveform file. Shape-preserving cubic (PCHIP), linear,
  and periodic-cubic interpolation are selectable; periodic cubic is smoothest but
  may overshoot between points. PCHIP and periodic cubic match the start/end slope.
  The first and last control-point markers share one flow value to close the cycle.
  Loaded source curves remain visible as a faint
  reference and can be refitted with a new point count using the number field and
  +/- buttons; each count change resets the source scaling, stays editable, and
  becomes the baseline used by Reset.
  Maximum flow and period are adjustable, and the preview reports fitted and
  scaled-original stroke volumes. Right-clicking on or near the fitted curve opens
  an Add Point menu, while right-clicking a point highlights it and opens
  lock/unlock and remove actions. Unlocked interior points can be dragged in time
  and flow; endpoint times remain fixed, and locked points cannot be dragged. Drag
  empty plot space to zoom; nearly horizontal or vertical drags show an end-capped
  range and zoom one axis, while diagonal drags show a box.
  Double-click restores the full view. Any manual edit
  disables point-count refitting until Reset restores the selected baseline curve.
  The current interpolated curve can be exported as a rasterized CSV, while a
  separate JSON builder checkpoint preserves control points, locks, interpolation,
  scaling, and period for later editing and reconstructs its saved default curve.
- Waveforms are interpolated at a selectable `250` or `500 Hz` raster
  and transferred as signed Q15 values plus their physical peak flow. The
  Arduino applies the measured forward calibration by magnitude for both flow
  directions. Before calibration, the configured waveform peak is capped at
  `1.0 mL/s`, which maps to a conservative `+-2.5 V`.
- Pulsatile negative-flow clipping and per-cycle total stroke-volume diagnostics
  are available in the Flow view.
- Trigger output is pin 7, active high, with a 10 ms default pulse. Starts use a
  `0.5 s` ramp; the first trigger occurs only after that ramp reaches its target.
  Later triggers fire at each pulsatile cycle start and once per second in
  constant-flow mode.
- `examples/sinusoid_1s_0_to_1.txt` is a signed one-second test waveform.
- `Inflow files/` contains validated, loader-compatible cardiac CSV curves.
  Source PMP metadata is retained in each file as ignored `#` comments.
- `Calibration files/example-format-not-measured.json` documents the calibration
  file format. Its placeholder values must not be used to operate hardware.
- Heartbeat loss and waveform timing overruns ramp to zero over `0.5 s`, open
  the Arduino-controlled relay, and latch a fault that requires manual clearing.
- Overpressure and interlock failures stop immediately and latch a fault.
  Servo Ready must close within 250 ms of Arduino Enable and remain closed;
  motion-time relay opening is detected without a software delay.
- DAC communication loss immediately opens Arduino Enable and latches a fault;
  start and enable commands require a responding DAC.
- Active pump operation and controlled ramps suppress full USB telemetry so a
  stalled host cannot block the safety loop or consume the 2 ms sample budget
  at 500 Hz. Compact, nonblocking heartbeat acknowledgements provide connection
  freshness while running; full diagnostics return after the pump stops.
- Serial protocol version 4 uses sequence-numbered acknowledgements. Waveform
  transfers require sequential offsets and CRC32 validation before Start is
  accepted; the backend rejects incompatible firmware. Full telemetry is a
  fixed-order numeric `TEL4` record without field labels and is emitted at 1 Hz
  while stopped or on request.
- Raw nonzero DAC voltage commands are disabled. Normal operation must use a
  guarded constant, waveform, or calibration run mode.
- The Uno R4 hardware watchdog resets the controller if the main loop stops
  refreshing it for approximately `2.7 s`. Reset cause, retained last fault,
  fault count, ramp timing, serial/protocol errors, and DAC write failures are
  reported in the Debug tab. A watchdog reset boots with Arduino Enable open
  and latches a `watchdog_reset` fault requiring manual clearing.
- Fault clearing requires all safe inputs to remain stable for `250 ms`; this
  dwell never delays fault entry.

Calibration workflow:

- User confirms that the pump may start.
- The Arduino applies `2.5`, `5`, `7.5`, and `10 V` points using a `0.5 s` ramp
  up, `59.5 s` flat top, and `0.5 s` ramp down. This has the same area as an
  instantaneous 60-second run at the requested voltage.
- The drive remains engaged after each run. The user enters either mean flow or
  measured fluid volume; the GUI converts volume to mean flow over 60 seconds.
- Calibration exists only in Arduino RAM. A complete four-point calibration can
  be loaded atomically with `CAL_LOAD`; the GUI is responsible for reloading it
  from a standalone calibration file after a board reset. Only last-fault
  diagnostics are retained on the Arduino.
- The Setup tab can save and load standalone calibration JSON files. Files use
  the fixed `2.5`, `5`, `7.5`, and `10 V` points and four positive, strictly
  increasing `flows_ml_s` values. Loading replaces all four RAM values atomically.
- Negative-flow commands reuse the forward calibration by magnitude; calibration
  runs do not drive the pump in reverse.

Finalized Arduino pinout:

- Pin 9: pressure latch input, `INPUT_PULLUP`; low means overpressure latch set.
- Pin 10: servo-ready input, `INPUT_PULLUP`; low means servo controller relay is enabled/ready.
- Pin 11: Arduino enable output; defaults high/safe, driven low to enable the interlock relay.
- Pin 12: pressure latch reset output; defaults low, pulsed high to reset the latch.
- Pin 7: trigger output; defaults low, driven high for the configured trigger pulse.

Run tests:

```sh
uv run python -m unittest discover -s tests -p 'test_*.py'
```

Install the desktop dependencies once:

```sh
pnpm install
```

Build the Vite renderer and start the Electron app with hardware auto-detection:

```sh
pnpm start
```

Run the complete Electron app without opening a serial port:

```sh
pnpm start:sim
```

For renderer development with Vite hot reload, use `pnpm dev` or
`pnpm dev:sim`. A specific serial port can be passed through Electron:

```sh
pnpm start -- --serial-port=/dev/cu.usbmodem90706925F9BC2
```

The Electron app requires `uv` while running from this source checkout because
the main process launches `uv run cardiopump-backend`. It uses an ephemeral
localhost port, maintains the firmware heartbeat in the main process, and stops
the backend when the desktop app exits. A renderer crash or unresponsive window
stops main-process heartbeats so the firmware still enters its controlled
communication fault; minimizing or unfocusing a healthy window does not.

Run the browser fallback and backend directly:

```sh
uv run cardiopump-backend
```

Open `http://127.0.0.1:8765` in a browser.

By default the backend auto-connects to the last `/dev/cu.usbmodem*` Arduino
port it finds. To force a port:

```sh
uv run cardiopump-backend --serial-port /dev/cu.usbmodem90706925F9BC2
```

Use `--no-hardware` to run the backend without opening a serial port. Hardware
start and enable controls remain unavailable in this mode.

Run GUI and backend tests:

```sh
pnpm test
uv run python -m unittest discover -s tests -p 'test_*.py'
```

See `Calibration files/` for the calibration JSON schema example and its safety
warning. Operational files must contain values measured from the connected pump.

Compile the production firmware without uploading it:

```sh
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi \
  --library third_party/DFRobot_GP8XXX-master \
  hardware/cardiopump_firmware
```
