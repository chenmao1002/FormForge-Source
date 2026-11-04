from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProject(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=20000)
    preferences: dict[str, Any] = Field(default_factory=dict)


    @model_validator(mode='after')
    def numeric_preferences(self):
        if 'moduleDefaults' in self.preferences:
            from .module_settings import ModuleDefaults
            ModuleDefaults.model_validate(self.preferences['moduleDefaults'])
        if 'pcbRules' in self.preferences:
            from .pcb_rule_settings import resolve
            resolve(self.preferences)
        return self


class RenameProject(StrictModel):
    expectedVersion: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)


class UpdateProject(StrictModel):
    expectedVersion: int = Field(ge=1)
    specification: dict[str, Any]
    preferences: dict[str, Any]


    @model_validator(mode='after')
    def numeric_preferences(self):
        if 'moduleDefaults' in self.preferences:
            from .module_settings import ModuleDefaults
            ModuleDefaults.model_validate(self.preferences['moduleDefaults'])
        if 'pcbRules' in self.preferences:
            from .pcb_rule_settings import resolve
            resolve(self.preferences)
        return self


class ImportProject(StrictModel):
    sourceDir: str = Field(min_length=1, max_length=1000)
    schematic: str | None = Field(default=None, max_length=500)
    board: str | None = Field(default=None, max_length=500)
    requestId: str = Field(min_length=1, max_length=160)


class InspectProject(StrictModel):
    revisionId: str
    include3d: bool = False
    includeDocumentation: bool | None = None
    timeoutSeconds: int = Field(default=180, ge=15, le=900)
    requestId: str = Field(min_length=1, max_length=160)


Coordinate = Annotated[float, Field(ge=-10000, le=10000, allow_inf_nan=False)]


class ObjectEdit(StrictModel):
    sheet: str = Field(min_length=1, max_length=500)
    objectId: str = Field(min_length=1, max_length=100)
    expectedHash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SetProperty(ObjectEdit):
    kind: Literal['set_property']
    property: Literal['Value', 'Footprint', 'Datasheet', 'Description']
    value: str = Field(min_length=1, max_length=2000)


class MoveField(ObjectEdit):
    kind: Literal['move_field']
    property: Literal['Reference', 'Value']
    at: tuple[Coordinate, Coordinate]
    rotation: Literal[0, 90] | None = Field(default=None, exclude_if=lambda v: v is None,
        description='可选原生字段角度；省略保持原角度。实际显示方向还包含器件旋转。')


class SetText(ObjectEdit):
    kind: Literal['set_text']
    text: str = Field(min_length=1, max_length=4000)


class MoveText(ObjectEdit):
    kind: Literal['move_text']
    at: tuple[Coordinate,Coordinate]


class RerouteWire(ObjectEdit):
    kind: Literal['reroute_wire']
    points: list[tuple[Coordinate, Coordinate]] = Field(min_length=2, max_length=20)


class AddSymbol(ObjectEdit):
    kind: Literal['add_symbol']
    reference: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]*[0-9]+$', max_length=64)
    at: tuple[Coordinate, Coordinate]
    value: str = Field(min_length=1, max_length=2000)
    footprint: str = Field(min_length=1, max_length=500)
    datasheet: str = Field(default='~', max_length=2000)


class SheetEdit(StrictModel):
    sheet: str = Field(min_length=1, max_length=500)
    expectedSheetHash: str = Field(pattern=r'^[0-9a-f]{64}$')


class TidyFields(SheetEdit):
    kind: Literal['tidy_fields']
    symbolIds: list[str] = Field(min_length=1, max_length=50)
    stepMm: float = Field(default=1.27, ge=.635, le=2.54, allow_inf_nan=False)
    maxShiftMm: float = Field(default=5.08, ge=.635, le=12.7, allow_inf_nan=False)


class AddWire(SheetEdit):
    kind: Literal['add_wire']
    points: list[tuple[Coordinate, Coordinate]] = Field(min_length=2, max_length=20)


class ConnectLocalLabels(SheetEdit):
    kind: Literal['connect_local_labels']
    labelIds: list[str] = Field(min_length=2, max_length=80)
    maxLengthMm: float = Field(default=25.4, ge=1.27, le=100, allow_inf_nan=False)


class RemoveWire(ObjectEdit):
    kind: Literal['remove_wire']


class AddJunction(SheetEdit):
    kind: Literal['add_junction']
    at: tuple[Coordinate, Coordinate]


class RemoveJunction(ObjectEdit):
    kind: Literal['remove_junction']


class MoveSymbol(ObjectEdit):
    kind: Literal['move_symbol']
    at: tuple[Coordinate, Coordinate]


class RotateSymbol(ObjectEdit):
    kind: Literal['rotate_symbol']
    rotation: Literal[0, 90, 180, 270]


class MoveGroup(SheetEdit):
    kind: Literal['move_group']
    objectIds: list[str] = Field(min_length=1, max_length=300)
    delta: tuple[Coordinate, Coordinate]


class RemoveSymbol(ObjectEdit):
    kind: Literal['remove_symbol']


class AddLabel(SheetEdit):
    kind: Literal['add_label']
    text: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_+.-]{0,79}$')
    at: tuple[Coordinate, Coordinate]
    rotation: Literal[0, 90, 180, 270] = 0


class AddGlobalLabel(AddLabel):
    kind: Literal['add_global_label']


class AddSheet(SheetEdit):
    kind: Literal['add_sheet']
    filename: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}\.kicad_sch$')
    name: str = Field(min_length=1,max_length=120)
    title: str = Field(min_length=1,max_length=200)
    paper: Literal['A4','A3','A2','A1','A0'] = 'A3'
    at: tuple[Coordinate,Coordinate]
    size: tuple[Annotated[float,Field(ge=10,le=500,allow_inf_nan=False)],Annotated[float,Field(ge=10,le=500,allow_inf_nan=False)]]


class RemoveLabel(ObjectEdit):
    kind: Literal['remove_label']


class AddNoConnect(SheetEdit):
    kind: Literal['add_no_connect']
    at: tuple[Coordinate, Coordinate]


class RemoveNoConnect(ObjectEdit):
    kind: Literal['remove_no_connect']


class AddText(SheetEdit):
    kind: Literal['add_text']
    text: str = Field(min_length=1, max_length=4000)
    at: tuple[Coordinate, Coordinate]


class SetDocumentDate(SheetEdit):
    kind: Literal['set_document_date']
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')


class InitializeSchematic(StrictModel):
    expectedProjectVersion: int = Field(ge=1)
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    title: str = Field(min_length=1, max_length=200)
    paper: Literal['A4', 'A3', 'A2', 'A1', 'A0'] = 'A3'
    requirements: str = Field(min_length=1, max_length=20000)
    decisions: dict[str, Any] = Field(default_factory=dict)
    message: str = Field(min_length=1, max_length=1000)
    timeoutSeconds: int = Field(default=180, ge=15, le=900)
    requestId: str = Field(min_length=1, max_length=160)


