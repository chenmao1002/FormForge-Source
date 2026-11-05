# FormForge starter · BRICK-90 and SERVO8

[中文](EXAMPLES.md) · English

This first preview of FormForge 0.37.1 includes application source, two importable examples, native design files, and startup scripts.

| Example | Available material | Open validation |
| --- | --- | --- |
| BRICK-90 handheld | Schematics, PCB, 39-part assembly, game source, manufacturing files | Physical assembly, display/buttons, feel and battery life; firmware dependency review remains visible |
| SERVO8 controller | USB control and reserved 3.3V UART, PCB, STM32 hardware-PWM firmware, host tool, Gerber/BOM/placement | No complete PCBA/enclosure assembly; physical waveforms, loads, temperature and USB operation unverified; no production release |

## Start

1. Prepare Windows x64, Python 3.12, Node.js 24 and Microsoft Edge. Python and Node must be available on PATH. Initial dependency installation needs internet access.
2. Extract the entire ZIP, keeping `app`, both `.formforge.zip` packages and the scripts together.
3. Double-click **Start-Examples.cmd**. It installs the main service and web dependencies, starts the workbench and imports both examples.
4. Open **http://127.0.0.1:17990** and choose a project in the left sidebar. Repeated starts reuse identical samples.

Choose another port if necessary:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-Examples.ps1 -Port 17991
~~~

Stop the service:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\app\scripts\Stop.ps1
~~~

## Explore

- **Schematic:** expand and zoom into components and connections.
- **PCB:** switch copper layers and inspect outlines, mounting holes, routing and pin legends.
- **BRICK-90 mechanical:** drag to rotate, move the explosion slider and click Fit, then restore zero and fit again.
- **Firmware:** select `game.c` for BRICK-90 or `servo_core.c` for SERVO8; inspect existing HEX/BIN outputs.
- **Manufacturing:** inspect historical Gerber, BOM and placement files. These are review data, without production approval.

Viewing existing results does not require CadQuery, an ARM compiler or supplier login. New EDA checks need KiCad 10; mechanical rebuilds and firmware compilation need their optional environments. See `app/README.en.md`.

## SERVO8 host tool and power scope

Open `native/SERVO8/servo-control.html` to inspect the serial-control interface. No physical board was connected or validated for this preview. A working page does not establish serial communication or servo operation.

The design uses STM32G030K8T6 hardware PWM, a CP2102N USB-UART bridge and AHCT244 signal buffering for the documented SG90/5V prototype. Compatibility with arbitrary servos is not established.

The servo rail requires a separate regulated 5V supply; USB must not power eight servos. The reserved UART uses 3.3V logic. The default 1000–2000µs range is a pulse-width setting, not calibrated angle. OFF stops pulses but leaves 5V present and is not a mechanical emergency stop. The 8A figure is an engineering budget, not a measured board rating. Power, waveforms, loading and mechanical behavior need validation before further hardware use.

## Contents

- `BRICK-90.formforge.zip` and `SERVO8.formforge.zip` can also be imported into an existing FormForge installation through Import project package, with fresh local identities.
- `native/BRICK-90/` and `native/SERVO8/` contain native EDA, firmware, model or manufacturing files. SERVO8 has no mechanical assembly.
- `app/` contains source, without installed environments, Node dependencies, runtime data or logged-in sessions.
- `SHA256SUMS.json` records delivered file hashes. Root license and third-party notices retain component licensing.

Examples retain necessary versions and results only. Historical checks do not establish fresh local acceptance, prototype performance or supplier approval. Manufacturer PDFs are not redistributed; reference links remain in source records. Obtain and register required reference material before rebuilding.

Report the first error and the necessary local-log excerpt in repository Issues if installation, import or viewing fails.
