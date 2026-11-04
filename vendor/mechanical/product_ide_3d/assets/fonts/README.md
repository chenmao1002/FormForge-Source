# Embedded PDF fonts

`ProductIDE3DSansSC-Regular.ttf` and `ProductIDE3DSansSC-Bold.ttf` are static
TrueType derivatives of the locally installed Noto Sans SC variable font.
They cover Chinese text and engineering symbols such as the original `Ø`,
Unicode minus `−`, `±`, `μ`, `µ`, `²` and `³` without replacing source text.

The source copyright and SIL Open Font License 1.1 are retained in the font
metadata and [OFL.txt](OFL.txt). The derivative family is renamed to
`Product IDE 3D Sans SC`, avoiding the source's Reserved Font Name `Source`.
The upstream license is available in the official Google Fonts repository:
https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/OFL.txt

`font-provenance.json` records the exact source and derivative SHA-256 hashes.
The source was `C:/Windows/Fonts/NotoSansSC-VF.ttf`; its hash identifies the
input independently of that machine-specific path. Development used
`fontTools.varLib.instancer.instantiateVariableFont` at `wght=400` and
`wght=700`, with `updateFontNames=True`, then renamed name IDs 1, 2, 3, 4, 6,
16 and 17. The original copyright/license metadata is retained. There is
no `fvar` table in either resulting font, and `OS/2.fsType=0` permits embedding.

These files and the license are included in wheel and portable packages.
PDF generation uses ReportLab only: it requires no fontTools installation,
network download or machine-installed CJK font. Both faces are embedded as
PDF TrueType subsets. Unsupported source characters cause an explicit error
containing their Unicode code points rather than a silent font substitution.
