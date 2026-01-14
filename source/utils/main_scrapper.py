
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
from service.mongdb_service import MongoDBService
from utils.file_storage import JobFileManager
from typing import List, Dict, Any
from utils.logging import setup_logger
import time

# Configure logging
logger = setup_logger(__name__)




async def main_scrapper(domain: str, llm_model: str = "gpt-5-nano", agent_id: int = 0) -> Dict[str, Any]:
    logger.info(
        "Starting main scraper",
        extra={"domain": domain},
    )

    config = JobScraperConfig(
        openai_api_key=settings.OPENAI_API_KEY,
        llm_model=llm_model,
    )
    logger.debug(
        "JobScraperConfig initialized",
        extra={"llm_model": config.llm_model},
    )

    extract_config = ExtractionConfig(
        handle_cookies=True,
        handle_popups=True,
        scroll_to_load=True,  # For infinite scroll pages
        wait_seconds=3.0,
    )
    logger.debug(
        "ExtractionConfig initialized",
        extra={
            "handle_cookies": extract_config.handle_cookies,
            "handle_popups": extract_config.handle_popups,
            "scroll_to_load": extract_config.scroll_to_load,
            "wait_seconds": extract_config.wait_seconds,
        },
    )

    
    logger.debug(
        "MongoDBService initialized",
        extra={
            "database_name": settings.DATABASE_NAME,
            "collection_name": "jobs",
        },
    )

    chrome_config =ChromeConfig(
        port= 9222 + agent_id
    )
    async with ChromeCDPManager(config=chrome_config) as manager:
        start_time = time.time()
        logger.debug("ChromeCDPManager context entered")
        page = manager.page
        
        extractor = DOMContentExtractor(page, extract_config)

        analyzer = JobPageAnalyzer(api_key=config.openai_api_key, model=config.llm_model)
        llm = ChatOpenAI(model=config.llm_model)
        tracker = URLTracker()
        url_extractor_page = UrlExtractor(page, extractor)
        
        browser = BrowserSession(keep_alive=True)
        await browser.connect(manager.cdp_url)
       
        logger.debug(
            "BrowserSession started",
            extra={"cdp_url": manager.cdp_url},
        )
        
        logger.debug("All services initialized")
        
        fallback_urls = await url_extractor_page.discover_job_urls_from_domain(
                domain=domain,
                try_common_paths=False,
                extract_from_homepage=True,
            )
        
        if not fallback_urls.get("success") or fallback_urls.get("redirected"):
            await browser.stop()
            fallback_urls["message"] = "Domain name redirected to a different domain or fail to access the page"
            return fallback_urls
    
        logger.info(
            "Starting search and filter phase",
            extra={"domain": domain},
        )

        search_query = f"{domain} jobs"
        logger.debug(
            "Executing web search",
            extra={"query": search_query, "engine": "DUCKDUCKGO"},
        )
        search_result = await url_extractor_page.search_duckduckgo(search_query, domain)
     
        job_filtered = []
        if not search_result.get("success"):
            # NOTE: add failure to state
            pass
            
        job_filtered = list(set(search_result.get("result", []) + fallback_urls.get("result", [])))
        
        if not job_filtered:
            logger.error(
                "No job URLs found even with fallback",
                extra={"domain": domain},
            )
            await browser.stop()
            return {
                "domain": domain,
                "job_search": search_result,
                "job_urls_from_domain": fallback_urls,
                "success": False,
                "message": "Was not able to find job/career page"
            }
            
        logger.info(
            "Starting job scraping phase",
            extra={"urls_to_process": len(job_filtered)},
        )
        scraper = TrackedJobScraper(
            browser=browser,
            llm=llm,
            extractor=extractor,
            analyzer=analyzer,
            tracker=tracker,
            config=config,
        )

        all_scraped_jobs = []
        error_list = []
        success_false = []
        success_true = []
        
        # job_filtered = ["https://archive.transparency.org.uk/careers", "https://www.lilianfaithfull.co.uk/our-care/care-team-training/"]
        # job_filtered = ["https://www.lilianfaithfull.co.uk/about-us/work-with-us/"]
        
        total_tokens = 0
        for url in job_filtered:
            url = tracker.normalize_full_path(url, domain)

            if tracker.should_skip(url):
                logger.debug(
                    "Skipping already processed URL",
                    extra={"url": url},
                )
                continue

            logger.debug(
                "Scraping jobs from URL",
                extra={"url": url},
            )

            # 1. Time tracking for scrape_jobs
            scrape_start_time = time.time()
            result = await scraper.scrape_jobs(url)
            scrape_duration = time.time() - scrape_start_time
            
            # Add scraping tokens
            total_tokens += result.total_token
            
            remaining = tracker.filter_unvisited(job_filtered)
            logger.debug(
                "Remaining URLs to process",
                extra={"remaining_count": len(remaining)},
            )
            
            if result.skip_url:
                continue
            
            result_dict = result.to_dict()
            result_dict["job_filter_url"] = url
            result_dict["scrape_duration_seconds"] = round(scrape_duration, 2)  # Add scrape time
            del result_dict["jobs"]
            
            if result.job_detail_urls:
                # 2. Time tracking for ats_checks
                ats_start_time = time.time()
                ats_checked = await scraper.ats_checks(domain=domain, jobs=result.job_detail_urls)
                ats_duration = time.time() - ats_start_time
                
                # Add ATS tokens
                total_tokens += ats_checked.get("total_tokens", 0)
                
                # Add ATS check duration to the response
                ats_checked["ats_duration_seconds"] = round(ats_duration, 2)
                
                result_dict["ats_checked"] = ats_checked
                all_scraped_jobs.append(result_dict)
                continue
            
            elif result.error:
                error_list.append(result_dict)
                
            elif not result.success:
                success_false.append(result_dict)
            
            else:
                success_true.append(result_dict)
                
        await browser.stop()

        # 3. Total time taken
        total_duration = time.time() - start_time

        return_dict = {
            "domain": domain,
            "job_urls_checked": job_filtered,
            "job_found": all_scraped_jobs,
            "error": error_list,
            "success_false": success_false,
            "success_true": success_true,
            "success": True,
            "message": "not able to find job" if len(all_scraped_jobs) == 0 else "Job found",
            "total_duration_seconds": round(total_duration, 2),  # Total time
            "total_urls_processed": len(job_filtered),
            "total_token_usage": total_tokens
        }

        return return_dict