class ProbeLibraryRequest(StrictModel):
    sourcePath: str = Field(min_length=1, max_length=1000)
    projectId: str | None = None


class LibrarySource(StrictModel):
    sourcePath: str = Field(min_length=1, max_length=1000)
    expectedSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    sourceUri: str = Field(min_length=1, max_length=2000)
    sourceVersion: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    license: str = Field(default='未声明', min_length=1, max_length=2000)


class ImportSymbolLibrary(LibrarySource):
    kind: Literal['import_symbol_library']


class ImportFootprint(LibrarySource):
    kind: Literal['import_footprint']


class ImportLibraryRequest(LibrarySource):
    revisionId: str = Field(min_length=1)
    expectedProjectVersion: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=1000)
    timeoutSeconds: int = Field(default=180, ge=15, le=900)
    requestId: str = Field(min_length=1, max_length=160)


class AddLibrarySymbol(SheetEdit):
    kind: Literal['add_library_symbol']
    libraryId: str = Field(pattern=r'^FF_[0-9a-f]{16}$')
    librarySha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    symbol: str = Field(min_length=1, max_length=300)
    symbolSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reference: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]*[0-9]+$', max_length=64)
    at: tuple[Coordinate, Coordinate]
    rotation: Literal[0, 90, 180, 270] = 0
    value: str = Field(min_length=1, max_length=2000)
    footprint: str = Field(min_length=1, max_length=500)
    datasheet: str = Field(default='~', max_length=2000)


class AddPowerFlag(SheetEdit):
    kind: Literal['add_power_flag']
    libraryId: str = Field(pattern=r'^FF_[0-9a-f]{16}$')
    librarySha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    symbolSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reference: str = Field(pattern=r'^#FLG[0-9]+$',max_length=64)
    at: tuple[Coordinate, Coordinate]


class PinRef(StrictModel):
    reference: str = Field(min_length=1, max_length=100)
    pin: str = Field(min_length=1, max_length=100)


class ExpectedConnectivity(StrictModel):
    mode: Literal['replace_groups']
    baselineHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    removeGroups: list[str] = Field(default_factory=list, max_length=300)
    addGroups: list[list[PinRef]] = Field(default_factory=list, max_length=300)


class SchematicBranchPin(StrictModel):
    reference: str = Field(min_length=1, max_length=80)
    pin: str = Field(min_length=1, max_length=80)


class SchematicBranch(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    kind: Literal['series', 'divider', 'decoupling', 'pull-up', 'pull-down', 'protection', 'driver']
    source: SchematicBranchPin
    target: SchematicBranchPin
    evidence: str = Field(min_length=1, max_length=2000)
    allowedRotations: list[Literal[0, 90, 180, 270]] = Field(default_factory=lambda: [0, 90, 180, 270], min_length=1, max_length=4)
    tapSide: Literal['left','right','top','bottom'] | None = None
    moveTarget: bool = True
    allowCrossings: bool = False


class SchematicLayoutIntent(StrictModel):
    version: Literal[1] = 1
    branches: list[SchematicBranch] = Field(min_length=1, max_length=80)
    cleanup: bool = False


class SchematicFunctionalGroup(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,60}$')
    name: str = Field(min_length=1,max_length=60)
    page: str | None = Field(default=None,min_length=1,max_length=40)
    references: list[str] = Field(min_length=1,max_length=100)


class SchematicRepetition(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,60}$')
    groups: list[str] = Field(min_length=2,max_length=32)
    columns: int = Field(default=2,ge=1,le=8)


class SchematicPageNetWeight(StrictModel):
    net: str = Field(min_length=1,max_length=200)
    weight: float = Field(ge=0,le=100)
    evidence: str = Field(min_length=1,max_length=2000)


class SchematicPagePlanning(StrictModel):
    version: Literal[1] = 1
    maxPages: int = Field(default=9,ge=1,le=9)
    pagePrefix: str = Field(default='Functions',min_length=1,max_length=30)
    samePage: list[Annotated[list[str], Field(min_length=2,max_length=40)]] = Field(default_factory=list,max_length=40)
    netWeights: list[SchematicPageNetWeight] = Field(default_factory=list,max_length=100)
    maxCandidates: int = Field(default=2000,ge=20,le=10000)


class SchematicOrganization(StrictModel):
    version: Literal[1] = 1
    groups: list[SchematicFunctionalGroup] = Field(min_length=1,max_length=40)
    repetitions: list[SchematicRepetition] = Field(default_factory=list,max_length=10)
    hierarchicalInterfaces: bool = True
    pagePlanning: SchematicPagePlanning | None = Field(default=None,exclude_if=lambda value:value is None)


class SchematicExistingBlock(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,60}$')
    name: str = Field(min_length=1,max_length=60)
    objectIds: list[str] = Field(min_length=1,max_length=500)


class SchematicSheetBlocks(SheetEdit):
    groups: list[SchematicExistingBlock] = Field(min_length=1,max_length=100)
    columns: int = Field(default=2,ge=1,le=8)
    affectSharedInstances: bool = False


class SchematicBlockLayout(StrictModel):
    version: Literal[1] = 1
    paper: Literal['A4','A3','A2','A1','A0'] | None = None
    sheets: list[SchematicSheetBlocks] = Field(min_length=1,max_length=40)
    preservePositions: bool = False
    localConnections: bool = False
    tidyFields: bool = False
    cleanupLabels: bool = False


class AutoLayoutSchematic(StrictModel):
    kind: Literal['auto_layout']
    preserveSheet: bool = False
    groupTitles: list[str] = Field(default_factory=list,max_length=500)
    paper: Literal['A4','A3','A2','A1','A0'] | None = None
    maxSymbolsPerSheet: int | None = Field(default=None,ge=1,le=500)
    layoutStrategy: Literal['readable','balanced','fast'] | None = None
    groups: list[list[str]] = Field(default_factory=list,max_length=500)
    intent: SchematicLayoutIntent | None = None
    organization: SchematicOrganization | None = None
    blockLayout: SchematicBlockLayout | None = None


SchematicOperation = Annotated[AutoLayoutSchematic | SetProperty | MoveField | TidyFields | ConnectLocalLabels | SetText | MoveText | RerouteWire | AddSymbol | AddWire |
                              RemoveWire | AddJunction | RemoveJunction | ImportSymbolLibrary | ImportFootprint |
                              AddLibrarySymbol | MoveSymbol | RotateSymbol | MoveGroup | RemoveSymbol | AddLabel | RemoveLabel |
                              AddNoConnect | RemoveNoConnect | AddText | AddPowerFlag | SetDocumentDate | AddSheet | AddGlobalLabel, Field(discriminator='kind')]


class AdoptSchematicCandidate(StrictModel):
    expectedProjectVersion: int = Field(ge=1)
    baseRevisionId: str = Field(min_length=1)
    jobId: str = Field(min_length=1)


class EditSchematic(StrictModel):
    revisionId: str = Field(min_length=1)
    expectedProjectVersion: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=1000)
    operations: list[SchematicOperation] = Field(min_length=1, max_length=1500)
    adoption: Literal['automatic','review'] = Field(default='automatic',exclude_if=lambda v:v=='automatic')
    connectivity: ExpectedConnectivity | None = None
    includeDocumentation: bool | None = None
    includeBaselineDiagnostics: bool = Field(default=False,exclude_if=lambda v:not v)
    timeoutSeconds: int = Field(default=180, ge=15, le=900)
    requestId: str = Field(min_length=1, max_length=160)

    @model_validator(mode='after')
    def local_connections_preserve(self):
        if any(op.kind == 'move_group' for op in self.operations):
            if len(self.operations) != 1 or self.connectivity is not None:
                raise ValueError('成组移动须单独成批并保持完整原生连接。')
        if any(op.kind == 'connect_local_labels' for op in self.operations):
            if len(self.operations) != 1 or self.connectivity is not None:
                raise ValueError('局部连线须单独成批并保持原有连接，不接受连接替换目标。')
        return self


