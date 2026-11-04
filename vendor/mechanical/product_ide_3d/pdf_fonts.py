"""Portable, embedded PDF fonts with explicit Unicode coverage checks.

The bundled static TrueType faces are derivatives of Noto Sans SC under the
SIL Open Font License 1.1. FontTools is used only to prepare those assets;
rendering requires ReportLab and never falls back to an unembedded CID font.
"""
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


@dataclass(frozen=True)
class PdfFonts:
    regular: str
    bold: str


# These are real source characters, not substitutions made during rendering.
ENGINEERING_CHARACTERS = "\u00d8\u2212\u00b1\u03bc\u00b5\u00b2\u00b3\u00b0\u2264\u2265\u00d7"


@lru_cache(maxsize=1)
def _registered_fonts() -> PdfFonts:
    fonts = PdfFonts("ProductIDE3DSansSC", "ProductIDE3DSansSC-Bold")
    directory = resources.files("product_ide_3d").joinpath("assets", "fonts")
    for name, filename in ((fonts.regular, "ProductIDE3DSansSC-Regular.ttf"),
                           (fonts.bold, "ProductIDE3DSansSC-Bold.ttf")):
        source = directory.joinpath(filename)
        if not source.is_file():
            raise FileNotFoundError("Bundled embedded PDF font is missing: " + str(source))
        with resources.as_file(source) as path:
            pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily(fonts.regular, normal=fonts.regular, bold=fonts.bold,
                                  italic=fonts.regular, boldItalic=fonts.bold)
    for name in (fonts.regular, fonts.bold):
        validate_pdf_text(ENGINEERING_CHARACTERS, name)
    return fonts


def validate_pdf_text(text: str, font_name: str) -> None:
    """Reject absent glyphs before writing a PDF, with their exact code points."""
    font = pdfmetrics.getFont(font_name)
    if not isinstance(font, TTFont):
        raise ValueError("PDF text requires an embedded TrueType font: " + font_name)
    missing = sorted({ord(char) for char in str(text)
                      if char not in "\n\r\t" and not font.face.charToGlyph.get(ord(char))})
    if missing:
        codepoints = ", ".join(f"U+{value:04X}" for value in missing)
        raise ValueError(f"Embedded PDF font {font_name} lacks glyphs: {codepoints}")


def embedded_pdf_fonts(text: str = "") -> PdfFonts:
    """Register the portable family and validate all supplied source text."""
    fonts = _registered_fonts()
    for name in (fonts.regular, fonts.bold):
        validate_pdf_text(text, name)
    return fonts
