import csv
import json
import io
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse

import tldextract
from fastapi import HTTPException, UploadFile


LIST_TYPES = ("ats", "non_ats")
TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=None)


class DomainListStorage:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or Path(__file__).resolve().parents[1] / "domain_lists"
        self.base_dir.mkdir(parents=True, exist_ok=True)

        for list_type in LIST_TYPES:
            file_path = self._get_file_path(list_type)
            if not file_path.exists():
                file_path.write_text("[]", encoding="utf-8")

    def _get_file_path(self, list_type: str) -> Path:
        return self.base_dir / f"{list_type}.json"

    def _load(self, list_type: str) -> List[str]:
        file_path = self._get_file_path(list_type)
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"{file_path.name} is not valid JSON: {exc}",
            ) from exc

        if not isinstance(data, list):
            raise HTTPException(
                status_code=500,
                detail=f"{file_path.name} must contain a JSON list",
            )

        return sorted({str(item).strip().lower() for item in data if str(item).strip()})

    def _save(self, list_type: str, domains: List[str]) -> None:
        file_path = self._get_file_path(list_type)
        file_path.write_text(
            json.dumps(sorted(set(domains)), indent=2),
            encoding="utf-8",
        )

    def get_all(self) -> Dict[str, List[str]]:
        return {list_type: self._load(list_type) for list_type in LIST_TYPES}

    def get_list(self, list_type: str) -> List[str]:
        return self._load(list_type)

    def add_many(self, list_type: str, domains: List[str]) -> Dict[str, int]:
        existing = set(self._load(list_type))
        incoming = {domain for domain in domains if domain}
        added = incoming - existing
        merged = sorted(existing | incoming)
        self._save(list_type, merged)
        return {
            "added": len(added),
            "skipped": len(incoming) - len(added),
            "total": len(merged),
        }

    def exists(self, list_type: str, domain: str) -> bool:
        return domain in set(self._load(list_type))

    def delete(self, list_type: str, domain: str) -> bool:
        domains = self._load(list_type)
        updated = [item for item in domains if item != domain]
        if len(updated) == len(domains):
            return False

        self._save(list_type, updated)
        return True


def normalize_domain(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        raise HTTPException(status_code=400, detail="Domain value cannot be empty")

    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or parsed.path or raw).strip().lower()
    hostname = hostname.split("/")[0].split("@")[-1].split(":")[0]

    if not hostname:
        raise HTTPException(status_code=400, detail="Could not extract a domain")

    extracted = TLD_EXTRACTOR(hostname)
    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}"

    fallback = hostname.removeprefix("www.")
    if "." not in fallback:
        raise HTTPException(status_code=400, detail=f"Invalid domain: {value}")

    return fallback


async def parse_csv_domain_columns(file: UploadFile) -> Dict[str, List[str]]:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV uploads are supported")

    content = await file.read()
    decoded = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if decoded is None:
        raise HTTPException(status_code=400, detail="Could not decode uploaded CSV file")

    reader = csv.DictReader(io.StringIO(decoded))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV file must include headers")

    header_map = {header.strip().lower(): header for header in reader.fieldnames if header}
    selected_columns = {
        list_type: header_map[list_type]
        for list_type in LIST_TYPES
        if list_type in header_map
    }

    if not selected_columns:
        raise HTTPException(
            status_code=400,
            detail="CSV must contain at least one of these columns: ats, non_ats",
        )

    result: Dict[str, List[str]] = {list_type: [] for list_type in LIST_TYPES}

    for row in reader:
        for list_type, source_column in selected_columns.items():
            raw_value = (row.get(source_column) or "").strip()
            if not raw_value:
                continue
            result[list_type].append(normalize_domain(raw_value))

    if not any(result.values()):
        raise HTTPException(status_code=400, detail="No valid domains found in CSV")

    return result