class ReviewSource(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    path: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    url: str = Field(min_length=1, max_length=2000)
    version: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=2000)


class ReviewFact(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    value: float = Field(allow_inf_nan=False, ge=-1e12, le=1e12)
    unit: str = Field(min_length=1, max_length=40)
    basis: Literal['datasheet_limit', 'typical', 'engineering_allowance', 'requirement']
    sourceId: str
    note: str = Field(min_length=1, max_length=2000)


class ReviewCalculation(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    expression: str = Field(min_length=1, max_length=500)
    operator: Literal['le', 'ge']
    limit: str = Field(min_length=1, max_length=500)
    unit: str = Field(min_length=1, max_length=40)


class ReviewProperty(StrictModel):
    reference: str
    name: Literal['Value', 'Footprint', 'Datasheet']
    expected: str = Field(min_length=1, max_length=2000)


class ReviewNet(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    mode: Literal['same_net', 'different_nets', 'exact_net'] = Field(default='same_net', exclude_if=lambda v: v == 'same_net')
    pins: list[PinRef] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def distinct_pins(self):
        keys = [(p.reference, p.pin) for p in self.pins]
        if len(set(keys)) != len(keys):
            raise ValueError('连接断言不能包含重复引脚。')
        if self.mode != 'exact_net' and len(keys) < 2:
            raise ValueError('同网/异网断言至少需要两个引脚。')
        return self


class LayoutMember(StrictModel):
    role: str = Field(min_length=1, max_length=100)
    reference: str = Field(min_length=1, max_length=100)
    fixedAt: tuple[Coordinate, Coordinate] | None = None


class ReviewLayoutGroup(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    title: str = Field(min_length=1, max_length=200)
    members: list[LayoutMember] = Field(min_length=1, max_length=80)
    anchorRole: str = Field(min_length=1, max_length=100)
    repeatOf: str | None = Field(default=None, max_length=64)
    toleranceMm: float = Field(default=.05, ge=0, le=2, allow_inf_nan=False)
    interfaces: list[PinRef] = Field(default_factory=list, max_length=100)
    intent: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def valid_roles(self):
        roles=[m.role for m in self.members];refs=[m.reference for m in self.members]
        if len(set(roles))!=len(roles) or len(set(refs))!=len(refs) or self.anchorRole not in roles:
            raise ValueError('功能组角色和位号须唯一且包含锚点角色。')
        keys=[(p.reference,p.pin) for p in self.interfaces]
        if len(set(keys))!=len(keys) or any(p.reference not in refs for p in self.interfaces):
            raise ValueError('接口引脚须唯一且属于功能组成员。')
        return self


class PinPresentationReview(PinRef):
    sheet: str = Field(min_length=1,max_length=500)
    reviewedRevisionId: str = Field(min_length=1,max_length=100)
    expectedSheetHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    decision: Literal['keep_label','accepted_layout','needs_layout']
    reason: str = Field(min_length=1,max_length=2000)

    @model_validator(mode='after')
    def meaningful_reason(self):
        if not self.reason.strip():raise ValueError('逐引脚复核理由不能为空白。')
        return self


class ReviewSchematic(StrictModel):
    revisionId: str
    expectedProjectVersion: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    scope: str = Field(min_length=1, max_length=3000)
    sources: list[ReviewSource] = Field(min_length=1, max_length=20)
    facts: list[ReviewFact] = Field(min_length=1, max_length=150)
    calculations: list[ReviewCalculation] = Field(min_length=1, max_length=100)
    properties: list[ReviewProperty] = Field(min_length=1, max_length=600)
    connections: list[ReviewNet] = Field(min_length=1, max_length=200)
    layoutGroups: list[ReviewLayoutGroup] = Field(default_factory=list, max_length=100, exclude_if=lambda v:not v)
    pinReviews: list[PinPresentationReview] = Field(default_factory=list,max_length=600,exclude_if=lambda v:not v)
    conditions: list[str] = Field(min_length=1, max_length=50)
    hardwareVerification: list[str] = Field(min_length=1, max_length=50)
    timeoutSeconds: int = Field(default=180, ge=15, le=900)
    requestId: str = Field(min_length=1, max_length=160)


class PcbHandoff(StrictModel):
    revisionId: str
    expectedProjectVersion: int = Field(ge=1)
    reviewJobId: str
    timeoutSeconds: int = Field(default=180, ge=15, le=900)
    requestId: str = Field(min_length=1, max_length=160)


BoardCoordinate = Annotated[float, Field(ge=-1000, le=1000, allow_inf_nan=False)]
CopperLayer = Literal['F.Cu','In1.Cu','In2.Cu','B.Cu']


class StackupCopper(StrictModel):
    name: CopperLayer
    thickness: float = Field(ge=.009,le=.21,allow_inf_nan=False)


class StackupDielectric(StrictModel):
    type: Literal['core','prepreg']
    thickness: float = Field(ge=.02,le=3.2,allow_inf_nan=False)
    material: str = Field(min_length=1,max_length=100)
    epsilonR: float = Field(ge=1,le=20,allow_inf_nan=False)
    lossTangent: float | None = Field(default=None,ge=0,le=.2,allow_inf_nan=False)


class StackupProfile(StrictModel):
    name: str = Field(min_length=1,max_length=150)
    nominalThickness: float = Field(ge=.4,le=3.2,allow_inf_nan=False)
    thicknessTolerance: float = Field(ge=0,le=.2,allow_inf_nan=False)
    thicknessNote: str = Field(max_length=2000)
    copper: list[StackupCopper] = Field(min_length=2,max_length=4)
    dielectrics: list[StackupDielectric] = Field(min_length=1,max_length=3)
    source: ReviewSource


class SetStackup(StrictModel):
    kind: Literal['set_stackup']
    profile: StackupProfile


class BoardOutline(StrictModel):
    origin: tuple[BoardCoordinate, BoardCoordinate] = (0, 0)
    width: float = Field(ge=5, le=1000, allow_inf_nan=False)
    height: float = Field(ge=5, le=1000, allow_inf_nan=False)
    cornerRadius: float = Field(default=3, ge=0, le=500, allow_inf_nan=False,
        description='默认3mm圆角；小板默认不超过短边1/4。仅用户明确要求直角时设0，并填写squareCornerReason。')
    squareCornerReason: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def corners(self):
        if 'cornerRadius' not in self.model_fields_set:self.cornerRadius=min(3.,self.width/4,self.height/4)
        if self.cornerRadius>min(self.width,self.height)/2:raise ValueError('圆角半径不能大于短边的一半。')
        if self.cornerRadius==0 and not self.squareCornerReason.strip():raise ValueError('直角板框须填写用户明确要求的squareCornerReason。')
        return self


class ZoneOutline(StrictModel):
    origin: tuple[BoardCoordinate, BoardCoordinate] = (0, 0)
    width: float = Field(ge=.1, le=1000, allow_inf_nan=False)
    height: float = Field(ge=.1, le=1000, allow_inf_nan=False)


class BoardPlacement(StrictModel):
    reference: str = Field(min_length=1, max_length=64)
    at: tuple[BoardCoordinate, BoardCoordinate]
    rotation: Literal[0, 90, 180, 270] = 0


class BoardRules(StrictModel):
    clearance: float = Field(default=.127, ge=.1, le=5, allow_inf_nan=False)
    trackWidth: float = Field(default=.127, ge=.1, le=5, allow_inf_nan=False)
    viaDiameter: float = Field(default=.45, ge=.3, le=5, allow_inf_nan=False)
    viaDrill: float = Field(default=.3, ge=.15, le=3, allow_inf_nan=False)
    copperEdge: float = Field(default=.3, ge=.1, le=5, allow_inf_nan=False)
    holeClearance: float = Field(default=.25, ge=.1, le=5, allow_inf_nan=False)
    zoneMinimumThickness: float | None = Field(default=None, ge=.1, le=2, allow_inf_nan=False, exclude_if=lambda v:v is None)
    zoneThermalSpoke: float | None = Field(default=None, ge=.1, le=5, allow_inf_nan=False, exclude_if=lambda v:v is None)


    @model_validator(mode='after')
    def via_annulus(self):
        if self.viaDiameter-self.viaDrill < .15-1e-9:
            raise ValueError('过孔须保留至少0.075mm单边铜环。')
        return self


class SetBoardRules(StrictModel):
    kind: Literal['set_board_rules']
    rules: BoardRules
    reason: str = Field(min_length=1, max_length=1000)


class SetZoneRules(StrictModel):
    kind: Literal['set_zone_rules']
    minimumThickness: float = Field(ge=.1, le=2, allow_inf_nan=False)
    thermalSpoke: float = Field(ge=.1, le=5, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=1000)


class ReplaceExistingBoard(StrictModel):
    expectedBoardHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1,max_length=1000)


class PcbMountingPlan(StrictModel):
    mode: Literal['four-corner','custom','none'] = 'four-corner'
    profile: Literal['M2','M2.5','M3'] = 'M2.5'
    edgeInsetMm: float = Field(default=4,ge=2,le=30,allow_inf_nan=False)
    maxInsetMm: float = Field(default=10,ge=2,le=40,allow_inf_nan=False)
    envelopeDiameterMm: float = Field(default=6,ge=3,le=20,allow_inf_nan=False)
    positions: list[tuple[BoardCoordinate,BoardCoordinate]] = Field(default_factory=list,max_length=16)
    reason: str = Field(default='',max_length=2000)
    source: str = Field(default='',max_length=2000)

    @model_validator(mode='after')
    def explicit_exception(self):
        if self.maxInsetMm<self.edgeInsetMm:raise ValueError('最大内缩不能小于首选内缩。')
        if self.mode!='four-corner' and (not self.reason.strip() or not self.source.strip()):
            raise ValueError('自定义孔位或无孔例外须明确reason和source。')
        if (self.mode=='custom') != bool(self.positions):raise ValueError('仅custom模式提供实际孔位。')
        if len(set(self.positions))!=len(self.positions):raise ValueError('孔位不能重复。')
        if self.envelopeDiameterMm<{'M2':2.2,'M2.5':2.7,'M3':3.2}[self.profile]:raise ValueError('固定包络不能小于钻孔。')
        return self


class InitializePcb(StrictModel):
    revisionId: str
    expectedProjectVersion: int = Field(ge=1)
    handoffJobId: str
    outline: BoardOutline
    placements: list[BoardPlacement] = Field(min_length=1, max_length=1000)
    mountingPlan: PcbMountingPlan = Field(default_factory=PcbMountingPlan)
    rules: BoardRules | None = None
    thickness: float = Field(default=1.6, ge=.4, le=3.2, allow_inf_nan=False)
    replaceExisting: ReplaceExistingBoard | None = Field(default=None, exclude_if=lambda v:v is None)
    message: str = Field(min_length=1, max_length=1000)
    include3d: bool = True
    timeoutSeconds: int = Field(default=300, ge=30, le=900)
    requestId: str = Field(min_length=1, max_length=160)


class BoardObjectEdit(StrictModel):
    objectId: str = Field(min_length=1, max_length=100)
    expectedHash: str = Field(pattern=r'^[0-9a-f]{64}$')


class MoveFootprint(BoardObjectEdit):
    kind: Literal['move_footprint']
    at: tuple[BoardCoordinate, BoardCoordinate]
    rotation: Literal[0, 90, 180, 270]
    layer: Literal['F.Cu', 'B.Cu'] | None = Field(default=None, description='Optional mounting side; omitted preserves the current side. Rotation is the absolute native angle after an explicit top-bottom flip.')


class SetBoardOutline(StrictModel):
    kind: Literal['set_outline']
    outline: BoardOutline


class AddTrack(StrictModel):
    kind: Literal['add_track']
    net: str = Field(min_length=1, max_length=300)
    points: list[tuple[BoardCoordinate, BoardCoordinate]] = Field(min_length=2, max_length=100)
    layer: CopperLayer
    width: float = Field(ge=.1, le=5, allow_inf_nan=False)


class RemoveTrack(BoardObjectEdit):
    kind: Literal['remove_track']


class AddVia(StrictModel):
    kind: Literal['add_via']
    net: str = Field(min_length=1, max_length=300)
    at: tuple[BoardCoordinate, BoardCoordinate]
    diameter: float = Field(ge=.3, le=5, allow_inf_nan=False)
    drill: float = Field(ge=.15, le=3, allow_inf_nan=False)


class RemoveVia(BoardObjectEdit):
    kind: Literal['remove_via']


class SetViaRules(StrictModel):
    kind: Literal['set_via_rules']
    diameter: float = Field(ge=.3,le=5,allow_inf_nan=False)
    drill: float = Field(ge=.15,le=3,allow_inf_nan=False)
    reason: str = Field(min_length=1,max_length=1000)

    @model_validator(mode='after')
    def annulus(self):
        if self.diameter-self.drill<.15-1e-9:raise ValueError('通孔单边铜环至少0.075mm。')
        return self


class MountingSource(StrictModel):
    path: str = Field(min_length=1,max_length=2000)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    uri: str = Field(min_length=1,max_length=2000)
    version: str = Field(min_length=1,max_length=300)
    license: str = Field(min_length=1,max_length=1000)
    note: str = Field(min_length=1,max_length=2000)


class AddMountingHole(StrictModel):
    kind: Literal['add_mounting_hole']
    reference: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,39}$')
    at: tuple[BoardCoordinate,BoardCoordinate]
    drill: float = Field(ge=.8,le=10,allow_inf_nan=False)
    source: MountingSource


class RemoveMountingHole(BoardObjectEdit):
    kind: Literal['remove_mounting_hole']


class AddZone(StrictModel):
    kind: Literal['add_zone']
    net: str = Field(min_length=1,max_length=300)
    layer: CopperLayer
    outline: ZoneOutline
    clearance: float = Field(default=.127,ge=.1,le=5,allow_inf_nan=False)
    minimumThickness: float = Field(default=.2,ge=.1,le=2,allow_inf_nan=False)
    thermalGap: float = Field(default=.2,ge=.1,le=5,allow_inf_nan=False)
    thermalSpoke: float = Field(default=.2,ge=.1,le=5,allow_inf_nan=False)
    priority: int = Field(default=0,ge=0,le=100)
    padConnection: Literal['thermal','full'] = 'thermal'


class RemoveZone(BoardObjectEdit):
    kind: Literal['remove_zone']


class ReleasePair(StrictModel):
    kind: Literal['release_pair']
    name: str = Field(min_length=1,max_length=120)
    expectedHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1,max_length=1000)


class SetFootprintText(BoardObjectEdit):
    kind: Literal['set_footprint_text']
    field: Literal['Reference','Value']
    at: tuple[BoardCoordinate,BoardCoordinate]
    rotation: Literal[0,90,180,270]
    visible: bool = True


class AddBoardText(StrictModel):
    kind: Literal['add_board_text']
    text: str = Field(min_length=1,max_length=120,pattern=r'^[^\x00-\x1f\x7f]+$')
    at: tuple[BoardCoordinate,BoardCoordinate]
    layer: Literal['F.Silkscreen','B.Silkscreen'] = 'F.Silkscreen'
    rotation: Literal[0,90,180,270] = 0
    size: float = Field(default=1,ge=.6,le=5,allow_inf_nan=False)
    thickness: float = Field(default=.15,ge=.08,le=.5,allow_inf_nan=False)


class RemoveBoardText(BoardObjectEdit):
    kind: Literal['remove_board_text']


class SetFootprintSilkscreenLine(BoardObjectEdit):
    kind: Literal['set_footprint_silkscreen_line']
    start: tuple[BoardCoordinate,BoardCoordinate]
    end: tuple[BoardCoordinate,BoardCoordinate]
    reason: str = Field(min_length=1,max_length=1000)


class RemoveFootprintSilkscreenLine(BoardObjectEdit):
    kind: Literal['remove_footprint_silkscreen_line']
    reason: str = Field(min_length=1,max_length=1000)


class RemoveFootprintSilkscreenPolygon(BoardObjectEdit):
    kind: Literal['remove_footprint_silkscreen_polygon']
    reason: str = Field(min_length=1,max_length=1000)


class CleanupTracks(StrictModel):
    kind: Literal['cleanup_tracks']
    nets: list[str] = Field(min_length=1,max_length=50)


class PadEscapeTarget(PinRef):
    width: float = Field(gt=0,le=10,allow_inf_nan=False)
    clearance: float = Field(gt=0,le=10,allow_inf_nan=False)


class EscapeContactRework(StrictModel):
    objects: list[BoardObjectEdit] = Field(min_length=2,max_length=9)
    reason: str = Field(min_length=1,max_length=1000)
    maxOffsetMm: float = Field(default=.25,gt=0,le=1,allow_inf_nan=False)


class OptimizeTracks(StrictModel):
    kind: Literal['optimize_tracks']
    nets: list[str] = Field(min_length=1,max_length=50)
    reason: str = Field(min_length=1,max_length=1000)
    maxChains: int = Field(default=12,ge=1,le=40)
    searchSeconds: int = Field(default=20,ge=1,le=120)
    escapeTargets: list[PadEscapeTarget] = Field(default_factory=list,max_length=16,exclude_if=lambda v:not v)
    contactRework: EscapeContactRework | None = Field(default=None,exclude_if=lambda v:v is None)


class OptimizeInterfaceSilkscreen(StrictModel):
    kind: Literal['optimize_interface_silkscreen']
    references: list[str] = Field(min_length=1,max_length=16)
    maxOffsetMm: float = Field(default=8,ge=.5,le=20)


class OptimizeReferenceSilkscreen(StrictModel):
    kind: Literal['optimize_reference_silkscreen']
    references: list[str] = Field(min_length=1,max_length=64)
    maxOffsetMm: float = Field(default=4,ge=.5,le=10,allow_inf_nan=False)

    @model_validator(mode='after')
    def unique_refs(self):
        if len(set(self.references))!=len(self.references):raise ValueError('丝印位号不能重复。')
        return self


BoardOperation = Annotated[SetBoardRules | MoveFootprint | SetBoardOutline | AddTrack | RemoveTrack | CleanupTracks | OptimizeTracks | OptimizeInterfaceSilkscreen | OptimizeReferenceSilkscreen | AddVia | RemoveVia | SetViaRules | AddZone | RemoveZone | ReleasePair | SetFootprintText | AddBoardText | RemoveBoardText | SetFootprintSilkscreenLine | RemoveFootprintSilkscreenLine | RemoveFootprintSilkscreenPolygon | SetStackup | AddMountingHole | RemoveMountingHole | SetZoneRules, Field(discriminator='kind')]


class PowerFeedPin(StrictModel):
    reference: str = Field(min_length=1,max_length=64)
    pin: str = Field(min_length=1,max_length=64)


class PowerFeedRule(StrictModel):
    name: str = Field(min_length=1,max_length=100)
    source: PowerFeedPin
    capacitor: PowerFeedPin
    loads: list[PowerFeedPin] = Field(min_length=1,max_length=64)
    returnNet: str = Field(min_length=1,max_length=300)

    @model_validator(mode='after')
    def distinct_anchors(self):
        pins=[(p.reference,p.pin)for p in [self.source,self.capacitor,*self.loads]]
        if len(set(pins))!=len(pins):raise ValueError('供电源、电容节点和负载端子不能重复。')
        return self


class RoutingStyle(StrictModel):
    maxAddedLengthMm: float = Field(default=2.,ge=0,le=10,allow_inf_nan=False)
    maxAddedLengthRatio: float = Field(default=.15,ge=0,le=.5,allow_inf_nan=False)
    bendPenaltyMm: float = Field(default=.25,ge=0,le=2,allow_inf_nan=False)
    padExitLengthMm: float = Field(default=.15,ge=0,le=1,allow_inf_nan=False)


class InterfacePinLegend(StrictModel):
    pin: str = Field(min_length=1,max_length=64)
    net: str = Field(max_length=300)
    label: str = Field(min_length=1,max_length=120,pattern=r'^[^\x00-\x1f\x7f]+$')

    @model_validator(mode='after')
    def numbered_label(self):
        if not self.label.startswith(self.pin+':'):
            raise ValueError('逐针丝印使用真实焊盘编号:信号，例如2:SWDIO。')
        return self


class InterfaceLegend(StrictModel):
    reference: str = Field(min_length=1,max_length=64)
    purpose: str = Field(min_length=1,max_length=300)
    title: str = Field(min_length=1,max_length=120,pattern=r'^[^\x00-\x1f\x7f]+$')
    layer: Literal['F.Silkscreen','B.Silkscreen'] = 'F.Silkscreen'
    pins: list[InterfacePinLegend] = Field(min_length=1,max_length=100)
    maxPinDistanceMm: float = Field(default=8,gt=0,le=30,allow_inf_nan=False)
    maxTitleDistanceMm: float = Field(default=20,gt=0,le=50,allow_inf_nan=False)
    minTextSizeMm: float = Field(default=.8,ge=.6,le=5,allow_inf_nan=False)
    minStrokeMm: float = Field(default=.12,ge=.08,le=.5,allow_inf_nan=False)

    @model_validator(mode='after')
    def unique_pins(self):
        if len({p.pin for p in self.pins}) != len(self.pins):
            raise ValueError('接口丝印针号不能重复。')
        return self


class EditPcb(StrictModel):
    revisionId: str
    expectedProjectVersion: int = Field(ge=1)
    expectedBoardHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    operations: list[BoardOperation] = Field(default_factory=list,max_length=500)
    mountingPlan: PcbMountingPlan | None = Field(default=None,exclude_if=lambda v:v is None)
    powerFeeds: list[PowerFeedRule] | None = Field(default=None,max_length=32,exclude_if=lambda v:v is None)
    interfaceLegends: list[InterfaceLegend] | None = Field(default=None,max_length=64,exclude_if=lambda v:v is None)
    message: str = Field(min_length=1, max_length=1000)
    include3d: bool = True
    timeoutSeconds: int = Field(default=300, ge=30, le=900)
    requestId: str = Field(min_length=1, max_length=160)

    @model_validator(mode='after')
    def unique_interfaces(self):
        if not self.operations and self.mountingPlan is None:raise ValueError('需要编辑操作或明确固定规划。')
        if self.interfaceLegends is not None and len({r.reference for r in self.interfaceLegends}) != len(self.interfaceLegends):
            raise ValueError('接口丝印位号不能重复。')
        return self


class YieldZone(StrictModel):
    objectId: str = Field(min_length=1,max_length=100)
    expectedHash: str = Field(pattern=r'^[0-9a-f]{64}$')


class ReplaceOrdinaryCopper(StrictModel):
    objects: list[BoardObjectEdit] = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)


