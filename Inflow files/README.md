# Cardiac inflow examples

The `.pmp` files are the original waveform sources and are retained for
provenance. The Cardiopump GUI does not load `.pmp` directly.

Use the corresponding CSV files in `loader-ready/`. They contain headerless
`time_ms,flow_ml_s` rows, preserve each source RR period, and close the waveform
cycle with a final point. The flow samples remain normalized; set the intended
physical maximum flow in the GUI before starting the pump.
