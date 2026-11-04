"""Independent manufacturing data, explicit identities and coordinate conventions."""
from typing import Literal
from pydantic import Field, model_validator
from .source_schemas import Strict, RegisteredSourceRef


class DrillFormat(Strict):
    unit: Literal['mm', 'inch']
    integerDigits: int | None = Field(default=None, ge=1, le=6)
    decimalDigits: int | None = Field(default=None, ge=0, le=8)
    zerosSuppressed: Literal['leading', 'trailing'] | None = None

    @model_validator(mode='after')
    def complete(self):
        if (self.integerDigits is None) != (self.decimalDigits is None):
            raise ValueError('Provide both integerDigits and decimalDigits.')
        if self.integerDigits is not None and self.zerosSuppressed is None:
            raise ValueError('Fixed coordinate format requires explicit suppressed-zero convention.')
        return self


class CamLayer(Strict):
    path: str = Field(min_length=1, max_length=400)
    role: Literal['copper', 'mask', 'paste', 'legend', 'profile', 'drill', 'job']
    copperLayer: int | None = Field(default=None, ge=1, le=32)
    side: Literal['top', 'bottom'] | None = None
    plating: Literal['plated', 'non-plated'] | None = None
    drillFormat: DrillFormat | None = None

    @model_validator(mode='after')
    def identity(self):
        if (self.role == 'copper') != (self.copperLayer is not None):
            raise ValueError('Only copper layers require copperLayer.')
        if (self.role in {'mask', 'paste', 'legend'}) != (self.side is not None):
            raise ValueError('Mask/paste/legend require an explicit side; other roles omit side.')
        if (self.role == 'drill') != (self.plating is not None):
            raise ValueError('Only drill files require a plating declaration.')
        if self.role != 'drill' and self.drillFormat is not None:
            raise ValueError('drillFormat applies only to Excellon.')
        return self


class IgnoredCamFile(Strict):
    path: str = Field(min_length=1, max_length=400)
    reason: str = Field(min_length=1, max_length=1000)


class CamTableProbe(Strict):
    source: RegisteredSourceRef | None = None
    archivePath: str | None = Field(default=None, min_length=1, max_length=400)
    format: Literal['csv', 'xlsx']
    encoding: Literal['utf-8-sig', 'gb18030', 'utf-16'] = 'utf-8-sig'
    delimiter: Literal[',', ';', '\t'] = ','
    sheet: str | None = Field(default=None, min_length=1, max_length=120)
    headerRow: int = Field(default=1, ge=1, le=100)

    @model_validator(mode='after')
    def location(self):
        if (self.source is None) == (self.archivePath is None):
            raise ValueError('Choose exactly one registered source or ZIP entry.')
        if self.format=='csv' and self.sheet is not None:
            raise ValueError('CSV omits sheet.')
        return self


class TableLocation(CamTableProbe):
    @model_validator(mode='after')
    def worksheet(self):
        if self.format=='xlsx' and self.sheet is None:raise ValueError('XLSX requires an explicit sheet.')
        return self


class ExternalManufacturingProbe(Strict):
    archive: RegisteredSourceRef | None = None
    table: CamTableProbe | None = None


class BomColumns(Strict):
    reference: str = Field(min_length=1, max_length=120)
    quantity: str | None = Field(default=None, min_length=1, max_length=120)
    dnp: str | None = Field(default=None, min_length=1, max_length=120)
    value: str | None = Field(default=None, min_length=1, max_length=120)
    footprint: str | None = Field(default=None, min_length=1, max_length=120)
    mpn: str | None = Field(default=None, min_length=1, max_length=120)
    supplierPart: str | None = Field(default=None, min_length=1, max_length=120)


class BomTable(Strict):
    file: TableLocation
    columns: BomColumns
    referenceSeparator: Literal['comma', 'semicolon', 'whitespace', 'combined'] = 'combined'
    expandRanges: bool = False
    quantityMode: Literal['column', 'one-per-reference']
    dnpMode: Literal['column', 'none-declared']
    dnpDeclaration: str = Field(min_length=1, max_length=1000)
    dnpTrueValues: list[str] = Field(default_factory=lambda: ['1', 'true', 'yes', 'dnp'], max_length=30)
    dnpFalseValues: list[str] = Field(default_factory=lambda: ['', '0', 'false', 'no'], max_length=30)

    @model_validator(mode='after')
    def mappings(self):
        if (self.quantityMode == 'column') != (self.columns.quantity is not None):
            raise ValueError('Quantity column must match the declared quantity mode.')
        if (self.dnpMode == 'column') != (self.columns.dnp is not None):
            raise ValueError('DNP column must match the declared DNP mode.')
        yes={v.strip().casefold() for v in self.dnpTrueValues}
        no={v.strip().casefold() for v in self.dnpFalseValues}
        if yes & no or not yes or not no:
            raise ValueError('DNP true/false values must be nonempty and disjoint.')
        return self


