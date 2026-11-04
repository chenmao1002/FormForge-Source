# FormForge licensing and third-party notices

Copyright (C) 2026 chenmao1002 and FormForge contributors.

Except where a file or component carries another license, the project's own
source code, documentation, and illustrations are licensed under the GNU General
Public License, version 3 only (SPDX: GPL-3.0-only). See LICENSE.

This program is free software: you can redistribute it and/or modify it under
the terms of version 3 of the GNU General Public License as published by the Free
Software Foundation. This program is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public
License for more details.

## Code from the author's earlier projects

The included original modules from Product_IDE_SCH, PCB_mode, 1021C, and
Product_IDE_HEX are covered by the author's GPL-3.0-only release. Migration
manifests in docs/migrations retain the source identities, hashes, and historical
migration license assessments alongside the current release authorization.
Unmodified archive bytes retain their hashes; earlier references to local
migration authorization do not narrow this release's GPL grant.

## Components with their own licenses

| Component | Location | License and retained notice |
| --- | --- | --- |
| Product IDE 3D mechanical engine | vendor/mechanical/ | MIT; see vendor/mechanical/LICENSE |
| Product IDE 3D Sans SC font derivatives | vendor/mechanical/product_ide_3d/assets/fonts/ | SIL Open Font License 1.1; see OFL.txt and font-provenance.json in that directory |
| Arm CMSIS core headers | vendor/firmware/cmsis/ and vendor/firmware/stm32/core/ | Apache-2.0; see LICENSE-ARM.txt and LICENSE.txt in the corresponding directories |
| STMicroelectronics device headers | vendor/firmware/cmsis/ and vendor/firmware/stm32/g0/, f1/, f4/ | Apache-2.0; retained LICENSE-ST.md and family LICENSE.md / License.md files apply |
| STM32G071 pin data | vendor/firmware/stm32/g071-pin-data/ | BSD-3-Clause; see the adjacent LICENSE |
| KiCad library symbol, extracted pin map, and associated license texts | vendor/firmware/stm32/g071cb.kicad_sym, vendor/firmware/stm32/pins.json, and src/formforge/resources/licenses/ | CC-BY-SA-4.0 with the KiCad libraries exception; see CC-BY-SA-4.0.txt and KiCad-library-exception.txt |

Third-party notices and source/license URLs in the migration manifests remain
part of the distribution. The project license does not replace those notices.

## Installed dependencies and external tools

Python and JavaScript dependencies are specified in the requirements and package
files. Their own license and copyright notices apply to their distributions.
KiCad, CadQuery/OpenCascade, the ARM GNU Toolchain, Electron, Playwright, pyOCD,
and esptool are separate dependencies or tools; their licenses are not replaced
by FormForge's project license.
