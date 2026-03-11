import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from utils.logging import setup_logger
import tldextract


# Configure logging
logger = setup_logger(__name__)

# =============================================================================
# ATS Detector
# =============================================================================




@dataclass
class ATSDetectionResult:
    is_ats: bool
    is_external_application: bool
    is_known_ats: bool
    ats_provider: Optional[str]
    job_domain: str
    company_domain: str
    detection_reason: str


class ATSDetector:
    ATS_DOMAINS_FILE = Path(__file__).resolve().parents[1] / "domain_lists" / "ats.json"

    @classmethod
    def get_known_ats_domains(cls) -> frozenset[str]:
        """Load ATS domains from the JSON-backed domain list."""
        try:
            if not cls.ATS_DOMAINS_FILE.exists():
                logger.warning(
                    "ATS domain list file not found",
                    extra={"path": str(cls.ATS_DOMAINS_FILE)},
                )
                return frozenset()

            raw_domains = json.loads(cls.ATS_DOMAINS_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw_domains, list):
                logger.warning(
                    "ATS domain list file does not contain a JSON list",
                    extra={"path": str(cls.ATS_DOMAINS_FILE)},
                )
                return frozenset()

            domains = frozenset(
                str(domain).strip().lower()
                for domain in raw_domains
                if str(domain).strip()
            )
            logger.debug(
                "Loaded ATS domains from storage",
                extra={"count": len(domains), "path": str(cls.ATS_DOMAINS_FILE)},
            )
            return domains
        except json.JSONDecodeError as e:
            logger.warning(
                "Failed to parse ATS domain list file",
                extra={"path": str(cls.ATS_DOMAINS_FILE), "error": str(e)},
            )
            return frozenset()

    @classmethod
    def extract_base_domain(cls, url: str) -> str:
        """Extract base domain (e.g., 'example.com' from 'jobs.example.com')."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Use tldextract to properly handle compound TLDs
            extracted = tldextract.extract(domain)
            
            if extracted.domain and extracted.suffix:
                base_domain = f"{extracted.domain}.{extracted.suffix}"
            else:
                # Fallback to simple extraction if tldextract fails
                domain = domain.replace("www.", "")
                parts = domain.split(".")
                base_domain = ".".join(parts[-2:]) if len(parts) >= 2 else domain
            
            logger.debug(
                "Extracted base domain",
                extra={
                    "url": url,
                    "full_domain": domain,
                    "base_domain": base_domain,
                    "subdomain": extracted.subdomain if extracted else None,
                },
            )
            return base_domain
        except Exception as e:
            logger.warning(
                "Failed to extract base domain",
                extra={"url": url, "error": str(e)},
            )
            return ""

    @classmethod
    def extract_full_domain(cls, url: str) -> str:
        """Extract full domain including subdomains."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")
            logger.debug(
                "Extracted full domain",
                extra={"url": url, "domain": domain},
            )
            return domain
        except Exception as e:
            logger.warning(
                "Failed to extract full domain",
                extra={"url": url, "error": str(e)},
            )
            return ""

    @classmethod
    def find_matching_ats(cls, url: str) -> Optional[str]:
        """Find matching ATS provider from known list."""
        logger.debug(
            "Searching for matching ATS provider",
            extra={"url": url},
        )
        base_domain = cls.extract_base_domain(url)
        full_domain = cls.extract_full_domain(url)
        known_ats_domains = cls.get_known_ats_domains()

        for ats_domain in known_ats_domains:
            if base_domain == ats_domain:
                logger.debug(
                    "ATS match found via base domain",
                    extra={"url": url, "ats_domain": ats_domain},
                )
                return ats_domain
            if full_domain.endswith(f".{ats_domain}"):
                logger.debug(
                    "ATS match found via subdomain",
                    extra={"url": url, "ats_domain": ats_domain, "full_domain": full_domain},
                )
                return ats_domain
            if full_domain == ats_domain:
                logger.debug(
                    "ATS match found via full domain",
                    extra={"url": url, "ats_domain": ats_domain},
                )
                return ats_domain

        logger.debug(
            "No matching ATS provider found",
            extra={"url": url, "base_domain": base_domain, "full_domain": full_domain},
        )
        return None


    @classmethod
    def detect_ats(cls, job_url: str, company_domain: str) -> dict[str, Any]:
        """
        Detect if a job URL is using an ATS.
        
        Detection logic:
        1. If job URL domain is in the stored ATS list → Confirmed ATS
        2. If job URL domain differs from company domain → External application
        3. If same domain and not in the stored ATS list → Internal application
        
        Args:
            job_url: The job application/listing URL
            company_domain: The company's main domain (e.g., "openai.com")
            
        Returns:
            Dictionary with ATS detection results
        """
        logger.info(
            "Starting ATS detection",
            extra={"job_url": job_url, "company_domain": company_domain},
        )

        # Normalize company domain (handle both "openai.com" and "https://openai.com")
        if company_domain.startswith("http"):
            company_domain_clean = cls.extract_base_domain(company_domain)
        else:
            extracted = tldextract.extract(company_domain)
            # Extract domain + suffix (handles compound TLDs correctly)
            if extracted.domain and extracted.suffix:
                company_domain_clean = f"{extracted.domain}.{extracted.suffix}"
            else:
                # Fallback to original
                company_domain_clean = company_domain.lower().replace("www.", "")

        logger.debug(
            "Company domain normalized",
            extra={"original": company_domain, "normalized": company_domain_clean},
        )

        job_domain = cls.extract_base_domain(job_url)
        # Check if domains match
        is_external = job_domain != company_domain_clean
        logger.debug(
            "Domain comparison for ATS detection",
            extra={
                "job_domain": job_domain,
                "company_domain_clean": company_domain_clean,
                "is_external": is_external,
            },
        )

        # Check if it's a known ATS
        known_ats_provider = cls.find_matching_ats(job_url)
        is_known_ats = known_ats_provider is not None

        # ATS is determined strictly by the stored ATS domain list.
        is_ats = is_known_ats

        # Determine ATS provider:
        # - Known ATS: use the matched ATS domain
        # - Otherwise: None
        if is_known_ats:
            ats_provider = known_ats_provider
        else:
            ats_provider = None

        # Determine detection reason
        if is_known_ats:
            reason = f"Known ATS provider: {known_ats_provider}"
        elif is_external:
            reason = f"External domain ({job_domain}) differs from company ({company_domain_clean}) but is not in the stored ATS list"
        else:
            reason = "Internal application on company domain and not in the stored ATS list"

        result = {
            "is_ats": is_ats,
            "is_external_application": is_external,
            "is_known_ats": is_known_ats,
            "ats_provider": ats_provider,
            "job_domain": job_domain,
            "company_domain": company_domain_clean,
            "detection_reason": reason,
        }

        logger.info(
            "ATS detection completed",
            extra={
                "job_url": job_url,
                "is_ats": is_ats,
                "is_known_ats": is_known_ats,
                "ats_provider": ats_provider,
                "detection_reason": reason,
            },
        )

        return result