class PlacementColumns(Strict):
    reference: str = Field(min_length=1, max_length=120)
    x: str = Field(min_length=1, max_length=120)
    y: str = Field(min_length=1, max_length=120)
    rotation: str = Field(min_length=1, max_length=120)
    side: str = Field(min_length=1, max_length=120)
    value: str | None = Field(default=None, min_length=1, max_length=120)
    footprint: str | None = Field(default=None, min_length=1, max_length=120)


class PlaneTransform(Strict):
    xSign: Literal[-1, 1]
    ySign: Literal[-1, 1]
    offsetXmm: float = Field(ge=-100000, le=100000, allow_inf_nan=False)
    offsetYmm: float = Field(ge=-100000, le=100000, allow_inf_nan=False)
    frameRotationDegrees: float = Field(ge=-360, le=360, allow_inf_nan=False)
    sourceAngleDirection: Literal['ccw', 'cw']
    sourceAngleZeroDegrees: float = Field(ge=-360, le=360, allow_inf_nan=False)


class PlacementTable(Strict):
    file: TableLocation
    columns: PlacementColumns
    unit: Literal['mm', 'inch', 'mil']
    topValues: list[str] = Field(min_length=1, max_length=30)
    bottomValues: list[str] = Field(min_length=1, max_length=30)
    topTransform: PlaneTransform
    bottomTransform: PlaneTransform

    @model_validator(mode='after')
    def sides(self):
        top={v.strip().casefold() for v in self.topValues}
        bottom={v.strip().casefold() for v in self.bottomValues}
        if top & bottom or '' in top | bottom:
            raise ValueError('Side values must be nonblank and disjoint.')
        return self


class ReferenceReason(Strict):
    reference: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=1000)


class ExternalManufacturingReview(Strict):
    expectedProjectVersion: int = Field(ge=1)
    archive: RegisteredSourceRef
    copperLayerCount: int = Field(ge=1, le=32)
    layers: list[CamLayer] = Field(min_length=2, max_length=128)
    ignoredFiles: list[IgnoredCamFile] = Field(default_factory=list, max_length=256)
    drillDeclaration: Literal['files-listed', 'none-declared']
    drillDeclarationReason: str = Field(min_length=1, max_length=1000)
    assemblyMode: Literal['pcb-only', 'assembly']
    bom: BomTable | None = None
    placements: PlacementTable | None = None
    excludedReferences: list[ReferenceReason] = Field(default_factory=list, max_length=10000)
    outsidePlacementExceptions: list[ReferenceReason] = Field(default_factory=list, max_length=10000)
    boardQuantity: int | None = Field(default=None, ge=1, le=1000000)
    footprintComparison: Literal['exact', 'name-after-colon'] = 'exact'
    message: str = Field(min_length=1, max_length=2000)
    requestId: str = Field(min_length=1, max_length=160)
    timeoutSeconds: int = Field(default=300, ge=30, le=600)

    @model_validator(mode='after')
    def complete(self):
        paths=[v.path for v in self.layers]+[v.path for v in self.ignoredFiles]
        if len({p.casefold() for p in paths}) != len(paths):
            raise ValueError('Each archive entry must be assigned once.')
        copper=[v.copperLayer for v in self.layers if v.role=='copper']
        if sorted(copper) != list(range(1,self.copperLayerCount+1)):
            raise ValueError('Declare every copper layer exactly once.')
        identities=[(v.role,v.side) for v in self.layers if v.role not in {'copper','drill'}]
        if len(set(identities)) != len(identities) or sum(v.role=='profile' for v in self.layers)!=1:
            raise ValueError('Declare one profile; do not duplicate side/role files.')
        if any(v.role=='drill' for v in self.layers) != (self.drillDeclaration=='files-listed'):
            raise ValueError('Drill files and declaration differ.')
        if self.assemblyMode=='assembly' and (self.bom is None or self.placements is None):
            raise ValueError('Assembly review requires both BOM and placements.')
        if self.assemblyMode=='pcb-only' and (self.bom or self.placements or self.excludedReferences or self.outsidePlacementExceptions):
            raise ValueError('Bare-board review must not silently consume assembly data.')
        for rows in [self.excludedReferences,self.outsidePlacementExceptions]:
            if len({r.reference.casefold() for r in rows}) != len(rows):
                raise ValueError('Reference declarations must not be duplicated.')
        return self
