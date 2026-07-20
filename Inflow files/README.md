# Cardiac inflow examples

Each CSV can be loaded directly by the Cardiopump GUI.
Comment lines retain all source PMP metadata; the loader ignores lines beginning with `#`.
Numeric rows contain `time_ms,flow_normalized` values.
The PMP normalized flow samples are preserved; use Maximum flow in the GUI to set physical scaling.
The final row closes the cycle at the full PMP RR period using the first flow sample.
At 250 Hz the GUI rounds periods to its 4 ms raster (by at most 2 ms here); at 500 Hz these 10 ms-based periods are exact.

| Source | Converted file | Source points | Period (ms) | Min | Max | PMP scale |
|---|---|---:|---:|---:|---:|---:|
| Aorta_cut_patient30a_cut_peak266.0_RR1000_ut10.0_ECGdelay0.0.pmp | Aorta_cut_patient30a_cut_peak266.0_RR1000_ut10.0_ECGdelay0.0.csv | 100 | 1000 | -0.10727174782246922 | 1 | 266 |
| COA_P021_peak266_RR990.pmp | COA_P021_peak266_RR990.csv | 99 | 990 | -0.03875832320815251 | 1 | 266 |
| COA_P022_peak105_RR910.pmp | COA_P022_peak105_RR910.csv | 91 | 910 | -0.02538225406243704 | 1 | 105 |
| COA_P022_peak135_RR910.pmp | COA_P022_peak135_RR910.csv | 91 | 910 | -0.02538225406243704 | 1 | 135 |
| COA_P022_peak158_RR910.pmp | COA_P022_peak158_RR910.csv | 91 | 910 | -0.02538225406243704 | 1 | 158 |
| CoAP001_CoA_peak266.0_RR850.0_ut10.0_ECGdelay0.0.pmp | CoAP001_CoA_peak266.0_RR850.0_ut10.0_ECGdelay0.0.csv | 85 | 850 | -0.00752203341324776 | 1 | 266 |
| CoAP021_CoA_peak300.0_RR1060.0_ut10.0_ECGdelay0.0.pmp | CoAP021_CoA_peak300.0_RR1060.0_ut10.0_ECGdelay0.0.csv | 106 | 1060 | -0.045684252824785526 | 1 | 300 |
| PVNicoleRVOT_peak200_RR830.0_ut10.0_ECGdelay0.0 - edit2.pmp | PVNicoleRVOT_peak200_RR830.0_ut10.0_ECGdelay0.0 - edit2.csv | 83 | 830 | 0.05931122913759268 | 1 | 200 |
| PVNicoleRVOT_peak200_RR830.0_ut10.0_ECGdelay0.0 - modified.pmp | PVNicoleRVOT_peak200_RR830.0_ut10.0_ECGdelay0.0 - modified.csv | 83 | 830 | 0.05931122913759268 | 1 | 200 |
| PVNicoleRVOT_peak200_RR830.0_ut10.0_ECGdelay0.0.pmp | PVNicoleRVOT_peak200_RR830.0_ut10.0_ECGdelay0.0.csv | 83 | 830 | 0.04119593436696569 | 1 | 200 |
| Priya PV inflow.pmp | Priya PV inflow.csv | 83 | 830 | 0.04119593436696569 | 1 | 231.9 |
| editP001_CoA_peak266.0_RR850.0_ut10.0_ECGdelay0.0.pmp | editP001_CoA_peak266.0_RR850.0_ut10.0_ECGdelay0.0.csv | 85 | 850 | 0 | 1 | 266 |
| edit_P003_CoA_peak300.0_RR610.0_ut10.0_ECGdelay0.0.pmp | edit_P003_CoA_peak300.0_RR610.0_ut10.0_ECGdelay0.0.csv | 61 | 610 | 0 | 1 | 300 |
| edit_P021_CoA_peak300.0_RR850.0_ut10.0_ECGdelay0.0.pmp | edit_P021_CoA_peak300.0_RR850.0_ut10.0_ECGdelay0.0.csv | 106 | 1060 | 0 | 1 | 300 |
| simon_tapered.pmp | simon_tapered.csv | 85 | 850 | 0 | 1 | 266 |
