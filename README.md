# Arduino Cardiopump

Backend skeleton for controlling a gear pump driven by a Parker Gemini GV servo
controller through an Arduino Uno R4 WiFi.

Current scope:

- PC-side backend only.
- Simulated Arduino/device state; no hardware connection required.
- Constant flow commands use non-negative `mL/s` values and are mapped to `+-10 V`.
  The current fallback full-scale value is `1.0 mL/s` until calibration is added.
- Pulsatile waveform files use two columns: `time_ms` and signed `flow_ml_s`.
- Waveforms are interpolated at a selectable `250`, `500`, or `1000 Hz` raster
  and transferred as signed Q15 DAC commands. The configured maximum flow is
  currently treated as the linear full-scale calibration point until a measured
  calibration is added.
- Pulsatile negative-flow clipping and per-cycle total stroke-volume diagnostics
  are available in the Flow view.
- Trigger output is pin 7, active high, with a 10 ms default pulse. It fires at
  each pulsatile cycle start and once per second in constant-flow mode.
- `examples/sinusoid_1s_0_to_1.txt` is a signed one-second test waveform.
- Board-style safety watchdog: loss of host contact for more than `0.5 s` forces
  zero velocity and opens the Arduino-controlled relay.

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
Electron later because it talks to the backend through JSON HTTP endpoints.

By default the backend auto-connects to the last `/dev/cu.usbmodem*` Arduino
port it finds. To force a port:

```sh
uv run cardiopump-backend --serial-port /dev/cu.usbmodem90706925F9BC2
```

Use `--no-hardware` to run the GUI against the simulator only.
