# R&S HMC8012 - Agent Reference Manual
> Rohde & Schwarz | 5 3/4 Digit Digital Multimeter  
> Focused on remote control, measurement functions, and scripting-relevant specs.

---

## 1. Remote Control Overview

The HMC8012 supports three interface modes. **Only one interface is active at a time.**  
Configure via: `SETUP → INTERFACE`

| Interface | Notes |
|-----------|-------|
| USB VCP (Virtual COM Port) | Uses SCPI over serial. Requires R&S USB-VCP driver. Compatible with any terminal. |
| USB TMC | Requires NI-VISA. No custom driver needed. Preferred for test automation. |
| Ethernet (LAN) | LXI 1.4 certified. Supports DHCP or static IP. Has integrated webserver. |
| IEEE-488 / GPIB | Optional, factory-fitted only (model: HMC8012-G). |

**SCPI Compatibility:** Commands are compatible with **Agilent 34401A and 34410A**.

---

## 2. Interface Setup

### 2.1 USB VCP
- Driver: download from Rohde & Schwarz website (free)
- Tested on: Windows XP, Vista, 7, 8 (32+64 bit)
- Use any terminal program with SCPI commands
- Software tool: HMExplorer (free, Windows) - supports terminal + screenshots

### 2.2 USB TMC
- Requires **NI-VISA** (download: http://www.ni.com/downloads/ni-drivers/)
- During install: select `NI-VISA → Leave this feature and its subfeatures installed locally`
- After install: `SETUP → INTERFACE → USB TMC`
- Connect via USB A–B cable
- OS recognizes as: `USB Test and Measurement Device (IVI)`
- **HMExplorer does NOT support USB TMC**

### 2.3 Ethernet
- Cable: CAT.5 or better, RJ-45, crossed or uncrossed
- Supports DHCP or static IP
- Static config: `SETUP → INTERFACE → ETHERNET → PARAMETER`
- Web access: `http://<IP_ADDRESS>` (device info, Ethernet settings, password)
- DHCP timeout if no cable/network: up to **3 minutes**
- RAW port: **5025** | VXI-11 port: **1024**

### 2.4 GPIB (optional)
- Model suffix: **HMC8012-G**
- Configure: `SETUP → INTERFACE → IEEE488 → PARAMETER`

---

## 3. Measurement Functions

### 3.1 Function Keys (front panel)

| Key | Function |
|-----|----------|
| `DC V` | DC Voltage |
| `AC V` | AC Voltage (displays RMS) |
| `DC I` | DC Current |
| `AC I` | AC Current |
| `Ω` | Resistance (2-wire or 4-wire) |
| `CAP` | Capacitance |
| `SENSOR` | Temperature (PT100 / PT500 / PT1000) |
| `HOLD` | Freeze measurement on display |
| `NULL` | Zero / relative offset correction |
| `MEAS` | Access math functions (Stats, Limits, Logging) |
| `TRIG` | Manual trigger |
| `SHIFT` | Activates numeric keypad |
| `SAVE/RECALL` | Store/load settings; hold for quick screenshot |
| `SETUP` | General settings, interface, firmware |

### 3.2 ADC Rate (applies to most functions)

| Rate | Readings/sec | Resolution |
|------|-------------|-----------|
| Slow | 5 | 5 3/4 digits |
| Medium | 10 | 4 3/4 digits |
| Fast | 200 | 4 3/4 digits |

> **Max accuracy always at SLOW.**  
> `ADC RATE = FAST` disables AUTO ZERO automatically.

### 3.3 Auto Range
- AUTO RANGE switches up at **90%** of range end, down at **10%**
- Manual override: `RANGE UP` / `RANGE DOWN` soft keys
- `OVER RANGE` shown on display if value exceeds manual range

### 3.4 Second Measurement (2nd Function)
Activate via soft key `2nd FUNCTION`. Available combinations:

| Main | Available 2nd |
|------|--------------|
| DC V | AC V, DC I, dB, dBm |
| AC V | DC V, Frequency, dB, dBm |
| DC I | AC I, DC V, dB, dBm |
| AC I | DC I, Frequency, dB, dBm |

---

## 4. Measurement Details

### 4.1 DC / AC Voltage
- Connectors: **COM** and **V**
- AC Filter (display filter, not low-pass): `SLOW` (<50Hz), `MEDIUM` (default), `FAST` (>1kHz)
- Input impedance: selectable **10MΩ** or **>10GΩ** (range-dependent)
- AUTO ZERO: `SETUP → Page 2|2`, compensates cable offset automatically
- dB/dBm: set reference voltage or impedance (50, 75, 600Ω or custom)

### 4.2 DC / AC Current
- Connectors: **COM** and **A**
- Max: **10A** (>5A: max 30s on, then >30s pause)
- Protected by fuse: **F10H250V** (user-replaceable, front panel)
- dBm ref impedance: 50, 75, 600Ω or custom

### 4.3 Frequency
- Available as **2nd function** on AC V and AC I only
- Gate time: `10ms`, `100ms`, `1s` (soft key `GATE TIME`)
- Resolution depends on gate time:

| Gate Time | Range | Resolution |
|-----------|-------|-----------|
| 1s (Slow) | 999.999 kHz | 1 Hz |
| 100ms (Medium) | 999.99 kHz | 10 Hz |
| 10ms (Fast) | 999.9 kHz | 100 Hz |

### 4.4 Resistance
- **2-wire**: COM + V connectors
- **4-wire**: COM + V + LO/HI (SENSE) connectors, use for precision
- Switch mode: `Ω menu → Page 2|2 → MODE → 2w / 4w`
- Use NULL (short circuit probes first) to cancel cable offset

### 4.5 Capacitance
- Connectors: COM and V
- Ranges: 5nF to 500µF
- Use NULL to compensate cable offset

### 4.6 Temperature (PT Sensors)
- Connectors: 2-wire (COM + V) or 4-wire (COM + V + LO/HI)
- Sensor types: **PT100**, **PT500**, **PT1000** (select via `PT TYPE`)
- Units: °C, K, °F
- Rate: 10 measurements/sec

### 4.7 Continuity / Diode Test
- Key: `*/diode` symbol
- Continuity: 1mA constant current, adjustable threshold (1Ω steps), 200 meas/sec
- Diode: 1mA constant current, adjustable threshold (10mV steps), 10 meas/sec
- Beeper: ON/OFF per soft key `BEEP`
- **Note: continuity beeper volume is NOT adjustable (hardware limitation)**

---

## 5. Math Functions (MEAS key)

### 5.1 Statistics
Activate: `MEAS → STATS → ON`

| Stat | Description |
|------|-------------|
| Min | Minimum value |
| Max | Maximum value |
| Mean | Average |
| StdDev | Standard deviation |
| Pk to Pk | Peak-to-peak |
| Count | Number of samples |

- `#MEAS`: set sample count (0 = infinite, max 50,000)
- `RESET`: resets stats
- `AUTO RESET ON`: count not reset in Auto Range mode

### 5.2 Limits
Activate: `MEAS → LIMIT → ON`
- Set `HIGH LIMIT` and `LOW LIMIT`
- Display turns **green** (within limits) or **red** (out of limits)
- `BEEPER`: optional audio alert on limit violation
- `INVALID COUNT` (red) shown when `OVER RANGE` occurs during stats

### 5.3 Data Logging
Activate: `MEAS → LOGGING → ON`

| Setting | Options |
|---------|---------|
| Storage | Internal (max 50,000 points) or USB stick (FAT/FAT32, max 4GB) |
| Format | CSV or TXT |
| Interval | Minimum ~5ms typ (function-dependent) |
| Mode U | Unlimited (until storage full) |
| Mode N | Fixed count (`COUNT` parameter) |
| Mode T | Fixed duration (`TIME` parameter) |

**Log file format (CSV):**
```
Rohde&Schwarz HMC8012 -Log-File
Date: YYYY-MM-DD
Start Time: HH:MM:SS
...
"DCV[V];ACV[V];Flag;Timestamp"
0.031153;0.004516;;00:02:53:974
```
- Flag field: `clipped` when OVER RANGE
- Timestamp format for Excel: `hh:mm:ss,000`
- Logs: Main + 2nd value + Timestamp

**Max log capacity example (DCV @ 200Sa/s, 4GB USB):**
- 153,858,348 values → ~8 days at 5ms interval

**Log gaps may occur due to:**
- Heavy SCPI command load on interface
- Slow USB flash drive
- File system sector size too large

### 5.4 AC+DC (True RMS)
- Soft key: `AC+DC` (only available when AC V or AC I is active)
- Shows DC+AC true RMS simultaneously with main value
- Useful for: PWM-driven signals, LED drivers

### 5.5 Power Display
- Activate: `MEAS → POWER → ON` (only available with DC/AC current active)
- Requires: current as main or 2nd function, voltage as the other
- `WIRE RES.`: enter cable resistance to auto-subtract voltage drop
- **Wire resistance can only be set from front panel, not via remote command**

---

## 6. Trigger

Activate: hold `TRIG` key or `SETUP → TRIGGER`

| Mode | Behavior |
|------|----------|
| AUTO (default) | Continuous measurement at ADC rate. Optional threshold. |
| SINGLE | Set `INTERVAL` (time between measurements) and `COUNT`. Press TRIG to start. |
| MANUAL | Press TRIG to start/stop capture. |

- `ABOVE/BELOW THRESHOLD`: capture only when signal crosses threshold
- `CONTINUE`: display runs without threshold gating
- Trigger settings **sync** with Logging settings

---

## 7. Input Protection Limits

| Parameter | Limit |
|-----------|-------|
| Max input voltage (DC) | 1000 V peak |
| Max input voltage (AC) | 750 V RMS |
| Max input current | 10A (max 250V), fuse F10H250V |
| Max V between V+ and GND | 1000 V peak |
| Max V between COM and GND | 600 V peak |
| Measurement category | CAT II (600V) |

---

## 8. Key Specifications

### DC Voltage Accuracy (1 year, 23°C ±5K)
| Range | Accuracy (% rdg + % range) |
|-------|---------------------------|
| 400mV | 0.015 + 0.002 |
| 4V | 0.015 + 0.002 |
| 40V | 0.020 + 0.002 |
| 400V | 0.020 + 0.002 |
| 1000V | 0.025 + 0.002 |

### AC Voltage Accuracy (45Hz–20kHz)
| Range | Accuracy |
|-------|---------|
| 400mV–750V | 0.3 + 0.05 |

### Resistance (4-wire)
| Range | Accuracy |
|-------|---------|
| 400Ω | 0.050 + 0.005 |
| 4kΩ–400kΩ | 0.015–0.030 + 0.002–0.003 |
| 4MΩ | 0.060 + 0.005 |
| 40MΩ | 0.250 + 0.003 |
| 250MΩ | 2.000 + 0.010 |

### Capacitance
| Range | Accuracy |
|-------|---------|
| 5nF | 2.0 + 2.5 |
| 50nF–50µF | 1.0 + 0.5–2.0 |
| 500µF | 2.0 + 1.0 |

---

## 9. Storage & Screenshot

- `SAVE/RECALL` → `DEVICE SETTINGS`: save/load instrument config (HDS binary format)
- Storage locations: **Internal** or **USB (FRONT)**
- Screenshot: USB only, formats **BMP** or **PNG**, color modes: COLOR / GRAYSCALE / INVERTED
- **Hold SAVE/RECALL** = instant screenshot to USB
- Instrument settings from old firmware **cannot** be loaded on new firmware

---

## 10. General / Power

| Parameter | Value |
|-----------|-------|
| Power supply | 115V or 230V ±10%, 50/60Hz (selector on rear) |
| Power consumption | 25W max, 12–15W typical |
| Mains fuse (115V) | T1L250V |
| Mains fuse (230V) | T500L250V |
| Operating temp | 0°C to +55°C |
| Warm-up time | **90 minutes** for full spec accuracy |
| Display | TFT color, 320×240px, 5 3/4 digits |
| Max simultaneous values | 3 (Main + 2nd + Math) |

---

## 11. Cabling Notes (EMC)
- Data cables (USB, LAN): max **3m**, shielded, indoor only
- Signal/measurement cables: max **1m**, shielded (RG58/U), indoor only
- Strong RF fields may inject noise into readings. Instrument won't be damaged but readings may drift slightly
