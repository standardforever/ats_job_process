from enum import Enum
from typing import Dict, List

from pydantic import BaseModel, Field


class DomainListType(str, Enum):
    ATS = "ats"
    NON_ATS = "non_ats"


class DomainListUploadResponse(BaseModel):
    success: bool = True
    message: str
    added: Dict[str, int]
    skipped: Dict[str, int]
    totals: Dict[str, int]


class DomainListResponse(BaseModel):
    list_type: DomainListType
    total: int
    domains: List[str]


class DomainListsResponse(BaseModel):
    totals: Dict[str, int]
    domains: Dict[str, List[str]]


class DomainLookupResponse(BaseModel):
    list_type: DomainListType
    input_domain: str = Field(..., description="Original domain sent by the client")
    normalized_domain: str
    exists: bool


class DomainDeleteResponse(BaseModel):
    success: bool
    list_type: DomainListType
    normalized_domain: str
    message: str
    total: int