class RouteNet(StrictModel):
    net: str = Field(min_length=1,max_length=300)
    width: float = Field(ge=.1,le=5,allow_inf_nan=False)
    clearance: float = Field(ge=.1,le=5,allow_inf_nan=False)
    allowVias: bool = True
    restoreSourceConnectionsOnly: bool = Field(default=False,exclude_if=lambda v:not v,
        description='Explicit reroute scope: restore only pad relationships connected in the immutable source. Existing disconnected groups remain reported; no width, clearance or acceptance rule changes.')
    layers: list[CopperLayer] = Field(default_factory=lambda:['F.Cu','B.Cu'],min_length=1,max_length=4)
    maxViasPerConnection: int = Field(default=2,ge=0,le=6)
    yieldZones: list[YieldZone] = Field(default_factory=list,max_length=32)
    replaceCopper: ReplaceOrdinaryCopper | None = Field(default=None, exclude_if=lambda v:v is None)


class LocalPlacementArea(StrictModel):
    origin: tuple[BoardCoordinate,BoardCoordinate]
    width: float = Field(gt=0,le=30,allow_inf_nan=False)
    height: float = Field(gt=0,le=30,allow_inf_nan=False)
    grid: float = Field(default=2,ge=.5,le=5,allow_inf_nan=False)


