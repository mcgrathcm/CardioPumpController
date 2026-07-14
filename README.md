# Arduino Cardiopump

Local control system for a gear pump driven by a Parker Gemini GV servo
controller through an Arduino Uno R4 WiFi.

Current scope:

- Browser GUI and Python backend connected to the Arduino over USB serial. The
  frontend is kept framework-neutral for a future Vite/Electron application.
- Constant flow commands use non-negative `mL/s` values and are mapped to `+-10 V`.
  The Arduino owns the active flow-to-voltage mapping. Before all four
  calibration points are loaded, the `1.0 mL/s` nominal fallback is capped at
  a conservative `2.5 V`.
- Pulsatile waveform files use two columns: `time_ms` and signed `flow_ml_s`.
- Waveforms are interpolated at a selectable `250` or `500 Hz` raster
  and transferred as signed Q15 values plus their physical peak flow. The
  Arduino applies the measured forward calibration by magnitude for both flow
  directions. Before calibration, the configured waveform peak is capped at
  `+-2.5 V`.
- Pulsatile negative-flow clipping and per-cycle total stroke-volume diagnostics
  are available in the Flow view.
- Trigger output is pin 7, active high, with a 10 ms default pulse. Starts use a
  `0.5 s` ramp; the first trigger occurs only after that ramp reaches its target.
  Later triggers fire at each pulsatile cycle start and once per second in
  constant-flow mode.
- `examples/sinusoid_1s_0_to_1.txt` is a signed one-second test waveform.
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

Run the web GUI and backend:

```sh
uv run cardiopump-backend
```

Open `http://127.0.0.1:8765` in a browser. The same frontend can be wrapped by
Vite/Electron later because it talks to the backend through JSON HTTP endpoints.

By default the backend auto-connects to the last `/dev/cu.usbmodem*` Arduino
port it finds. To force a port:

```sh
uv run cardiopump-backend --serial-port /dev/cu.usbmodem90706925F9BC2
```

Use `--no-hardware` to run the backend without opening a serial port. Hardware
start and enable controls remain unavailable in this mode.

Compile the production firmware without uploading it:

```sh
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi \
  --library third_party/DFRobot_GP8XXX-master \
  hardware/cardiopump_firmware
```
