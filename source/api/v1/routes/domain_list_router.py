from fastapi import APIRouter, File, HTTPException, UploadFile

from schemas.domain_list_schemas import (
    DomainDeleteResponse,
    DomainListResponse,
    DomainListsResponse,
    DomainListType,
    DomainListUploadResponse,
    DomainLookupResponse,
)
from utils.domain_list_storage import DomainListStorage, normalize_domain, parse_csv_domain_columns


router = APIRouter(prefix="/domain-lists", tags=["Domain Lists"])
storage = DomainListStorage()


@router.post("/upload", response_model=DomainListUploadResponse)
async def upload_domain_csv(file: UploadFile = File(...)):
    parsed_domains = await parse_csv_domain_columns(file)

    added: dict[str, int] = {}
    skipped: dict[str, int] = {}
    totals: dict[str, int] = {}

    for list_type in DomainListType:
        summary = storage.add_many(list_type.value, parsed_domains[list_type.value])
        added[list_type.value] = summary["added"]
        skipped[list_type.value] = summary["skipped"]
        totals[list_type.value] = summary["total"]

    return DomainListUploadResponse(
        message="CSV processed successfully",
        added=added,
        skipped=skipped,
        totals=totals,
    )


@router.get("", response_model=DomainListsResponse)
async def get_all_domain_lists():
    domains = storage.get_all()
    totals = {list_type: len(items) for list_type, items in domains.items()}
    return DomainListsResponse(totals=totals, domains=domains)


@router.get("/{list_type}", response_model=DomainListResponse)
async def get_domain_list(list_type: DomainListType):
    domains = storage.get_list(list_type.value)
    return DomainListResponse(list_type=list_type, total=len(domains), domains=domains)


@router.get("/{list_type}/domains/{domain:path}", response_model=DomainLookupResponse)
async def get_domain(list_type: DomainListType, domain: str):
    normalized_domain = normalize_domain(domain)
    return DomainLookupResponse(
        list_type=list_type,
        input_domain=domain,
        normalized_domain=normalized_domain,
        exists=storage.exists(list_type.value, normalized_domain),
    )


@router.delete("/{list_type}/domains/{domain:path}", response_model=DomainDeleteResponse)
async def delete_domain(list_type: DomainListType, domain: str):
    normalized_domain = normalize_domain(domain)
    deleted = storage.delete(list_type.value, normalized_domain)
    total = len(storage.get_list(list_type.value))

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"{normalized_domain} was not found in {list_type.value}",
        )

    return DomainDeleteResponse(
        success=True,
        list_type=list_type,
        normalized_domain=normalized_domain,
        message="Domain deleted successfully",
        total=total,
    )
