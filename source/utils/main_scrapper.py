
# =============================================================================
# Main Integration Example
# =============================================================================

from service.url_extractor_engine_service import UrlExtractor
from service.brower_scraper_service import DOMContentExtractor, ExtractionConfig
from service.chromium_service import ChromeCDPManager, CDPConfig
from service.job_analyzer import JobPageAnalyzer, AnalysisPromptType
from service.agent_service import JobScraperConfig, URLTracker, TrackedJobScraper, JobEntry, ScrapeResult
from utils.domain_name_filters import URLFilter
from utils.ats_detector import  ATSDetector
from browser_use import Agent, BrowserSession, ChatOpenAI
from service.create_session import create_session
from core.config import settings
import asyncio
from service.mongdb_service import MongoDBService
from utils.file_storage import JobFileManager
from typing import List, Dict, Any, Optional
from utils.logging import setup_logger
import json
from functools import lru_cache
from pathlib import Path
import tldextract
import time
from urllib.parse import urlparse


# Configure logging
logger = setup_logger(__name__)
TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=None)




@lru_cache(maxsize=1)
def _load_ats_lists():
    """Load and cache ats.json and non_ats.json provider lists."""
    ats_file = Path(__file__).resolve().parents[1] / "domain_lists" / "ats.json"
    non_ats_file = Path(__file__).resolve().parents[1] / "domain_lists" / "non_ats.json"

    def _load(path):
        try:
            with open(path) as f:
                data = json.load(f)
            # Support both list and dict formats
            items = data if isinstance(data, list) else list(data.keys())
            return {str(item).lower() for item in items}
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    return _load(ats_file), _load(non_ats_file)


def _normalize_provider(value: str) -> Optional[str]:
    """
    Safely extract the bare domain from an ATS provider string.
    Returns None if the value can't be parsed — no exceptions raised.
    
    Examples:
      "Greenhouse"              → "greenhouse.io"  (if in list as such)
      "app.greenhouse.io"       → "greenhouse.io"
      "https://lever.co/apply"  → "lever.co"
      "Workday"                 → "workday.com"    (if stored that way)
    """
    if not value:
        return None

    raw = value.strip().lower()
    candidate = raw if "://" in raw else f"https://{raw}"

    try:
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or parsed.path or raw).strip().lower()
        hostname = hostname.split("/")[0].split("@")[-1].split(":")[0]

        if not hostname:
            return raw  # return as-is, best effort

        extracted = TLD_EXTRACTOR(hostname)
        if extracted.domain and extracted.suffix:
            return f"{extracted.domain}.{extracted.suffix}"

        # Fallback: strip www. and return if it looks like a domain
        fallback = hostname.removeprefix("www.")
        if "." in fallback:
            return fallback

        # Plain name like "Greenhouse" with no TLD — return as-is for list matching
        return raw

    except Exception:
        return raw

def reconfirm_ats_result(ats_result: dict) -> dict:
    """
    Reconfirm ATS result against known provider lists.

    Rules:
    - is_ats=True, no provider       → no adjustment
    - provider in non_ats.json       → override to non-ATS (even if LLM said ATS)
    - provider in ats.json           → confirm as ATS
    - provider exists, not in either → unknown_ats (is_ats stays True, flagged unknown)
    - is_ats=False, no provider      → no adjustment
    """

    result = ats_result.copy()
    is_ats = result.get("is_ats")
    provider = result.get("ats_provider")

    # No provider → no adjustment
    if not provider:
        return result

    ats_set, non_ats_set = _load_ats_lists()

    # ── Clean provider before lookup ────────────────────────────────────────
    normalized_provider = _normalize_provider(provider)
    if not normalized_provider:
        return result

    provider_lower = normalized_provider.lower()

    if provider_lower in non_ats_set:
        result["is_ats"] = False
        result["_reconfirmed"] = "overridden_to_non_ats"

    elif provider_lower in ats_set:
        result["is_ats"] = True
        result["_reconfirmed"] = "confirmed_ats"

    else:
        result["_reconfirmed"] = "unknown_ats"
        result["_unknown_ats"] = True

    return result