class RouteSearchArea(StrictModel):
    origin: tuple[BoardCoordinate,BoardCoordinate]
    width: float = Field(gt=0,le=1000,allow_inf_nan=False)
    height: float = Field(gt=0,le=1000,allow_inf_nan=False)


class LocalCopperRework(StrictModel):
    objects: list[BoardObjectEdit] = Field(min_length=1,max_length=8)
    reason: str = Field(min_length=1,max_length=1000)
    maxOffsetMm: float = Field(default=.5,gt=0,le=1,allow_inf_nan=False)


class LocalContactRework(StrictModel):
    objects: list[BoardObjectEdit] = Field(min_length=2,max_length=9,
        description='One existing via and all straight segments whose centreline reaches its centre, with source hashes. Existing finite side contacts are retained by native checks.')
    reason: str = Field(min_length=1,max_length=1000)
    maxOffsetMm: float = Field(default=.5,gt=0,le=1,allow_inf_nan=False)


class ExplicitFootprintPose(StrictModel):
    reference: str = Field(min_length=1,max_length=100)
    at: tuple[Coordinate,Coordinate]
    rotation: Literal[0,90,180,270]


class LocalPlacementOptimization(StrictModel):
    references: tuple[str,str]
    linkTopology: Literal['series-pair','shared-node','escape-cluster'] = Field(default='series-pair',exclude_if=lambda v:v=='series-pair',
        description='Explicit shared-node selects two ordinary two-terminal footprints within one multi-terminal net; all global pads, fixed external endpoints and source physical groups remain intact and fully checked.')
    companionReferences: list[str] = Field(default_factory=list,max_length=1,exclude_if=lambda v:not v)
    poseCandidates: list[list[ExplicitFootprintPose]] = Field(default_factory=list,max_length=4,exclude_if=lambda v:not v)
    copperRework: LocalCopperRework | None = Field(default=None,exclude_if=lambda v:v is None,
        description='Explicit bounded straight copper detours; preserve source width/layer and fixed contacts. Existing source is still an incumbent.')
    contactRework: LocalContactRework | None = Field(default=None,exclude_if=lambda v:v is None,
        description='Bounded via-contact movement with all attached straight legs rebuilt at their original widths and layers. Preserve the via UUID, hole and fixed outer contacts.')
    linkNet: str = Field(min_length=1,max_length=300)
    area: LocalPlacementArea
    connectedPadGapMm: float = Field(default=.5,ge=.2,le=3,allow_inf_nan=False,
        description='Target edge gap for re-packing. A small source-axis alignment can preserve an existing tighter axial gap without narrowing it; native design rules still apply.')
    maxCandidates: int = Field(default=8,ge=2,le=24)
    candidateSearchSeconds: int = Field(default=3,ge=1,le=60)
    minCopperImprovementMm: float = Field(default=.5,ge=0,le=10,allow_inf_nan=False)
    allowQuarterTurns: bool = True

    @model_validator(mode='after')
    def distinct_references(self):
        refs=[*self.references,*self.companionReferences]
        if len(set(refs))!=len(refs):raise ValueError('局部布局的器件位号不能重复。')
        if self.copperRework and self.contactRework:raise ValueError('单次局部比较选择铜链绕行或过孔接点调整之一。')
        if self.linkTopology=='escape-cluster':
            if not self.poseCandidates:raise ValueError('出口协调布局须提供明确有界姿态候选。')
            if self.copperRework or self.contactRework:raise ValueError('出口协调布局不叠加接点/障碍铜候选。')
            for pose in self.poseCandidates:
                if len(pose)!=len(refs) or {p.reference for p in pose}!=set(refs):raise ValueError('每个姿态须恰好覆盖全部选定器件。')
        elif self.poseCandidates:raise ValueError('明确姿态仅用于escape-cluster。')
        elif self.candidateSearchSeconds>10:raise ValueError('普通二端布局每候选最多10秒。')
        return self


