import re
from typing import  Optional
from urllib.parse import urlparse


from utils.logging import setup_logger



# Configure logging
logger = setup_logger(__name__)


# =============================================================================
# URL Filtering Utilities
# =============================================================================


class URLFilter:
    DEFAULT_JOB_KEYWORDS = frozenset({
        "job", "jobs", "career", "careers",
        "vacancy", "vacancies", "opportunity", "opportunities",
        "hiring", "recruit", "recruitment",
        "position", "positions", "opening", "openings",
        "join", "apply", "application", "talent",
        "team", "work", "working", "people", "peoples", "about"
    })

    SKIP_EXTENSIONS = frozenset({
        ".pdf", ".doc", ".docx", ".xls", ".xlsx",
        ".ppt", ".pptx", ".zip", ".rar", ".7z",
        ".png", ".jpg", ".jpeg", ".gif", ".svg",
    })

    COMMON_JOB_PATHS = [
        "/careers",
        "/jobs",
        "/careers/",
        "/jobs/",
        "/work-with-us",
        "/join-us",
        "/join-our-team",
        "/opportunities",
        "/vacancies",
        "/openings",
        "/hiring",
        "/employment",
        "/career",
        "/job",
        "/work",
        "/about/careers",
        "/about/jobs",
        "/company/careers",
        "/en/careers",
        "/en/jobs",
    ]

    @classmethod
    def filter_web_pages_only(cls, urls: list[str]) -> list[str]:
        logger.debug(
            "Filtering web pages only",
            extra={"input_count": len(urls)},
        )
        filtered = []
        skipped_count = 0
        for url in urls:
            url_lower = url.lower().split("?")[0]
            if not any(url_lower.endswith(ext) for ext in cls.SKIP_EXTENSIONS):
                filtered.append(url)
            else:
                skipped_count += 1
                logger.debug(
                    "URL skipped due to extension",
                    extra={"url": url},
                )
        
        logger.debug(
            "Web pages filtering completed",
            extra={
                "input_count": len(urls),
                "output_count": len(filtered),
                "skipped_count": skipped_count,
            },
        )
        return filtered

    @staticmethod
    def filter_by_domain(urls: list[str], domain: str) -> list[str]:
        logger.debug(
            "Filtering URLs by domain",
            extra={"input_count": len(urls), "domain": domain},
        )
        domain = domain.replace("www.", "").lower()
        domain = domain.replace('/', '')
        filtered = []

        for url in urls:
            try:
                parsed = urlparse(url)
                url_domain = parsed.netloc.replace("www.", "").lower()

                if url_domain == domain or url_domain.endswith(f".{domain}"):
                    if url_domain == domain:
                        if (parsed.path and parsed.path != "/") or parsed.query or parsed.fragment:
                            filtered.append(url)
                            logger.debug(
                                "URL matched domain",
                                extra={"url": url, "domain": domain},
                            )
                    else:
                        filtered.append(url)
                        logger.debug(
                            "URL matched subdomain",
                            extra={"url": url, "url_domain": url_domain, "domain": domain},
                        )
            except Exception as e:
                logger.debug(
                    "Failed to parse URL for domain filtering",
                    extra={"url": url, "error": str(e)},
                )
                continue

        logger.debug(
            "Domain filtering completed",
            extra={
                "input_count": len(urls),
                "output_count": len(filtered),
                "domain": domain,
            },
        )
        return filtered

    @classmethod
    def filter_job_urls(
        cls,
        urls: list[str],
        include_keywords: Optional[set[str]] = None,
    ) -> list[str]:
        keywords = include_keywords or cls.DEFAULT_JOB_KEYWORDS
        logger.debug(
            "Filtering job URLs",
            extra={
                "input_count": len(urls),
                "keywords_count": len(keywords),
            },
        )
        scored = []

        for url in urls:
            try:
                url_lower = url.lower()
                score = sum(
                    1 for kw in keywords
                    if re.search(rf"\b{re.escape(kw)}\b", url_lower)
                )
                if score > 0:
                    scored.append((url, score))
                    logger.debug(
                        "URL matched job keywords",
                        extra={"url": url, "score": score},
                    )
            except Exception as e:
                logger.debug(
                    "Failed to score URL",
                    extra={"url": url, "error": str(e)},
                )
                continue

        scored.sort(key=lambda x: x[1], reverse=True)
        result = [url for url, _ in scored]
        
        logger.debug(
            "Job URL filtering completed",
            extra={
                "input_count": len(urls),
                "output_count": len(result),
                "top_score": scored[0][1] if scored else 0,
            },
        )
        return result

