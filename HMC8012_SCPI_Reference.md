# R&S HMC8012 — Complete SCPI Reference
> Rohde & Schwarz HMC8012 Digital Multimeter  
> Based on official SCPI Programmers Manual Version 01  
> Overflow sentinel: `9.90000000E+37` (input exceeds selected range in manual mode)

---

## Table of Contents
1. [Interfaces & Connection](#1-interfaces--connection)
2. [Remote Control Basics](#2-remote-control-basics)
3. [SCPI Command Syntax](#3-scpi-command-syntax)
4. [Common IEEE 488.2 Commands](#4-common-ieee-4882-commands)
5. [Synchronization](#5-synchronization)
6. [Status Reporting System](#6-status-reporting-system)
7. [System Commands](#7-system-commands)
8. [Display Commands](#8-display-commands)
9. [Trigger Commands](#9-trigger-commands)
10. [Measurement Function Selection](#10-measurement-function-selection)
11. [Quick Measure — MEASure](#11-quick-measure--measure)
12. [Configure + Read — CONFigure / READ? / FETCh?](#12-configure--read--configure--read--fetch)
13. [Sensor Configuration — SENSe](#13-sensor-configuration--sense)
14. [Math / Calculation Functions — CALCulate](#14-math--calculation-functions--calculate)
15. [Data Logging — DATA](#15-data-logging--data)
16. [Screenshot & State — HCOPy / SAV / RCL](#16-screenshot--state--hcopy--sav--rcl)
17. [Scripting Patterns & Templates](#17-scripting-patterns--templates)

---

## 1. Interfaces & Connection

### 1.1 USB

- Two modes selectable on the instrument: **VCP** (Virtual COM Port) or **USB TMC**.
- **VCP**: communicate via any terminal after installing the Windows driver (XP 32-bit, Vista, Win7 32/64-bit). Driver free from HAMEG website.
- **USB TMC** (preferred): no special driver if VISA is installed; uses status registers for reliable completion detection (same mechanism as GPIB).
- No configuration needed on the instrument side, just select the interface.

### 1.2 LAN (Ethernet)

- 10/100 Mbps Ethernet (IEEE 802.3/802.3u), RJ-45 cable.
- IP via DHCP (default) or manual: `SETUP → Interface → Ethernet → Parameter`.
- **End character must be LF (`\n`).**

**Resource strings:**
```
TCPIP::<IP_address>::5025::SOCKET    ← SCPI raw socket (default port)
TCPIP::<hostname>::5025::SOCKET
TCPIP::<IP_address>::1024::SOCKET    ← VXI-11 port
```

**Verify connection:**
```bash
ping <IP>
# Then open http://<IP> in browser → "Instrument Home" page
```

### 1.3 GPIB (Optional)

- Default address: **20** (configurable 0–30 via `Setup → Interface → Parameter`).
- Max 15 instruments, max cable 15 m (2 m between any two).
- Address retained after instrument reset.

---

## 2. Remote Control Basics

- Instrument starts in **local** state on power-on.
- Sending any command switches it to remote; display stays on, front panel remains usable.
- Commands are mostly compatible with Agilent 34401A / 34410A.

---

## 3. SCPI Command Syntax

### 3.1 Structure

```
<HEADER> <whitespace> <parameter>[,<parameter>,...]
```

- Header and parameters separated by whitespace (ASCII 0–9, 11–32).
- Queries formed by appending `?` to the header.
- **Case-insensitive.**

### 3.2 Short vs Long Form

Each mnemonic has a long and short form. Short form = uppercase letters only. Only these two forms are valid.

```
CALCulate:FUNCtion NULL  ≡  CALC:FUNC NULL
INITiate[:IMMediate]     ≡  INIT:IMM  ≡  INIT
```

### 3.3 Special Characters

| Symbol | Meaning |
|--------|---------|
| `:`    | Separates mnemonics (hierarchy) |
| `;`    | Separates two commands on same line (does not change path) |
| `,`    | Separates multiple parameters |
| `?`    | Forms a query |
| `*`    | Marks a common (IEEE 488.2) command |
| `"`    | Delimits string parameters |
| `[ ]`  | Optional mnemonic |
| `{ }`  | Optional parameter, can repeat |
| `\|`   | OR, alternative parameter values |

### 3.4 Parameter Types

| Type | Notes |
|------|-------|
| Numeric | With sign, decimal, exponent. `10mV = 10E-3`. Mantissa max 255 chars, exponent -32000…32000. |
| Special numeric | `MIN` / `MAX`, instrument uses min/max value for that function. |
| Boolean | `ON` or `1` / `OFF` or `0`. Queries always return `1` or `0`. |
| Text | Short/long mnemonic form. Query returns short form. |
| String | Enclosed in `"..."`. |

---

## 4. Common IEEE 488.2 Commands

| Command | Description | Usage |
|---------|-------------|-------|
| `*RST` | Reset to factory defaults | Set only |
| `*IDN?` | Returns `HAMEG,HMC8012,<serial>,<firmware>` | Query only |
| `*CLS` | Clear status registers (STB, ESR, EVENt) and output buffer | Set only |
| `*OPC` | Set bit 0 in ESR when all commands complete | Set |
| `*OPC?` | Block until all commands complete, returns `1` | Query |
| `*WAI` | Block next command until all previous complete | Event |
| `*TRG` | Software trigger | Event |
| `*TST?` | Self-test, returns `0` if no errors | Query only |
| `*SRE <0-255>` | Set Service Request Enable register (bit 6 always 0) | Set / Query |
| `*STB?` | Read Status Byte | Query only |
| `*ESE <0-255>` | Set Event Status Enable register | Set / Query |
| `*ESR?` | Read Event Status Register (clears it) | Query only |

---

## 5. Synchronization

Use one of these to prevent reading stale values or overlapping execution:

| Command | Behavior |
|---------|----------|
| `*OPC?` | Blocks until done, returns `1`. Simplest method. |
| `*OPC`  | Sets OPC bit (bit 0) in ESR when done. Non-blocking, detect via SRQ. |
| `*WAI`  | Blocks further processing until all previous commands complete. |

**Simple blocking (recommended for scripts):**
```python
instrument.write("CONF:VOLT:DC")
instrument.write("*OPC?")
instrument.read()   # blocks until measurement complete
value = instrument.query("FETCH?")
```

**SRQ-based (non-blocking, event-driven):**
```python
instrument.write("*ESE 1")    # enable OPC in ESE
instrument.write("*SRE 32")   # enable ESB in SRE → triggers SRQ
instrument.write("<command>; *OPC")
# wait for SRQ interrupt from instrument
```

**Polling ESR:**
```python
instrument.write("*ESE 1")
instrument.write("<command>")
while True:
    instrument.write("*OPC; *ESR?")
    if int(instrument.read()) & 1:
        break
```

> On timeout: clear error queue with `SYST:ERR?` to remove `-410, Query interrupted` entries.

---

## 6. Status Reporting System

### 6.1 Register Hierarchy

```
STB (Status Byte)  ←  SRE (mask)
  ├── ESR (Event Status Register)  ←  ESE (mask)
  ├── STATus:OPERation register
  └── STATus:QUEStionable register
       └── Error Queue (via bit 2 of STB)
```

### 6.2 Each Register Has 5 Parts

| Part | Description |
|------|-------------|
| CONDition | Current hardware state. Read-only, NOT cleared by reading. |
| EVENt | Latches events since last read. Cleared on read. |
| ENABle | Mask: which EVENt bits contribute to sum bit. |
| PTRansition | Positive transition filter |
| NTRansition | Negative transition filter |

Sum bit = OR of (EVENt AND ENABle) → propagates up the hierarchy.

### 6.3 Status Byte (STB) — Bits

| Bit | Decimal | Meaning |
|-----|---------|---------|
| 2   | 4       | Error Queue, entry present |
| 3   | 8       | QUEStionable status sum bit |
| 4   | 16      | MAV (message in output buffer available) |
| 5   | 32      | ESB (Event Status Register sum bit) |
| 6   | 64      | MSS (Master Status Summary, instrument requests service) |
| 7   | 128     | OPERation status register sum bit |

### 6.4 Event Status Register (ESR) — Bits

| Bit | Meaning |
|-----|---------|
| 0   | Operation Complete (set by `*OPC`) |
| 2   | Query Error |
| 3   | Device-dependent Error (-300…-399 or positive error) |
| 4   | Execution Error (-200…-300) |
| 5   | Command Error (-100…-200) |
| 7   | Power On |

### 6.5 STATus:OPERation Register — Bits

| Bit | Meaning |
|-----|---------|
| 0   | Calibrating (service only) |
| 4   | **Measuring** |
| 5   | **Waiting for Trigger** |
| 10  | Instrument Locked (RWLock) |

```
STATus:OPERation:CONDition?
STATus:OPERation[:EVENt]?
STATus:OPERation:ENABle <val>
STATus:OPERation:ENABle?
```

### 6.6 STATus:QUEStionable Register — Bits

| Bit | Meaning |
|-----|---------|
| 0   | Voltage overrange |
| 1   | Current overrange |
| 4   | Temperature overrange |
| 5   | Frequency overload/underflow |
| 9   | Resistance overrange |
| 10  | Capacitance overload/underflow |
| 11  | **Lower limit failed** |
| 12  | **Upper limit failed** |

```
STATus:QUEStionable:CONDition?
STATus:QUEStionable[:EVENt]?
STATus:QUEStionable:ENABle <val>
STATus:QUEStionable:ENABle?
STATus:PRESet   ← resets all enable registers
```

### 6.7 Error Queue

```
SYSTem:ERRor[:NEXT]?   → <code>, "<description>"
                          returns  0, "No error"  when empty
```

---

## 7. System Commands

| Command | Description | Usage |
|---------|-------------|-------|
| `SYSTem:REMote` | Lock front panel; unlockable via panel button or `SYSTem:LOCal` | Set only |
| `SYSTem:LOCal` | Return control to front panel | Set only |
| `SYSTem:RWLock` | Hard lock, only `SYSTem:LOCal` can unlock | Set only |
| `SYSTem:BEEPer:STATe {ON\|OFF}` | Enable/disable beeper. Default: ON | Set / Query |
| `SYSTem:BEEPer[:IMMediate]` | Emit single beep immediately | Set only |
| `SYSTem:ERRor[:NEXT]?` | Read and dequeue next error | Query only |
| `SYSTem:VERSion?` | Returns SCPI standard version | Query only |

> Prefer `SYSTem:REMote` over `SYSTem:RWLock`. If the script crashes, the panel won't be permanently locked.

---

## 8. Display Commands

```
DISPlay:TEXT[:DATA] "<String>"   ← show message on display
DISPlay:TEXT:CLEar               ← clear message
```

---

## 9. Trigger Commands

### Mode
```
TRIGger:MODE {AUTO | MANual | SINGle}
TRIGger:MODE?
```
| Mode | Behavior |
|------|----------|
| `AUTO` | Continuous auto trigger (default after `*RST`) |
| `MANual` | Waits for `*TRG` or `READ?` |
| `SINGle` | Triggers N times (per `TRIGger:COUNt`), then goes idle |

### Count (SINGle mode)
```
TRIGger:COUNt {<1-50000> | MIN | MAX | DEFault}   ← *RST: 1
TRIGger:COUNt? [MINimum | MAXimum]
```

### Interval (SINGle mode)
```
TRIGger:INTerval {<0-3600s> | MIN | MAX | DEFault}   ← *RST: 0
TRIGger:INTerval? [MINimum | MAXimum]
```

### Level (AUTO mode)
```
TRIGger:LEVel {<-1000 to 1000V> | MIN | MAX | DEFault}   ← *RST: 0V
TRIGger:LEVel? [MINimum | MAXimum]

TRIGger:LEVel:MODe {CONTinue | ABOVe | BELow}   ← *RST: CONTinue
TRIGger:LEVel:MODe?
```

---

## 10. Measurement Function Selection

```
[SENSe:]FUNCtion[:ON] <Function>
[SENSe:]FUNCtion[:ON]?
```

| Parameter | Function | Query returns |
|-----------|----------|---------------|
| `VOLTage[:DC]` | DC Voltage | `VOLT` |
| `VOLTage:AC` | AC Voltage | `VOLT:AC` |
| `CURRent[:DC]` | DC Current | `CURR` |
| `CURRent:AC` | AC Current | `CURR:AC` |
| `RESistance` | 2-wire Resistance | `RES` |
| `FRESistance` | 4-wire Resistance (Kelvin) | `FRES` |
| `FREQuency[:VOLTage]` | Frequency via AC V | `FREQ` |
| `FREQuency:CURRent` | Frequency via AC I | `FREQ:CURR` |
| `CONTinuity` | Continuity | `CONT` |
| `DIODe` | Diode test | `DIOD` |
| `SENSor` | Temperature (RTD) | `SENS` |
| `CAPacity` | Capacitance | `CAP` |

**Default:** `VOLT[:DC]`

---

## 11. Quick Measure — `MEASure`

One-shot: configure + trigger + return result in a single query.  
All commands are **Query only**. On range overflow: returns `9.90000000E+37`.

```
MEASure[:VOLTage][:DC]?    [{<Range>|AUTO|MIN|MAX|DEF}]   ← ranges: 400mV,4V,40V,400V,1000V
MEASure[:VOLTage]:AC?      [{<Range>|AUTO|MIN|MAX|DEF}]   ← ranges: 400mV,4V,40V,400V,750V
MEASure:CURRent[:DC]?      [{<Range>|AUTO|MIN|MAX|DEF}]   ← ranges: 20mA,200mA,2A,10A
MEASure:CURRent:AC?        [{<Range>|AUTO|MIN|MAX|DEF}]   ← ranges: 20mA,200mA,2A,10A
MEASure:RESistance?        [{<Range>|AUTO|MIN|MAX|DEF}]   ← ranges: 400Ω,4k,40k,400k,4M,40M,250MΩ
MEASure:FRESistance?       [{<Range>|AUTO|MIN|MAX|DEF}]   ← ranges: 400Ω,4k,40k,400k,4MΩ
MEASure:FREQuency[:VOLTAGE]? [{<Range>|AUTO|MIN|MAX|DEF}] ← 5Hz–700kHz, voltage range
MEASure:FREQuency:CURRent  [{<Range>|AUTO|MIN|MAX|DEF}]   ← 5Hz–10kHz (20mA,200mA); 5Hz–5kHz (2A,10A)
MEASure:CAPacitance?       [{<Range>|AUTO|MIN|MAX|DEF}]   ← ranges: 5nF,50nF,500nF,5µF,50µF,500µF
MEASure:CONTinuity?                                        ← fixed 4000Ω range
MEASure:DIODe?                                             ← fixed 5V range
MEASure:TEMPerature?       [{<Probe_Type>|DEF}[,{<Type>|DEF}]]  ← FRTD/RTD, PT100/PT500/PT1000
```

> `MEASure:*` resets displayed statistics on each call.

---

## 12. Configure + Read — `CONFigure` / `READ?` / `FETCh?`

Use when you want to configure once, then trigger separately.

```
CONFigure[:VOLTage][:DC]  [{<Range>|AUTO|MIN|MAX|DEF}]
CONFigure[:VOLTage]:AC    [{<Range>|AUTO|MIN|MAX|DEF}]
CONFigure:CURRent[:DC]    [{<Range>|AUTO|MIN|MAX|DEF}]
CONFigure:CURRent:AC      [{<Range>|AUTO|MIN|MAX|DEF}]
CONFigure:RESistance      [{<Range>|AUTO|MIN|MAX|DEF}]
CONFigure:FRESistance     [{<Range>|AUTO|MIN|MAX|DEF}]
CONFigure:FREQuency[:VOLTAGE]
CONFigure:FREQuency:CURRent
CONFigure:CAPacitance     [{<Range>|AUTO|MIN|MAX|DEF}]
CONFigure:CONTinuity
CONFigure:DIODe
CONFigure:TEMPerature     [{<Probe_Type>|DEF}[,{<Type>|DEF}[,1]]]

CONFigure?   ← returns current config string, e.g. "TEMP, PT100, RTD"
```

**Trigger + read:**
```
READ?    ← triggers AND returns one measurement
FETCh?   ← returns last measurement WITHOUT triggering (use after *TRG)
```

> `FETCh?` vs `READ?`: Always use `FETCh?` after `*TRG`. `READ?` triggers automatically.

---

## 13. Sensor Configuration — `[SENSe:]`

> `[SENSe:]` prefix is optional. `*RST` values noted inline.

### ADC Rate
```
[SENSe:]ADCRate {SLOW | MEDium | FAST}   ← *RST: SLOW
[SENSe:]ADCRate?                          ← returns: SLOW | MED | FAST
```

---

### DC Voltage

Ranges: `400mV, 4V, 40V, 400V, 1000V` | MIN/DEF: 400mV | MAX: 1000V

```
[SENSe:]VOLTage[:DC]:NULL[:STATe] {ON|OFF}                    # *RST: OFF
[SENSe:]VOLTage[:DC]:NULL[:STATe]?
[SENSe:]VOLTage[:DC]:NULL:VALue {<Value>|MIN|MAX}              # -1000V to 1000V, step 1nV
[SENSe:]VOLTage[:DC]:NULL:VALue?
[SENSe:]VOLTage[:DC]:RANGe:AUTO <Mode>                        # *RST: ON
[SENSe:]VOLTage[:DC]:RANGe:AUTO?
[SENSe:]VOLTage[:DC]:RANGe[:UPPer] {<Range>|MIN|MAX|DEF}      # DEF=1000mV
[SENSe:]VOLTage[:DC]:RANGe[:UPPer]?                           # MIN=0.4, MAX=1000
[SENSe:]VOLTage[:DC]:ZERO:AUTO <Mode>                         # ON|OFF
[SENSe:]VOLTage[:DC]:ZERO:AUTO?
```

---

### AC Voltage

Ranges: `400mV, 4V, 40V, 400V, 750V` | MIN/DEF: 400mV | MAX: 750V

```
[SENSe:]VOLTage:AC:BANDwidth {<Filter>|MIN|MAX|DEF}           # 10=Slow,50=Medium,400=Fast | *RST: 50
[SENSe:]VOLTage:AC:BANDwidth?
[SENSe:]VOLTage:AC:NULL[:STATe] {ON|OFF}                      # *RST: OFF
[SENSe:]VOLTage:AC:NULL[:STATe]?
[SENSe:]VOLTage:AC:NULL:VALue {<Value>|MIN|MAX}               # 0 to 750V, step 1nV
[SENSe:]VOLTage:AC:NULL:VALue?
[SENSe:]VOLTage:AC:RANGe:AUTO <Mode>                          # *RST: ON
[SENSe:]VOLTage:AC:RANGe:AUTO?
[SENSe:]VOLTage:AC:RANGe[:UPPer] {<Range>|MIN|MAX|DEF}
[SENSe:]VOLTage:AC:RANGe[:UPPer]?                             # MIN=0.4, MAX=750
```

---

### DC Current

Ranges: `20mA, 200mA, 2A, 10A` | MIN/DEF: 20mA | MAX: 10A

```
[SENSe:]CURRent[:DC]:NULL[:STATe] {ON|OFF}                    # *RST: OFF
[SENSe:]CURRent[:DC]:NULL[:STATe]?
[SENSe:]CURRent[:DC]:NULL:VALue {<Value>|MIN|MAX}             # -10A to 10A, step 10pA
[SENSe:]CURRent[:DC]:NULL:VALue?
[SENSe:]CURRent[:DC]:RANGe:AUTO <Mode>                        # *RST: ON
[SENSe:]CURRent[:DC]:RANGe:AUTO?
[SENSe:]CURRent[:DC]:RANGe[:UPPer] {<Range>|MIN|MAX|DEF}
[SENSe:]CURRent[:DC]:RANGe[:UPPer]?                           # MIN=0.02, MAX=10
```

---

### AC Current

Ranges: `20mA, 200mA, 2A, 10A` | MIN/DEF: 20mA | MAX: 10A

```
[SENSe:]CURRent:AC:BANDwidth {<Threshold>|MIN|MAX|DEF}        # 10=Slow,50=Medium,400=Fast | *RST: 50
[SENSe:]CURRent:AC:BANDwidth?
[SENSe:]CURRent:AC:NULL[:STATe] {ON|OFF}                      # *RST: OFF
[SENSe:]CURRent:AC:NULL[:STATe]?
[SENSe:]CURRent:AC:NULL:VALue {<Value>|MIN|MAX}               # -10 to 10A, step 10pA
[SENSe:]CURRent:AC:NULL:VALue?
[SENSe:]CURRent:AC:RANGe:AUTO <Mode>                          # *RST: ON
[SENSe:]CURRent:AC:RANGe:AUTO?
[SENSe:]CURRent:AC:RANGe[:UPPer] {<Range>|MIN|MAX|DEF}
[SENSe:]CURRent:AC:RANGe[:UPPer]?                             # MIN=0.02, MAX=10
```

---

### 2-Wire Resistance

Ranges: `400Ω, 4kΩ, 40kΩ, 400kΩ, 4MΩ, 40MΩ, 250MΩ` | MIN/DEF: 400Ω | MAX: 250MΩ

```
[SENSe:]RESistance:NULL[:STATe] {ON|OFF}                      # *RST: OFF
[SENSe:]RESistance:NULL[:STATe]?
[SENSe:]RESistance:NULL:VALue {<Value>|MIN|MAX}               # 0 to 250MΩ, step 1Ω
[SENSe:]RESistance:NULL:VALue?                                 # MIN=0, MAX=2.5e8
[SENSe:]RESistance:RANGe:AUTO <Mode>                          # *RST: ON
[SENSe:]RESistance:RANGe:AUTO?
[SENSe:]RESistance:RANGe[:UPPer] {<Range>|MIN|MAX|DEF}
[SENSe:]RESistance:RANGe[:UPPer]?                             # MIN=400, MAX=2.5e8
```

---

### 4-Wire Resistance (Kelvin)

Ranges: `400Ω, 4kΩ, 40kΩ, 400kΩ, 4MΩ` | MIN/DEF: 400Ω | MAX: 4MΩ

```
[SENSe:]FRESistance:NULL[:STATe] {ON|OFF}                     # *RST: OFF
[SENSe:]FRESistance:NULL[:STATe]?
[SENSe:]FRESistance:NULL:VALue {<Value>|MIN|MAX}              # 0 to 4MΩ, step 1Ω
[SENSe:]FRESistance:NULL:VALue?                                # MIN=0, MAX=4e6
[SENSe:]FRESistance:RANGe:AUTO <Mode>                         # *RST: ON
[SENSe:]FRESistance:RANGe:AUTO?
[SENSe:]FRESistance:RANGe[:UPPer] {<Range>|MIN|MAX|DEF}
[SENSe:]FRESistance:RANGe[:UPPer]?                            # MIN=400, MAX=4e6
```

---

### Capacitance

Ranges: `5nF, 50nF, 500nF, 5µF, 50µF, 500µF` | MIN/DEF: 5nF | MAX: 500µF

```
[SENSe:]CAPacitance:NULL[:STATe] {ON|OFF}                     # *RST: OFF
[SENSe:]CAPacitance:NULL[:STATe]?
[SENSe:]CAPacitance:NULL:VALue {<Value>|MIN|MAX}              # 0 to 500µF, step 1F
[SENSe:]CAPacitance:NULL:VALue?
[SENSe:]CAPacitance:RANGe:AUTO <Mode>                         # *RST: ON
[SENSe:]CAPacitance:RANGe:AUTO?
[SENSe:]CAPacitance:RANGe[:UPPer] {<Range>|MIN|MAX|DEF}
[SENSe:]CAPacitance:RANGe[:UPPer]?
```

---

### Continuity

Fixed range: 4000Ω

```
[SENSe:]CONTinuity:THReshold {<Threshold>|MIN|MAX|DEF}        # 0Ω to 1MΩ, step 1Ω | DEF/RST: 200Ω
[SENSe:]CONTinuity:THReshold?
[SENSe:]CONTinuity:BEEPer[:STATe] {ON|OFF}                    # *RST: OFF
[SENSe:]CONTinuity:BEEPer[:STATe]?
```

---

### Diode Test

Fixed range: 5V

```
[SENSe:]DIODe:THReshold {<Threshold>|MIN|MAX|DEF}             # 0V to 5V, step 1µV | DEF/RST: 700mV
[SENSe:]DIODe:THReshold?                                       # MIN=0, MAX≈4.95, DEF≈0.7
[SENSe:]DIODe:BEEPer[:STATe] {ON|OFF}                         # *RST: OFF
[SENSe:]DIODe:BEEPer[:STATe]?
```

---

### Frequency

```
[SENSe:]FREQuency:APERture {<Seconds>|MIN|MAX|DEF}            # 10ms|100ms|1s | DEF/RST: 1s
[SENSe:]FREQuency:APERture?                                    # MIN=0.01, MAX=1

# Voltage input ranges (AC V mode):
[SENSe:]FREQuency:VOLTage:RANGe:AUTO <Mode>                   # *RST: ON
[SENSe:]FREQuency:VOLTage:RANGe:AUTO?
[SENSe:]FREQuency:VOLTage:RANGe[:UPPer] {<Range>|MIN|MAX|DEF} # 400mV,4V,40V,400V,750V
[SENSe:]FREQuency:VOLTage:RANGe[:UPPer]?                      # MIN=0.4, MAX=750

# Current input ranges (AC I mode):
[SENSe:]FREQuency:CURRent:RANGe:AUTO <Mode>                   # *RST: ON
[SENSe:]FREQuency:CURRent:RANGe:AUTO?
[SENSe:]FREQuency:CURRent:RANGe[:UPPer] {<Range>|MIN|MAX|DEF} # 20mA,200mA,2A,10A
[SENSe:]FREQuency:CURRent:RANGe[:UPPer]?                      # MIN=0.02, MAX=10
```

---

### Temperature (RTD)

```
CONFigure:TEMPerature [{<Probe_Type>|DEF}[,{<Type>|DEF}[,1]]]
# Example: CONF:TEMP FRTD,PT500

[SENSe:]TEMPerature:TRANsducer:TYPE {FRTD|RTD}                # *RST: RTD
[SENSe:]TEMPerature:TRANsducer:TYPE?
[SENSe:]TEMPerature:TRANsducer:RTD:TYPE {PT100|PT500|PT1000}  # *RST: PT100
[SENSe:]TEMPerature:TRANsducer:RTD:TYPE?

UNIT:TEMPerature {C|K|F}
UNIT:TEMPerature?

[SENSe:]TEMPerature:NULL[:STATe] {ON|OFF}                     # *RST: OFF
[SENSe:]TEMPerature:NULL[:STATe]?
[SENSe:]TEMPerature:NULL:VALue {<Value>|MIN|MAX}              # -273.1°C to 999.9°C, step 1µ°C
[SENSe:]TEMPerature:NULL:VALue?                                # MIN=-273.1, MAX=999.9
```

---

## 14. Math / Calculation Functions — `CALCulate`

> **Two-step activation required:** `CALC:FUNC <x>` sets the function; `CALC ON` activates it.

```
CALCulate:FUNCtion {NULL | DB | DBM | AVERage | LIMit | POWer}
CALCulate:FUNCtion?
CALCulate[:STATe] {ON | OFF}
CALCulate[:STATe]?   ← returns 1 (ON) or 0 (OFF)
```

### Function compatibility matrix

| Meas. Function | AVER | LIMit | NULL | dB | dBm | Power |
|----------------|:----:|:-----:|:----:|:--:|:---:|:-----:|
| DC V           | ✓    | ✓     | ✓    | ✓  | ✓   | ✓ (with DC I as 2nd) |
| AC V           | ✓    | ✓     | ✓    | ✓  | ✓   | —     |
| DC I           | ✓    | ✓     | ✓    | ✓  | ✓   | ✓ (with DC V as 2nd) |
| AC I           | ✓    | ✓     | ✓    | ✓  | ✓   | —     |
| Ω / FRES       | ✓    | ✓     | ✓    | —  | —   | —     |
| CAP            | ✓    | ✓     | ✓    | —  | —   | —     |
| SENSOR / TEMP  | ✓    | —     | ✓    | —  | —   | —     |

### Limits

```
CALCulate:LIMit:LOWer {<Value> | MINimum | MAXimum}
CALCulate:LIMit:LOWer? {MINimum | MAXimum}    ← e.g. -7.500E+02
CALCulate:LIMit:UPPer {<Value> | MINimum | MAXimum}
CALCulate:LIMit:UPPer? {MINimum | MAXimum}
```

Pass/fail results → STATus:QUEStionable bits 11 (lower) and 12 (upper).

### dB Reference

```
CALCulate:DB:REFerence {<Value> | MINimum | MAXimum}
CALCulate:DB:REFerence? {MINimum | MAXimum}    ← e.g. 1.0E-06
```

### dBm Reference Resistance

```
CALCulate:DBM:REFerence {<Value> | MINimum | MAXimum}   # 1Ω to 65535Ω, step 1Ω
CALCulate:DBM:REFerence? {MINimum | MAXimum}             # e.g. 6.000E+02
```

### Null Offset

```
CALCulate:NULL:OFFSet {<Value> | MINimum | MAXimum}
CALCulate:NULL:OFFSet? {MINimum | MAXimum}    ← e.g. 1.00E+01
```

### DC Power

```
CALCulate:POWer?   ← returns DC power (W). Returns -1 if not in DCV/DCI or DCI/DCV mode.
```

### Statistics (AVERage mode)

Activate with `CALC:FUNC AVER` + `CALC ON`.

```
CALCulate:AVERage:CLEar          ← reset all stats (setting only)
CALCulate:AVERage:COUNt?         ← number of samples, e.g. 1.000E+02
CALCulate:AVERage:AVERage?       ← mean value,            e.g. 1.82852E-07
CALCulate:AVERage:MAXimum?       ← max value,             e.g. 1.55606E-07
CALCulate:AVERage:MINimum?       ← min value,             e.g. 2.24768E-07
CALCulate:AVERage:PTPeak?        ← peak-to-peak,          e.g. 6.91621E-08
CALCulate:AVERage:SDEViation?    ← standard deviation,    e.g. 1.50020E-08
```

---

## 15. Data Logging — `DATA`

### Enable/Disable

```
DATA:LOG[:STATe] {0 | 1 | OFF | ON}
DATA:LOG[:STATe]?   ← returns 1 or 0
```

> Logging must be STATE ON even for manual/single trigger mode to save files.

### Configure

```
DATA:LOG:FNAMe {"<FileName>"},[{INT|EXT|DEF}]   # e.g. "Test01.CSV",INT
DATA:LOG:FNAMe?                                  # returns e.g. "/INT/DATA/Test01.CSV"

DATA:LOG:FORMat {CSV | TXT}                      # *RST: CSV
DATA:LOG:FORMat?

DATA:LOG:MODE {UNLimited | COUNt | TIME}         # *RST: UNL
DATA:LOG:MODE?                                   # returns UNL | COUN | TIME

DATA:LOG:TIME <seconds>                          # internal max: 50000h
DATA:LOG:TIME?

DATA:LOG:COUNt <n_samples>                       # internal max: 50000
DATA:LOG:COUNt?

DATA:LOG:INTerval <seconds>                      # 0s to 3600s
DATA:LOG:INTerval?
```

### Access Files

```
DATA:DATA? {"<FileName>"},[{INT|EXT|DEF}]    ← return file contents
DATA:DELete {"<FileName>"},[{INT|EXT|DEF}]   ← delete file
DATA:POINts? {"<FileName>"},[{INT|EXT|DEF}]  ← number of data points (INT max 50000)
DATA:LIST? [{INT|EXT|DEF}]                   ← list all log files in location
```

**Storage locations:** `INT` = internal, `EXT` = USB stick, `DEF` = internal (default).

**Log file format (CSV example):**
```
# HAMEG-Log-File ;
# Date: 2013-05-23 ;
# Start Time:, 15:09:32;
# Stop Time:, 15:10:03 ;
# Settings: ;
#   ADC Rate : Fast;
#   AC Filter : ------;
#   Input Imp. : 10M;
DCV[V],DCI[A],Flag;
12.891854, 0.982340 ;
12.889381, 0.982539 ;
```

---

## 16. Screenshot & State — `HCOPy` / `*SAV` / `*RCL`

```
HCOPy:FORMat {BMP | PNG}    ← *RST: BMP
HCOPy:FORMat?
HCOPy:SIZE:X?               ← horizontal resolution (query only)
HCOPy:SIZE:Y?               ← vertical resolution (query only)
HCOPy:DATA?                 ← returns screenshot as binary data (query only)

*SAV {0|1|2|3|4}            ← save current instrument state to slot (overwrites silently)
*RCL {0|1|2|3|4}            ← recall state from slot
```

---

## 17. Scripting Patterns & Templates

### Setup (pyvisa, LAN)

```python
import pyvisa

rm = pyvisa.ResourceManager()
instr = rm.open_resource("TCPIP::192.168.0.2::5025::SOCKET")
instr.read_termination = '\n'
instr.write_termination = '\n'
instr.timeout = 5000  # ms

instr.write("*RST")
instr.write("*CLS")
instr.write("SYSTem:REMote")
print(instr.query("*IDN?"))
```

### Single DC voltage measurement

```python
instr.write("CONF:VOLT:DC AUTO")
instr.query("*OPC?")              # block until configured
value = float(instr.query("READ?"))
if value > 9e37:
    raise RuntimeError("Range overflow, reduce range or use AUTO")
print(f"Voltage: {value:.6f} V")
```

### Synchronized measurement (explicit trigger)

```python
instr.write("CONF:VOLT:DC 4")    # fixed 4V range
instr.write("TRIG:MODE MAN")
instr.write("*TRG")
instr.query("*OPC?")             # wait for trigger + conversion
value = float(instr.query("FETCH?"))
```

### Single trigger mode, N samples

```python
instr.write("CONF:VOLT:DC AUTO")
instr.write("TRIG:MODE SING")
instr.write("TRIG:COUN 10")
instr.write("TRIG:INT 0.5")      # 0.5s between samples
instr.write("READ?")             # starts acquisition (returns first value)
instr.query("*OPC?")
```

### Statistics over N samples

```python
instr.write("CONF:VOLT:DC AUTO")
instr.write("CALC:FUNC AVER")
instr.write("CALC ON")
instr.write("CALC:AVER:CLE")

instr.write("TRIG:COUN 100")
instr.write("READ?")
instr.query("*OPC?")

mean   = float(instr.query("CALC:AVER:AVER?"))
stddev = float(instr.query("CALC:AVER:SDEV?"))
maxv   = float(instr.query("CALC:AVER:MAX?"))
minv   = float(instr.query("CALC:AVER:MIN?"))
pkpk   = float(instr.query("CALC:AVER:PTP?"))
print(f"Mean={mean:.4e}  StdDev={stddev:.4e}  Max={maxv:.4e}  Min={minv:.4e}")
```

### Limit test (pass/fail)

```python
instr.write("CONF:VOLT:DC AUTO")
instr.write("CALC:FUNC LIM")
instr.write("CALC:LIM:LOW 4.9")   # lower limit
instr.write("CALC:LIM:UPP 5.1")   # upper limit
instr.write("CALC ON")

value = float(instr.query("READ?"))
questional = int(instr.query("STAT:QUES:EVEN?"))
lower_fail = bool(questional & (1 << 11))
upper_fail = bool(questional & (1 << 12))
print(f"Value={value:.4f}  PASS={not (lower_fail or upper_fail)}")
```

### DC Power measurement (V × I)

```python
# Requires dual input: DCV primary + DCI secondary (or vice versa)
instr.write("CALC:FUNC POW")
instr.write("CALC ON")
power = float(instr.query("CALC:POW?"))
if power == -1:
    raise RuntimeError("Not in DCV/DCI or DCI/DCV dual mode")
print(f"Power: {power:.4f} W")
```

### Data logging to USB

```python
instr.write('DATA:LOG:FNAM "TEST01.CSV",EXT')
instr.write("DATA:LOG:FORM CSV")
instr.write("DATA:LOG:MODE COUN")
instr.write("DATA:LOG:COUN 500")
instr.write("DATA:LOG:INT 1")       # 1s interval
instr.write("DATA:LOG:STAT ON")
instr.query("*OPC?")                # wait until complete
instr.write("DATA:LOG:STAT OFF")
data = instr.query('DATA:DATA? "TEST01.CSV",EXT')
```

### Error check & teardown

```python
err = instr.query("SYST:ERR?")
if not err.startswith("0"):
    print(f"Instrument error: {err}")

instr.write("SYSTem:LOCal")
instr.close()
```

---

## Key Rules for Script Authors

1. **End character:** always LF (`\n`), mandatory for LAN.
2. **Case-insensitive.** Use short forms (`CONF:VOLT:DC` not `CONFigure:VOLTage:DC`).
3. **Never mix commands and queries in the same message** unless deliberately chaining (e.g., `*OPC; *ESR?`).
4. **Synchronize every measurement** with `*OPC?` or check STATus:OPERation bit 4 (Measuring) before reading.
5. **Always drain the error queue** (`SYST:ERR?`) after sequences to catch silent failures.
6. **Overflow sentinel** `9.90000000E+37` must be caught in code (means range too small).
7. **Overrange conditions** reported in STATus:QUEStionable. Poll it when readings seem invalid.
8. **Limit test results** in STATus:QUEStionable bit 11 (lower) and bit 12 (upper).
9. Prefer `SYSTem:REMote` over `SYSTem:RWLock`. Prevents permanent panel lockout on script crash.
10. **Always send `SYSTem:LOCal`** at script teardown.