class RoutePcb(StrictModel):
    revisionId: str
    expectedProjectVersion: int = Field(ge=1)
    expectedBoardHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    acceptanceScope: Literal['schematic-contract','native-source-preserving'] = Field(default='schematic-contract',
        exclude_if=lambda v:v=='schematic-contract',
        description='Native-only scope requires an explicitly marked board-only source; it never asserts ERC or schematic parity and can only adopt a PCB draft.')
    nets: list[RouteNet] = Field(min_length=1,max_length=64)
    grid: float = Field(default=.2,ge=.1,le=.5,allow_inf_nan=False)
    searchSeconds: int = Field(default=30,ge=1,le=180)
    maxConnections: int = Field(default=20,ge=1,le=64)
    maxExpansions: int = Field(default=80000,ge=100,le=300000)
    maxPairAttemptsPerConnection: int | None = Field(default=None,ge=1,le=12,exclude_if=lambda v:v is None)
    searchHeuristicWeight: float | None = Field(default=None,ge=1,le=2,allow_inf_nan=False,exclude_if=lambda v:v is None)
    viaCost: float = Field(default=3,ge=1,le=30,allow_inf_nan=False)
    routingStyle: RoutingStyle = Field(default_factory=RoutingStyle)
    bodyAvoidance: Literal['smd-pad-gap-v1'] | None = Field(default=None,exclude_if=lambda v:v is None)
    searchArea: RouteSearchArea | None = Field(default=None,exclude_if=lambda v:v is None,
        description='Explicit board-contained search window. It must contain every selected-net pad and existing copper endpoint; final native board checks remain global.')
    localPlacement: LocalPlacementOptimization | None = Field(default=None,exclude_if=lambda v:v is None)
    message: str = Field(min_length=1,max_length=1000)
    include3d: bool = True
    timeoutSeconds: int = Field(default=300,ge=60,le=900)
    requestId: str = Field(min_length=1,max_length=160)