async def main_scrapper(domain: str, llm_model: str = "gpt-5-nano", agent_id: int = 0) -> Dict[str, Any]:
    browser = None  # Initialize at top
    manager = None
    run_status = None
    start_time = time.time()
    
    try:
        logger.info("Starting main scraper", extra={"domain": domain})

        config = JobScraperConfig(
            openai_api_key=settings.OPENAI_API_KEY,
            llm_model=llm_model,
        )

        extract_config = ExtractionConfig(
            handle_cookies=True,
            handle_popups=True,
            scroll_to_load=True,
            wait_seconds=3.0,
        )
        cdp_url = create_session()
        if not cdp_url:
            message = "No Browser found on the server"
            return {
                    "domain": domain,
                    "success": False,
                    "run_status": run_status,
                    "message": message,
                    "total_duration_seconds": 0,
                    "total_urls_processed": 0,
                    "total_token_usage": 0,
                    "summary": {
                        "urls_checked": 0,
                        "jobs_found": 0,
                        "successful_scrapes": 0,
                        "failed_scrapes": 1,
                        "linkedin_indeed_redirects": 0,
                        "ats_jobs_found": 0
                    },
                    "scrape_results": [],
                    "error_details": {
                        "error_type": message,
                        "error": message,
                        "status": "error",
                        "redirected": None
                    }
                }
            
        chrome_config = CDPConfig(cdp_url=cdp_url)
        
        async with ChromeCDPManager(config=chrome_config) as manager:
            logger.debug("ChromeCDPManager context entered")
            page = manager.page
            
            extractor = DOMContentExtractor(page, extract_config)
            analyzer = JobPageAnalyzer(api_key=config.openai_api_key, model=config.llm_model)
            llm = ChatOpenAI(model=config.llm_model)
            tracker = URLTracker()
            url_extractor_page = UrlExtractor(page, extractor)
            
            # browser = BrowserSession(keep_alive=True)
            # await browser.connect(manager.cdp_url)
            
            logger.debug("BrowserSession started", extra={"cdp_url": manager.cdp_url})
            logger.info("All services initialized")
            
            # [... all the URL discovery and validation code ...]
            
            
            fallback_urls = await url_extractor_page.discover_job_urls_from_domain(
                domain=domain,
                try_common_paths=False,
                extract_from_homepage=True,
            )
            
            non_domain_careers_url= await url_extractor_page._extract_career_urls_from_page(domain)
            
            meta_data = fallback_urls.get("meta_data", {})
            is_redirected = meta_data.get("redirected", False)
            all_urls = meta_data.get("all_urls", [])

            if not fallback_urls.get("success") or is_redirected:
           
                total_duration = time.time() - start_time
                
                if not fallback_urls.get("success"):
                    error_type = "domain_access_failed"
                    message = "Failed to access domain or load homepage"
                    run_status = "Domain Failed"
                    error = fallback_urls.get("error", "Unknown error")
                    status = fallback_urls.get("status", "")
                    
                else:
                    error_type = "domain_redirected"
                    final_domain = meta_data.get('final_domain', 'unknown')
                    message = f"Domain redirected from {meta_data.get('original_domain')} to {final_domain}"
                    error = f"Redirect detected: {meta_data.get('original_url')} → {meta_data.get('final_url')}"
                    status = "redirected"
                    run_status = f"Domain Redirected to {final_domain}"
                
                return {
                    "domain": domain,
                    "success": False,
                    "run_status": run_status,
                    "message": message,
                    "total_duration_seconds": round(total_duration, 2),
                    "total_urls_processed": 0,
                    "total_token_usage": 0,
                    "summary": {
                        "non_domain_careers_url": non_domain_careers_url,
                        "urls_checked": 0,
                        "jobs_found": 0,
                        "successful_scrapes": 0,
                        "failed_scrapes": 1,
                        "linkedin_indeed_redirects": 0,
                        "ats_jobs_found": 0
                    },
                    "scrape_results": [],
                    "error_details": {
                        "error_type": error_type,
                        "error": error,
                        "status": status,
                        "redirected": is_redirected,
                        "original_url": meta_data.get("original_url"),
                        "final_url": meta_data.get("final_url"),
                        "original_domain": meta_data.get("original_domain"),
                        "final_domain": meta_data.get("final_domain")
                    }
                }
            
            logger.info("Starting search and filter phase", extra={"domain": domain})

            search_query = f"{domain} jobs"
            search_result = await url_extractor_page.search_duckduckgo(search_query, domain)
            all_urls = all_urls + search_result.get("meta_data", {}).get("all_urls", [])

            
            job_filtered = list(set(search_result.get("result", []) + fallback_urls.get("result", [])))
        
            if not job_filtered:
                logger.error("No job URLs found", extra={"domain": domain})
               
                total_duration = time.time() - start_time
                return {
                    "domain": domain,
                    "success": False,
                    "run_status": "No career/job page found",
                    "message": "Was not able to find job/career page",
                    "total_duration_seconds": round(total_duration, 2),
                    "total_urls_processed": 0,
                    "total_token_usage": 0,
                    "summary": {
                        "non_domain_careers_url": non_domain_careers_url,
                        "urls_checked": 0,
                        "jobs_found": 0,
                        "successful_scrapes": 0,
                        "failed_scrapes": 1,
                        "linkedin_indeed_redirects": 0,
                        "ats_jobs_found": 0
                    },
                    "scrape_results": [],
                    "error_details": {
                        "error_type": "no_job_urls_found",
                        "search_result": search_result.get("status", ""),
                        "fallback_result": "No URLs found"
                    }
                }
            
            logger.info("Starting job scraping phase", extra={"urls_to_process": len(job_filtered)})
            
            scraper = TrackedJobScraper(
                page=page,
                llm=llm,
                extractor=extractor,
                analyzer=analyzer,
                tracker=tracker,
                config=config,
            )

            # CRITICAL: These must be INSIDE the 'async with' block!
            scrape_results = []
            total_tokens = 0
            
            stats = {
                "jobs_found": 0,
                "successful_scrapes": 0,
                "failed_scrapes": 0,
                "linkedin_indeed_redirects": 0,
                "ats_jobs_found": 0,
                "access_blocked_scrapes": 0,
                "ats_results": {
                    "ats_true": [],      # Jobs confirmed as ATS
                    "ats_false": [],     # Jobs confirmed as NOT ATS
                    "ats_uncertain": [],  # Jobs we couldn't determine
                    "ats_unknown": []
                }
            }
            
            scrape_results = []
            total_tokens = 0
            start_time = time.time()
            complete = False
            # SCRAPING LOOP MUST BE INSIDE THE 'async with' BLOCK

            for url in job_filtered:
                url = tracker.normalize_full_path(url, domain)

                if tracker.should_skip(url):
                    logger.debug("Skipping already processed URL", extra={"url": url})
                    continue

                logger.debug("Scraping jobs from URL", extra={"url": url})
                
                scrape_start_time = time.time()
                result = await scraper.scrape_jobs(url)
                scrape_duration = time.time() - scrape_start_time
                
                total_tokens += result.total_token
                
                if result.skip_url:
                    continue
                
                scrape_result = {
                    "url": url,
                    "status": "success" if result.success else ("error" if result.error else "failed"),
                    "scrape_duration_seconds": round(scrape_duration, 2),
                    "result_type": None,
                    "page_access_status": result.page_access_status,
                    "page_access_issue_detail": result.page_access_issue_detail,
                    "jobs": {
                        "count": len(result.job_detail_urls),
                        "job_urls": result.job_detail_urls
                    },
                    "ats_check": None,
                    "scraping_details": {
                        "visited_urls": result.visited_urls,
                        "total_tokens": result.total_token,
                        "llm_iterations": len(result.llm_reasoning),
                        "llm_reasoning": result.llm_reasoning,
                        "message": result.message
                    },
                    "error": result.error
                }
                if result.page_access_status in ("bot_detected", "login_required"):
                    scrape_result["result_type"] = "access_blocked"
                    stats["access_blocked_scrapes"] += 1
                    
                elif result.is_linkd_or_indeed_url:
                    scrape_result["result_type"] = "linkedin_indeed_redirect"
                    stats["linkedin_indeed_redirects"] += 1
                    
                elif result.error:
                    scrape_result["result_type"] = "error"
                    stats["failed_scrapes"] += 1
                    
                elif result.job_detail_urls:
                    scrape_result["result_type"] = "jobs_found"
                    stats["successful_scrapes"] += 1
                    stats["jobs_found"] += len(result.job_detail_urls)

                    ats_start_time = time.time()
                    ats_checked = await scraper.ats_checks(domain=domain, jobs=result.job_detail_urls)
                    ats_duration = time.time() - ats_start_time

                    total_tokens += ats_checked.get("total_tokens", 0)

                    scrape_result["ats_check"] = {
                        "duration_seconds": round(ats_duration, 2),
                        "total_tokens": ats_checked.get("total_tokens", 0),
                        "jobs_processed": ats_checked.get("jobs_processed", 0),
                        "results": ats_checked.get("results", [])
                    }

                    ats_results = ats_checked.get("results", [])
                    for ats_result in ats_results:

                        # ── Reconfirm against known provider lists ──────────────────
                        ats_result = reconfirm_ats_result(ats_result)

                        job_info = {
                            "job_url": ats_result.get("job_url"),
                            "filter_url": url,
                            "ats_provider": ats_result.get("ats_provider"),
                            "confidence": ats_result.get("confidence"),
                            "reasoning": ats_result.get("reasoning"),
                            "detection_method": ats_result.get("detection_method"),
                            "reconfirmed": ats_result.get("_reconfirmed"),  # audit trail
                        }

                        if ats_result.get("status") == "success":
                            if ats_result.get("is_ats") == True:
                                if ats_result.get("_unknown_ats"):
                                    # ATS detected but provider not in any known list
                                    stats["ats_results"]["ats_unknown"].append(job_info)
                                else:
                                    stats["ats_results"]["ats_true"].append(job_info)
                                stats["ats_jobs_found"] += 1
                                complete = True
                            elif ats_result.get("is_ats") == False:
                                stats["ats_results"]["ats_false"].append(job_info)
                        else:
                            job_info["status"] = ats_result.get("status")
                            job_info["error"] = ats_result.get("error")
                            stats["ats_results"]["ats_uncertain"].append(job_info)
                            
                elif not result.success:
                    scrape_result["result_type"] = "no_jobs_found"
                    stats["failed_scrapes"] += 1
                    
                else:
                    scrape_result["result_type"] = "success_no_jobs"
                    stats["successful_scrapes"] += 1
                
                scrape_results.append(scrape_result)
                
                if complete == True:
                    break
        

            # STILL INSIDE THE 'async with' BLOCK
            total_duration = time.time() - start_time
            
            # Determine priority ATS result
            # Priority: true > false > uncertain > none
            priority_ats_detection = None
            
            if stats["ats_results"]["ats_true"]:
                first_true = stats["ats_results"]["ats_true"][0]
                priority_ats_detection = {
                    "ats_status": "true",
                    **{k: first_true[k] for k in ("job_url", "filter_url", "ats_provider", "confidence", "reasoning", "detection_method")}
                }

            elif stats["ats_results"]["ats_unknown"]:
                # ATS confirmed but provider not in any known list
                first_unknown = stats["ats_results"]["ats_unknown"][0]
                priority_ats_detection = {
                    "ats_status": "unknown_ats",
                    **{k: first_unknown[k] for k in ("job_url", "filter_url", "ats_provider", "confidence", "reasoning", "detection_method")}
                }

            elif stats["ats_results"]["ats_false"]:
                first_false = stats["ats_results"]["ats_false"][0]
                priority_ats_detection = {
                    "ats_status": "false",
                    **{k: first_false.get(k) for k in ("job_url", "filter_url", "ats_provider", "confidence", "reasoning", "detection_method")}
                }

            elif stats["ats_results"]["ats_uncertain"]:
                first_uncertain = stats["ats_results"]["ats_uncertain"][0]
                priority_ats_detection = {
                    "ats_status": "uncertain",
                    **{k: first_uncertain.get(k) for k in ("job_url", "filter_url", "ats_provider", "confidence", "reasoning", "detection_method")},
                    "status": first_uncertain.get("status"),
                    "error": first_uncertain.get("error")
                }
                
            # Determine run_status based on results
            if stats["linkedin_indeed_redirects"] > 0:
                run_status = "LinkedIn/Indeed Redirect"

            elif (
                stats["access_blocked_scrapes"] > 0
                and stats["access_blocked_scrapes"] == len(job_filtered)
            ):
                blocked_statuses = [
                    r["page_access_status"] for r in scrape_results
                    if r["page_access_status"] in ("bot_detected", "login_required")
                ]
                if "bot_detected" in blocked_statuses and "login_required" in blocked_statuses:
                    run_status = "Access Blocked - Bot Detected / Login Required"
                elif "bot_detected" in blocked_statuses:
                    run_status = "Access Blocked - Bot Detected"
                else:
                    run_status = "Access Blocked - Login Required"

            elif priority_ats_detection is None:
                run_status = "No Jobs Found"

            elif priority_ats_detection["ats_status"] == "true":
                provider = priority_ats_detection.get("ats_provider", "Unknown")
                run_status = f"ATS Detected - {provider}"

            elif priority_ats_detection["ats_status"] == "unknown_ats":
                provider = priority_ats_detection.get("ats_provider", "Unknown")
                run_status = f"ATS Detected - Unknown ({provider})"

            elif priority_ats_detection["ats_status"] == "false":
                run_status = "No ATS - Direct Application"

            elif priority_ats_detection["ats_status"] == "uncertain":
                run_status = "Uncertain - Manual Review Needed"

            else:
                run_status = "Completed"
            
            # Build comprehensive message
            message_parts = []
            message_parts.append(f"Completed scraping {len(job_filtered)} URL(s).")
            message_parts.append(f"Found {stats['jobs_found']} jobs total.")
            
            # ATS True summary
            if stats["ats_results"]["ats_true"]:
                ats_true_count = len(stats["ats_results"]["ats_true"])
                message_parts.append(f"\n✓ ATS Detected ({ats_true_count} jobs):")
                for job in stats["ats_results"]["ats_true"]:
                    provider = job.get('ats_provider', 'Unknown')
                    message_parts.append(f"  • {job['job_url']}")
                    message_parts.append(f"    Provider: {provider} | Filter: {job['filter_url']}")
            
            # ATS False summary
            if stats["ats_results"]["ats_false"]:
                ats_false_count = len(stats["ats_results"]["ats_false"])
                message_parts.append(f"\n✗ No ATS Detected ({ats_false_count} jobs):")
                for job in stats["ats_results"]["ats_false"]:
                    message_parts.append(f"  • {job['job_url']}")
                    message_parts.append(f"    Filter: {job['filter_url']}")
            
            # Uncertain summary
            if stats["ats_results"]["ats_uncertain"]:
                uncertain_count = len(stats["ats_results"]["ats_uncertain"])
                message_parts.append(f"\n? Uncertain/Needs Review ({uncertain_count} jobs):")
                for job in stats["ats_results"]["ats_uncertain"]:
                    status_info = f"Status: {job.get('status', 'unknown')}"
                    message_parts.append(f"  • {job['job_url']}")
                    message_parts.append(f"    {status_info} | Filter: {job['filter_url']}")
                    if job.get('reasoning'):
                        message_parts.append(f"    Reason: {job['reasoning'][:100]}")
                        
            # Unknown ATS summary
            if stats["ats_results"]["ats_unknown"]:
                ats_unknown_count = len(stats["ats_results"]["ats_unknown"])
                message_parts.append(f"\n~ Unknown ATS Detected ({ats_unknown_count} jobs):")
                for job in stats["ats_results"]["ats_unknown"]:
                    provider = job.get('ats_provider', 'Unknown')
                    message_parts.append(f"  • {job['job_url']}")
                    message_parts.append(f"    Provider: {provider} (unrecognised) | Filter: {job['filter_url']}")
                    
            # Additional stats
            if stats["linkedin_indeed_redirects"] > 0:
                message_parts.append(f"\n{stats['linkedin_indeed_redirects']} URL(s) redirected to LinkedIn/Indeed.")
            
            if stats["failed_scrapes"] > 0:
                message_parts.append(f"\n{stats['failed_scrapes']} scrapes failed.")
            
            summary_message = " ".join(message_parts)

            return {
                "domain": domain,
                "success": stats["successful_scrapes"] > 0 or stats["linkedin_indeed_redirects"] > 0,
                "message": summary_message,
                "run_status": run_status,
                
                # TOP-LEVEL PRIORITY ATS DETECTION
                "ats_detection": priority_ats_detection,
                
                "total_duration_seconds": round(total_duration, 2),
                "total_urls_processed": len(job_filtered),
                "total_token_usage": total_tokens,
                "summary": {
                    "non_domain_careers_url": non_domain_careers_url,
                    "job_filtered": job_filtered,
                    "urls_checked": len(job_filtered),
                    "jobs_found": stats["jobs_found"],
                    "successful_scrapes": stats["successful_scrapes"],
                    "failed_scrapes": stats["failed_scrapes"],
                    "linkedin_indeed_redirects": stats["linkedin_indeed_redirects"],
                    "ats_jobs_found": stats["ats_jobs_found"],
                    # Enhanced ATS breakdown
                    "ats_breakdown": {
                        "ats_true_count": len(stats["ats_results"]["ats_true"]),
                        "ats_false_count": len(stats["ats_results"]["ats_false"]),
                        "ats_uncertain_count": len(stats["ats_results"]["ats_uncertain"]),
                        "ats_unknown_count": len(stats["ats_results"]["ats_unknown"]),   # ← MISSING
                        "ats_true_jobs": stats["ats_results"]["ats_true"],
                        "ats_false_jobs": stats["ats_results"]["ats_false"],
                        "ats_uncertain_jobs": stats["ats_results"]["ats_uncertain"],
                        "ats_unknown_jobs": stats["ats_results"]["ats_unknown"],          # ← MISSING
                    }
                },
                "scrape_results": scrape_results
            }

                
    except asyncio.CancelledError:
        logger.warning(
            "Scraper task was cancelled",
            extra={"domain": domain, "agent_id": agent_id},
        )
        
        total_duration = time.time() - start_time
        
        return {
            "domain": domain,
            "success": False,
            "run_status": run_status,
            "error": "Task was cancelled",
            "message": "Scraping task was cancelled by user",
            "total_duration_seconds": round(total_duration, 2),
            "total_urls_processed": 0,
            "total_token_usage": 0,
            "job_found": [],
            "job_urls_checked": [],
            "cancelled": True
        }
        
    except Exception as e:
        logger.error(
            "Critical error in main scraper",
            extra={
                "domain": domain,
                "agent_id": agent_id,
                "error": str(e),
                "error_type": type(e).__name__
            },
            exc_info=True,
        )
        
        
        total_duration = time.time() - start_time
    
        return {
            "domain": domain,
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "run_status": f"Error - {type(e).__name__}",
            "message": f"Critical error during scraping: {str(e)}",
            "total_duration_seconds": round(total_duration, 2),
            "total_urls_processed": 0,
            "total_token_usage": 0,
            "job_found": [],
            "job_urls_checked": []
        }
