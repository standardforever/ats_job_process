
# =============================================================================
# Main Integration Example
# =============================================================================

from service.url_extractor_engine_service import UrlExtractor
from service.brower_scraper_service import DOMContentExtractor, ExtractionConfig
from service.chromium_service import ChromeCDPManager, ChromeConfig
from service.job_analyzer import JobPageAnalyzer, AnalysisPromptType
from service.agent_service import JobScraperConfig, URLTracker, TrackedJobScraper, JobEntry, ScrapeResult
from utils.domain_name_filters import URLFilter
from utils.ats_detector import  ATSDetector
from browser_use import Agent, BrowserSession, ChatOpenAI
from core.config import settings
import asyncio
from service.mongdb_service import MongoDBService
from utils.file_storage import JobFileManager
from typing import List, Dict, Any
from utils.logging import setup_logger
import time

# Configure logging
logger = setup_logger(__name__)



async def main_scrapper(domain: str, llm_model: str = "gpt-4o-mini", agent_id: int = 0) -> Dict[str, Any]:
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

        chrome_config = ChromeConfig(
            port=9222 + agent_id  # IMPORTANT: Use agent_id!
        )
        
        async with ChromeCDPManager(config=chrome_config) as manager:
            logger.debug("ChromeCDPManager context entered")
            page = manager.page
            
            extractor = DOMContentExtractor(page, extract_config)
            analyzer = JobPageAnalyzer(api_key=config.openai_api_key, model=config.llm_model)
            llm = ChatOpenAI(model=config.llm_model)
            tracker = URLTracker()
            url_extractor_page = UrlExtractor(page, extractor)
            
            browser = BrowserSession(keep_alive=True)
            await browser.connect(manager.cdp_url)
            
            logger.debug("BrowserSession started", extra={"cdp_url": manager.cdp_url})
            logger.debug("All services initialized")
            
            # [... all the URL discovery and validation code ...]
            
            fallback_urls = await url_extractor_page.discover_job_urls_from_domain(
                domain=domain,
                try_common_paths=False,
                extract_from_homepage=True,
            )
            
            meta_data = fallback_urls.get("meta_data", {})
            is_redirected = meta_data.get("redirected", False)

            if not fallback_urls.get("success") or is_redirected:
                if browser:
                    await browser.stop()
                
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
            
            job_filtered = list(set(search_result.get("result", []) + fallback_urls.get("result", [])))
            
            if not job_filtered:
                logger.error("No job URLs found", extra={"domain": domain})
                if browser:
                    await browser.stop()
                
                total_duration = time.time() - start_time
                return {
                    "domain": domain,
                    "success": False,
                    "run_status": "No Job Pages Found",
                    "message": "Was not able to find job/career page",
                    "total_duration_seconds": round(total_duration, 2),
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
                        "error_type": "no_job_urls_found",
                        "search_result": search_result.get("status", ""),
                        "fallback_result": "No URLs found"
                    }
                }
            
            logger.info("Starting job scraping phase", extra={"urls_to_process": len(job_filtered)})
            
            scraper = TrackedJobScraper(
                browser=browser,
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
                "ats_results": {
                    "ats_true": [],      # Jobs confirmed as ATS
                    "ats_false": [],     # Jobs confirmed as NOT ATS
                    "ats_uncertain": []  # Jobs we couldn't determine
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
                
                if result.is_linkd_or_indeed_url:
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
                    
                    # Categorize ATS results with filter URL context
                    ats_results = ats_checked.get("results", [])
                    for ats_result in ats_results:
                        job_info = {
                            "job_url": ats_result.get("job_url"),
                            "filter_url": url,  # Track which filter URL led to this job
                            "ats_provider": ats_result.get("ats_provider"),
                            "confidence": ats_result.get("confidence"),
                            "reasoning": ats_result.get("reasoning"),
                            "detection_method": ats_result.get("detection_method")
                        }
                        
                        if ats_result.get("status") == "success":
                            if ats_result.get("is_ats") == True:
                                stats["ats_results"]["ats_true"].append(job_info)
                                stats["ats_jobs_found"] += 1
                                complete = True
                            elif ats_result.get("is_ats") == False:
                                stats["ats_results"]["ats_false"].append(job_info)
                        else:
                            # Status is "uncertain" or "error"
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
            # Clean up browser BEFORE exiting context
            if browser:
                await browser.stop()

            # STILL INSIDE THE 'async with' BLOCK
            total_duration = time.time() - start_time
            
            # Determine priority ATS result
            # Priority: true > false > uncertain > none
            priority_ats_detection = None
            
            if stats["ats_results"]["ats_true"]:
                # Prioritize TRUE - take the first one
                first_true = stats["ats_results"]["ats_true"][0]
                priority_ats_detection = {
                    "ats_status": "true",
                    "job_url": first_true["job_url"],
                    "filter_url": first_true["filter_url"],
                    "ats_provider": first_true["ats_provider"],
                    "confidence": first_true["confidence"],
                    "reasoning": first_true["reasoning"],
                    "detection_method": first_true["detection_method"]
                }
            elif stats["ats_results"]["ats_false"]:
                # Prioritize FALSE if no TRUE found
                first_false = stats["ats_results"]["ats_false"][0]
                priority_ats_detection = {
                    "ats_status": "false",
                    "job_url": first_false["job_url"],
                    "filter_url": first_false["filter_url"],
                    "ats_provider": first_false.get("ats_provider"),  # Usually null for false
                    "confidence": first_false["confidence"],
                    "reasoning": first_false["reasoning"],
                    "detection_method": first_false["detection_method"]
                }
            elif stats["ats_results"]["ats_uncertain"]:
                # Show UNCERTAIN if only uncertain results
                first_uncertain = stats["ats_results"]["ats_uncertain"][0]
                priority_ats_detection = {
                    "ats_status": "uncertain",
                    "job_url": first_uncertain["job_url"],
                    "filter_url": first_uncertain["filter_url"],
                    "ats_provider": first_uncertain.get("ats_provider"),
                    "confidence": first_uncertain.get("confidence", "uncertain"),
                    "reasoning": first_uncertain["reasoning"],
                    "detection_method": first_uncertain["detection_method"],
                    "status": first_uncertain.get("status"),
                    "error": first_uncertain.get("error")
                }
            # Determine run_status based on results
            if stats["linkedin_indeed_redirects"] > 0:
                run_status = "LinkedIn/Indeed Redirect"
            elif priority_ats_detection is None:
                run_status = "No Jobs Found"
            elif priority_ats_detection["ats_status"] == "true":
                provider = priority_ats_detection.get("ats_provider", "Unknown")
                run_status = f"ATS Detected - {provider}"
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
                        "ats_true_jobs": stats["ats_results"]["ats_true"],
                        "ats_false_jobs": stats["ats_results"]["ats_false"],
                        "ats_uncertain_jobs": stats["ats_results"]["ats_uncertain"]
                    }
                },
                "scrape_results": scrape_results
            }

                
    except asyncio.CancelledError:
        logger.warning(
            "Scraper task was cancelled",
            extra={"domain": domain, "agent_id": agent_id},
        )
        
        # Clean up browser if it exists
        if browser:
            try:
                await browser.stop()
            except Exception as e:
                logger.error(f"Error stopping browser during cancellation: {e}")
        
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
        
        # Clean up browser if it exists
        if browser:
            try:
                await browser.stop()
            except Exception as cleanup_error:
                logger.error(
                    "Error stopping browser during cleanup",
                    extra={"error": str(cleanup_error)},
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