class ReferenceAnchor(StrictModel):
    reference: str = Field(min_length=1,max_length=64)
    pin: str = Field(min_length=1,max_length=64)


class LayerReference(StrictModel):
    signalLayer: CopperLayer
    referenceLayer: CopperLayer


class TerminalAntipadPolicy(StrictModel):
    maxReturnDistance: float = Field(ge=.5,le=3,allow_inf_nan=False)
    maxClearance: float = Field(ge=.1,le=.5,allow_inf_nan=False)
    maxNativeError: float = Field(default=.01,ge=.001,le=.02,allow_inf_nan=False)
    reason: str = Field(min_length=1,max_length=1000)


class ReferenceCheck(StrictModel):
    revisionId: str
    referenceNet: str = Field(min_length=1,max_length=300)
    referenceLayer: CopperLayer
    layerReferences: list[LayerReference] = Field(default_factory=list,max_length=4)
    maxReturnViaDistance: float | None = Field(default=None,ge=.5,le=10,allow_inf_nan=False)
    terminalAntipads: TerminalAntipadPolicy | None = None
    anchor: ReferenceAnchor
    nets: list[str] = Field(min_length=1,max_length=64)
    margin: float = Field(default=.2,ge=0,le=2,allow_inf_nan=False)
    timeoutSeconds: int = Field(default=120,ge=15,le=300)
    requestId: str = Field(min_length=1,max_length=160)


class PadNetChange(StrictModel):
    reference: str = Field(min_length=1,max_length=64)
    pin: str = Field(min_length=1,max_length=64)
    beforeNet: str | None = Field(min_length=1,max_length=300)
    afterNet: str = Field(min_length=1,max_length=300)


class FootprintContact(StrictModel):
    padId: str = Field(min_length=1,max_length=100)
    number: str = Field(min_length=1,max_length=100)
    targetAt: tuple[BoardCoordinate,BoardCoordinate]


class FootprintReplacement(BoardObjectEdit):
    reference: str = Field(min_length=1,max_length=100)
    footprint: str = Field(min_length=1,max_length=500)
    footprintSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    at: tuple[BoardCoordinate,BoardCoordinate]
    rotation: Literal[0,90,180,270] = 0
    padMap: list[FootprintContact] = Field(min_length=1,max_length=500)
    releasedPadIds: list[str] = Field(default_factory=list,max_length=500)
    reason: str = Field(min_length=1,max_length=2000)


class SyncPcb(StrictModel):
    revisionId: str
    expectedProjectVersion: int = Field(ge=1)
    boardBaseRevisionId: str
    expectedBoardHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    handoffJobId: str
    expectedPadChanges: list[PadNetChange] = Field(default_factory=list,max_length=500)
    addedPlacements: list[BoardPlacement] = Field(default_factory=list,max_length=1000)
    footprintReplacements: list[FootprintReplacement] = Field(default_factory=list,max_length=100)
    message: str = Field(min_length=1,max_length=1000)
    include3d: bool = True
    timeoutSeconds: int = Field(default=300,ge=60,le=900)
    requestId: str = Field(min_length=1,max_length=160)


