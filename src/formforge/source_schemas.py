"""Transport-only data contracts; registered input files are never executed."""
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field

CHUNK_BYTES = 192 * 1024


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RegisteredSourceRef(Strict):
    sourceId: str = Field(pattern=r'^s_[0-9a-f]{16}$')
    expectedSha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expectedProvenanceSha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class SourceMetadata(Strict):
    name: str = Field(min_length=1, max_length=120)
    uri: str = Field(min_length=1, max_length=2000)
    version: str = Field(min_length=1, max_length=200)
    license: str = Field(min_length=1, max_length=1000)
    note: str = Field(min_length=1, max_length=2000)
    origin: Literal['project-authored', 'supplier-declared', 'third-party-declared']


class BeginSource(SourceMetadata):
    action: Literal['begin']
    size: int = Field(ge=1, le=64 * 1024 * 1024)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    requestId: str = Field(min_length=1, max_length=160)


class RegisterTextSource(SourceMetadata):
    action: Literal['register_text']
    text: str = Field(min_length=1, max_length=CHUNK_BYTES)
    expectedSha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    requestId: str = Field(min_length=1, max_length=160)


class UploadAction(Strict):
    uploadId: str = Field(pattern=r'^u_[0-9a-f]{16}$')


class AppendSource(UploadAction):
    action: Literal['append']
    offset: int = Field(ge=0, le=64 * 1024 * 1024)
    contentBase64: str = Field(min_length=4, max_length=CHUNK_BYTES * 4 // 3)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class CommitSource(UploadAction):
    action: Literal['commit']


class AbortSource(UploadAction):
    action: Literal['abort']


class SourceStatus(UploadAction):
    action: Literal['status']


SourceTransfer = Annotated[Union[BeginSource, RegisterTextSource, AppendSource, CommitSource, AbortSource, SourceStatus], Field(discriminator='action')]
