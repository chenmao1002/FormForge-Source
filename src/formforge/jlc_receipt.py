"""Internal receipt type; no HTTP/MCP input accepts this trusted path."""
from dataclasses import dataclass

API_EVIDENCE='provider-api-https-response'


@dataclass(frozen=True)
class VerifiedJlcObservation:
    jobId: str
    sourceBindingSha256: str
    apiIdentity: dict
    observation: dict
    responseSha256: str