class PairRail(StrictModel):
    net: str = Field(min_length=1,max_length=300)
    source: ReferenceAnchor
    target: ReferenceAnchor


class PairedSection(StrictModel):
    name: str = Field(min_length=1,max_length=120)
    positive: PairRail
    negative: PairRail
    positiveSide: Literal['left','right']
    spine: list[tuple[BoardCoordinate,BoardCoordinate]] = Field(min_length=2,max_length=20)
    layer: CopperLayer
    width: float = Field(ge=.1,le=5,allow_inf_nan=False)
    gap: float = Field(ge=.1,le=5,allow_inf_nan=False)
    clearance: float = Field(ge=.1,le=5,allow_inf_nan=False)
    maxSkew: float = Field(ge=0,le=100,allow_inf_nan=False)
    maxLength: float = Field(gt=0,le=1000,allow_inf_nan=False)
    maxFanoutLength: float = Field(ge=0,le=100,allow_inf_nan=False)


class PairBranchTrim(BoardObjectEdit):
    endpoint: Literal['start','end']
    at: tuple[BoardCoordinate,BoardCoordinate]


class ReplacePairedSection(StrictModel):
    expectedHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1,max_length=1000)
    trimBranches: list[PairBranchTrim] = Field(default_factory=list,max_length=40)


class RoutePair(StrictModel):
    revisionId: str
    expectedProjectVersion: int = Field(ge=1)
    expectedBoardHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    pair: PairedSection
    replaceSection: ReplacePairedSection | None = None
    searchSeconds: int = Field(default=10,ge=1,le=30)
    message: str = Field(min_length=1,max_length=1000)
    include3d: bool = True
    timeoutSeconds: int = Field(default=300,ge=60,le=900)
    requestId: str = Field(min_length=1,max_length=160)


class MechanicalSource(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    path: str = Field(min_length=1,max_length=2000)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    uri: str = Field(min_length=1,max_length=2000)
    version: str = Field(min_length=1,max_length=300)
    license: str = Field(min_length=1,max_length=1000)
    note: str = Field(min_length=1,max_length=2000)


class PcbPassage(StrictModel):
    footprintId: str
    openingIndex: int = Field(ge=0,le=39)
    clearance: float = Field(ge=.05,le=5,allow_inf_nan=False)


class PcbFastenerEnvelope(StrictModel):
    evidencePath: str
    shaftDiameter: float = Field(gt=0,le=10,allow_inf_nan=False)
    headDiameter: float = Field(gt=0,le=20,allow_inf_nan=False)
    headHeight: float = Field(gt=0,le=10,allow_inf_nan=False)
    underHeadLength: float = Field(gt=0,le=30,allow_inf_nan=False)
    minEngagement: float = Field(gt=0,le=20,allow_inf_nan=False)
    minRadialClearance: float = Field(ge=.05,le=2,allow_inf_nan=False)
    minTipClearance: float = Field(ge=.1,le=5,allow_inf_nan=False)
    headSpaceClearance: float = Field(ge=.05,le=5,allow_inf_nan=False)


class DesignMechanical(StrictModel):
    expectedProjectVersion: int = Field(ge=1)
    expectedMechanicalRevisionId: str | None
    design: dict[str,Any]
    sources: list[MechanicalSource] = Field(default_factory=list,max_length=80)
    pcbRevisionId: str | None = None
    expectedBoardHash: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    pcbGeometryJobId: str | None = None
    pcbPart: str | None = Field(default=None,pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$',description='feature_model中完整PCBA的唯一输出零件；仅允许同SHA STEP经刚性变换，绑定后追踪PCB版本变化。')
    pcbPassages: list[PcbPassage] = Field(default_factory=list,max_length=40)
    pcbMounts: list[str] = Field(default_factory=list,max_length=40)
    pcbFastener: PcbFastenerEnvelope | None = None
    message: str = Field(min_length=1,max_length=1000)
    timeoutSeconds: int = Field(default=300,ge=30,le=10800,description='Single total budget for native build/export plus independent reconstruction and STEP verification; explicitly requested large complete-PCBA assemblies may use up to10800seconds. Default remains300seconds.')
    requestId: str = Field(min_length=1,max_length=160)

    @model_validator(mode='after')
    def bound_pcb(self):
        if (self.pcbRevisionId is None)!=(self.expectedBoardHash is None):raise ValueError('PCB dependency requires both revisionId and expectedBoardHash')
        if self.pcbGeometryJobId and not self.pcbRevisionId:raise ValueError('Geometry binding requires explicit PCB revision/hash')
        if self.pcbPart and (not self.pcbGeometryJobId or self.design.get('kind')!='feature_model' or self.pcbPassages or self.pcbMounts or self.pcbFastener):
            raise ValueError('pcbPart requires feature_model native geometry; enclosure passages/mounts do not apply')
        if self.pcbPassages and not self.pcbGeometryJobId:raise ValueError('Connector passages require native geometry binding')
        if self.pcbMounts and (not self.pcbGeometryJobId or len(set(self.pcbMounts))!=len(self.pcbMounts)):raise ValueError('PCB mounts require native geometry binding and unique feature IDs')
        if bool(self.pcbMounts)!=(self.pcbFastener is not None):raise ValueError('PCB mounting requires an explicit nominal fastener envelope and vice versa')
        return self


class PcbModelInput(StrictModel):
    footprintId: str
    expectedFootprintHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    modelIndex: int = Field(ge=0,le=20)
    expectedModelPath: str
    source: MechanicalSource


class ExportPcbGeometry(StrictModel):
    revisionId: str
    expectedBoardHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    models: list[PcbModelInput] = Field(default_factory=list,max_length=1000)
    scope: Literal['complete-pcba','selected-models','board-only'] = Field(default='complete-pcba',description='默认要求所有电气器件模型显式来源完整；局部或仅板体须明确选择，不自动推断许可/来源。')
    timeoutSeconds: int = Field(default=300,ge=30,le=900)
    requestId: str = Field(min_length=1,max_length=160)

    @model_validator(mode='after')
    def explicit_coverage(self):
        if self.scope=='board-only' and self.models:raise ValueError('board-only cannot select component models')
        if self.scope=='selected-models' and not self.models:raise ValueError('selected-models requires explicit model sources; use board-only for substrate alone')
        return self


from .adapters.insertion_spec import InsertionInterface
class CheckInsertion(StrictModel):
    revisionId:str
    expectedDesignSha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    interfaces:list[InsertionInterface]=Field(min_length=1,max_length=12)
    sources:list[MechanicalSource]=Field(min_length=1,max_length=80)
    timeoutSeconds:int=Field(default=300,ge=30,le=900)
    requestId:str=Field(min_length=1,max_length=160)
