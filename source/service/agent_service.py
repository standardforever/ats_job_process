import asyncio
from dataclasses import dataclass, asdict, field
from typing import Any, Optional
from service.brower_scraper_service import DOMContentExtractor
from models.agent_output_models import PaginationCheck
from browser_use import Agent, BrowserSession, ChatOpenAI
from service.job_analyzer import JobPageAnalyzer, AnalysisPromptType
from utils.logging import setup_logger
from utils.text_processor import TextProcessor
from urllib.parse import urlparse, urlunparse
from utils.ats_detector import  ATSDetector

# Configure logging
logger = setup_logger(__name__)
@dataclass
class JobEntry:
    title: str
    url: str
    details: Optional[dict[str, Any]] = None

@dataclass
class JobScraperConfig:
    max_navigation: int = 2
    page_load_wait: float = 5.0
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

@dataclass
class ScrapeResult:
    jobs: list["JobEntry"]
    visited_urls: list[str]
    job_detail_urls: list[str]
    error: Optional[str] = None
    message: Optional[str] = None
    success: Optional[bool] = True
    skip_url: bool = False
    is_linkd_or_indeed_url: bool = False
    ats_checked: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return asdict(self)



class URLTracker:
    def __init__(self):
        self._visited: set[str] = set()
        self._scraped_jobs: set[str] = set()
        logger.debug("URLTracker initialized")

    @staticmethod
    def extract_domain(url: str) -> str:
        """
        Extract domain/host from URL.
        
        Examples:
            https://www.example.com/Jobs/  →  www.example.com
            example.com/careers            →  example.com
            careers.example.com            →  careers.example.com
            https://jobs.google.com/page   →  jobs.google.com
        """
        if not url:
            logger.warning("Empty URL provided")
            return ""
        
        url = url.strip()
        
        # Add scheme if missing (required for urlparse to work correctly)
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            logger.debug(
                "Domain extracted",
                extra={"original_url": url, "domain": domain},
            )
            return domain
            
        except Exception as e:
            logger.error(
                "Failed to extract domain",
                extra={"url": url, "error": str(e)},
            )
            return ""

    @staticmethod
    def normalize_full_path(url, domain):
        if url.startswith("/") and domain:
            return domain.rstrip("/") + url
        return url

    @staticmethod
    def normalize_url(url: str) -> str:
        parsed = urlparse(url.lower().rstrip("/"))
        normalized = urlunparse((
            parsed.scheme,
            parsed.netloc.replace("www.", ""),
            parsed.path.rstrip("/"),
            "",
            "",
            "",
        ))
        return normalized

    def mark_visited(self, url: str) -> None:
        normalized = self.normalize_url(url)
        self._visited.add(normalized)
        logger.debug(
            "URL marked as visited",
            extra={"url": url, "normalized_url": normalized},
        )

    def mark_job_scraped(self, url: str) -> None:
        normalized = self.normalize_url(url)
        self._scraped_jobs.add(normalized)
        logger.debug(
            "Job URL marked as scraped",
            extra={"url": url, "normalized_url": normalized},
        )

    def is_visited(self, url: str) -> bool:
        result = self.normalize_url(url) in self._visited
        logger.debug(
            "Checking if URL is visited",
            extra={"url": url, "is_visited": result},
        )
        return result

    def is_job_scraped(self, url: str) -> bool:
        result = self.normalize_url(url) in self._scraped_jobs
        logger.debug(
            "Checking if job URL is scraped",
            extra={"url": url, "is_scraped": result},
        )
        return result

    def should_skip(self, url: str) -> bool:
        normalized = self.normalize_url(url)
        result = normalized in self._visited or normalized in self._scraped_jobs
        if result:
            logger.debug(
                "URL should be skipped",
                extra={
                    "url": url,
                    "in_visited": normalized in self._visited,
                    "in_scraped_jobs": normalized in self._scraped_jobs,
                },
            )
        return result

    def filter_unvisited(self, urls: list[str]) -> list[str]:
        filtered = [url for url in urls if not self.should_skip(url)]
        logger.debug(
            "Filtered unvisited URLs",
            extra={"input_count": len(urls), "output_count": len(filtered)},
        )
        return filtered

    def get_stats(self) -> dict:
        stats = {
            "visited_pages": len(self._visited),
            "scraped_jobs": len(self._scraped_jobs),
        }
        logger.debug(
            "URLTracker stats",
            extra=stats,
        )
        return stats


class TrackedJobScraper:
    def __init__(
        self,
        browser: BrowserSession,
        llm: ChatOpenAI,
        extractor: "DOMContentExtractor",
        analyzer: "JobPageAnalyzer",
        tracker: URLTracker,
        config: Optional["JobScraperConfig"] = None,
    ):
        self._browser = browser
        self._llm = llm
        self._extractor = extractor
        self._analyzer = analyzer
        self._tracker = tracker
        self._config = config or JobScraperConfig()
        self._current_visited: list[str] = []
        logger.debug(
            "TrackedJobScraper initialized",
            extra={
                "max_navigation": self._config.max_navigation,
                "page_load_wait": self._config.page_load_wait,
                "llm_model": self._config.llm_model,
            },
        )

    async def _get_page(self):
        return await self._browser.get_current_page()

    async def _navigate(self, url: str) -> None:
        logger.debug(
            "Navigating to URL",
            extra={"url": url},
        )
        page = await self._get_page()
        await page.goto(url)
        await asyncio.sleep(self._config.page_load_wait)
        self._tracker.mark_visited(url)
        self._current_visited.append(url)
        logger.debug(
            "Navigation completed and URL marked as visited",
            extra={"url": url, "wait_time": self._config.page_load_wait},
        )

    async def scrape_jobs(self, url: str) -> ScrapeResult:
        self._current_visited = []
        logger.info(
            "Starting tracked job scrape",
            extra={"url": url},
        )

        if self._tracker.should_skip(url):
            logger.info(
                "Skipping already visited URL",
                extra={"url": url},
            )
            return ScrapeResult(jobs=[], visited_urls=self._current_visited, job_detail_urls=[], skip_url=True, message="Skipping already visited URL")

        await self._navigate(url)

        nav_count = 0
        all_jobs: list[JobEntry] = []

        while True:
            content = await self._extractor.extract()
            if not content.structured_text:
                return ScrapeResult(jobs=all_jobs, visited_urls=self._current_visited, job_detail_urls=[j.url for j in all_jobs if j.url], success=False, error=content.raw_structure.get("error"))
            
            logger.debug(
                "Content extracted",
                extra={"url": url, "content_length": len(content.structured_text)},
            )
        
            analysis = await self._analyzer.analyze(url, content.structured_text)
    
            logger.debug(
                "Analysis completed",
                extra={"url": url, "success": analysis.success},
            )
          
            if not analysis.success:
                return ScrapeResult(jobs=all_jobs, visited_urls=self._current_visited, job_detail_urls=[j.url for j in all_jobs if j.url], error=str(analysis.error), message="Ai analysis failed", success=False)
           
            result = analysis.response
            page_category = result.get("page_category", "not_job_related")
            logger.debug(
                "Analysis result",
                extra={
                    "url": url,
                    "page_category": page_category,
                    "next_action": result.get("next_action"),
                },
            )

            if page_category == "not_job_related":
                logger.info(
                    "Page not job related",
                    extra={"url": url},
                )
                return ScrapeResult(jobs=all_jobs, visited_urls=self._current_visited, job_detail_urls=[j.url for j in all_jobs if j.url], message="Page not job related", success=False)

            if page_category == "single_job_posting":
                logger.info(
                    "Working on single job posting",
                    extra={"url": url},
                )
                jobs_on_page = result.get("jobs_listed_on_page", [])
                job_detail_urls = []

                for job in jobs_on_page:
                    job_url = job.get("job_url") or url
                    all_jobs.append(JobEntry(
                        title=job.get("title", ""),
                        url=job_url,
                    ))
                    if job_url:
                        job_detail_urls.append(job_url)
                        self._tracker.mark_job_scraped(job_url)

                logger.info(
                    "Single job posting scraped",
                    extra={"job_count": len(all_jobs)},
                )
                return ScrapeResult(
                    jobs=all_jobs,
                    visited_urls=self._current_visited,
                    job_detail_urls=[j.url for j in all_jobs if j.url],
                    success=True
                )
            
            if page_category == "jobs_listed":
                jobs_on_page = result.get("jobs_listed_on_page", [])
                job_detail_urls = []

                for job in jobs_on_page:
                    job_url = job.get("job_url", "")
                    all_jobs.append(JobEntry(
                        title=job.get("title", ""),
                        url=job_url,
                    ))
                    if job_url:
                        job_detail_urls.append(job_url)
                        self._tracker.mark_job_scraped(job_url)

                logger.info(
                    "Found jobs on page",
                    extra={"job_count": len(jobs_on_page), "url": url},
                )

                
                return ScrapeResult(
                    jobs=all_jobs,
                    visited_urls=self._current_visited,
                    job_detail_urls=[j.url for j in all_jobs if j.url],
                    success=True
                )

            if page_category == "navigation_required" or page_category == "job_listings_preview_page":
                jobs_on_page = result.get("jobs_listed_on_page", [])
                job_detail_urls = []

                for job in jobs_on_page:
                    job_url = job.get("job_url", "")
                    all_jobs.append(JobEntry(
                        title=job.get("title", ""),
                        url=job_url,
                    ))
                    if job_url:
                        job_detail_urls.append(job_url)
                        self._tracker.mark_job_scraped(job_url)

                logger.info(
                    "Found jobs on page",
                    extra={"job_count": len(jobs_on_page), "url": url},
                )

                if nav_count >= self._config.max_navigation:
                    logger.warning(
                        "Max navigation reached",
                        extra={
                            "nav_count": nav_count,
                            "max_navigation": self._config.max_navigation,
                        },
                    )
                    return ScrapeResult(
                        jobs=all_jobs,
                        visited_urls=self._current_visited,
                        job_detail_urls=[j.url for j in all_jobs if j.url],
                        message="Reached max number of page navigation and job page not found.",
                        success=False
                    )

                nav_target = result.get("next_action_target", {})
                nav_url = nav_target.get("url", "")
                current_page = await self._get_page()
                page_url = await current_page.get_url()
                page_url = urlparse(page_url).netloc
                
                nav_domain = urlparse(nav_url).netloc.lower()

                if "linkedin" in nav_domain or "indeed" in nav_domain:
                    return ScrapeResult(
                        jobs=all_jobs,
                        visited_urls=self._current_visited,
                        job_detail_urls=[j.url for j in all_jobs if j.url],
                        message="The job page is pointing to indeed/linkedin site",
                        success=True,
                        is_linkd_or_indeed_url=True
                    )                    
    
                nav_url = TextProcessor.normalize_url(nav_url, page_url)

                if nav_url and nav_url != url:
                    if self._tracker.should_skip(nav_url):
                        logger.warning(
                            "Navigation target already visited",
                            extra={"nav_url": nav_url},
                        )
                        return ScrapeResult(
                            jobs=all_jobs,
                            visited_urls=self._current_visited,
                            job_detail_urls=[j.url for j in all_jobs if j.url],
                            message="Navigation target already visited.",
                            success=False,
                        )

                    nav_count += 1
                    url = nav_url
                    await self._navigate(url)
                    logger.info(
                        "Navigated to new URL",
                        extra={"url": url, "nav_count": nav_count},
                    )
                    continue
                
                link_text = nav_target.get("link_text", "")
                if link_text:
                    nav_count += 1
                    page = await self._get_page()
                    prompt = (
                        f"Find the clickable element whose visible text most closely matches "
                        f"'{link_text}' and is used to navigate to the job listings page."
                    )
                    logger.debug(
                        "Searching for navigation element by text",
                        extra={"link_text": link_text},
                    )
                    button = await page.get_element_by_prompt(prompt, llm=self._llm)
                    if button:
                        await button.click("left")
                        await asyncio.sleep(self._config.page_load_wait)

                        current_url = await page.get_url()
                        self._tracker.mark_visited(current_url)
                        self._current_visited.append(current_url)
                        logger.info(
                            "Clicked and navigated to new page",
                            extra={"current_url": current_url, "link_text": link_text},
                        )
                        continue

                logger.debug(
                    "No valid navigation target found",
                    extra={"nav_target": nav_target},
                )
                
                return ScrapeResult(
                    jobs=all_jobs,
                    visited_urls=self._current_visited,
                    job_detail_urls=[j.url for j in all_jobs if j.url],
                    message="No valid navigation target found.",
                    success=False
                )

            logger.debug(
                "Breaking main loop - unhandled page category",
                extra={"page_category": page_category},
            )
            break

        logger.info(
            "Tracked job scrape completed",
            extra={
                "total_jobs": len(all_jobs),
                "visited_urls_count": len(self._current_visited),
            },
        )
        return ScrapeResult(
            jobs=all_jobs,
            visited_urls=self._current_visited,
            job_detail_urls=[j.url for j in all_jobs if j.url],
            message="No valid contins meets",
            success=False
        )


    async def ats_checks(self, jobs: list[str], domain: str):
        response = {}
        for i, job_url in enumerate(jobs):

            # Detect ATS and create document
            ats_info = ATSDetector.detect_ats(job_url, domain)
            logger.debug(
                "ATS detection completed",
                extra={
                    "job_url": job_url,
                    "is_ats": ats_info["is_ats"],
                    "ats_provider": ats_info["ats_provider"],
                },
            )
            
            if ats_info["is_ats"]:
                self._tracker.mark_visited(job_url)
                response["m_is_ats"] = ats_info["is_ats"]
                response["m_is_known_ats"] = ats_info["is_known_ats"]
                response['m_is_external_application'] = ats_info["is_external_application"]
                response['m_ats_provider'] = ats_info["ats_provider"]
                response['m_reasoning'] = ats_info["detection_reason"]
                response["job_url"] = job_url
                
            else:
                try:
                    # First scrape attempt
                    await self._navigate(job_url)
                    text_extracted = await self._extractor.extract()
                    analysis = await self._analyzer.analyze(
                        job_url,
                        text_extracted.structured_text,
                        prompt_type=AnalysisPromptType.STRUCTURED,
                        main_domain=domain
                    )

                    if analysis.success:
                        response = analysis.response
                        response["job_url"] = job_url
                        
                        # Case 1: AI detected ATS - return immediately
                        if response.get("is_ats") == True:
                            logger.debug(
                                "ATS detected by AI analysis",
                                extra={
                                    "job_url": job_url,
                                    "ats_provider": response.get("ats_provider"),
                                    "confidence": response.get("confidence")
                                },
                            )

                        # Case 2: Requires scraping - navigate to apply URL and check again
                        elif response.get("requires_scraping") == True:
                            if  response.get("apply_url") or  response.get("apply_button_text"):
                                try:
                                    if response.get("apply_url"):
                                        apply_url = response.get("apply_url")
                                        logger.debug(
                                            "Requires additional scraping of apply URL",
                                            extra={"job_url": job_url, "apply_url": apply_url},
                                        )
                                        
                                        page = await self._browser.get_current_page()
                                        page_url = await page.get_url()
                                        filter_domain = self._tracker.extract_domain(page_url)
                                        apply_url = self._tracker.normalize_full_path(apply_url, filter_domain)
                                        # Navigate to apply URL and scrape again
                                        await self._navigate(apply_url)
                                        
                                    else:
                                        button_text = response.get("apply_button_text")
                                        prompt = (
                                            f"Find the clickable element whose visible text most closely matches "
                                            f"'{button_text or 'Apply Now'}' and is used to apply for a job."
                                        )
                                        
                                        button = await page.get_element_by_prompt(prompt, llm=self._llm)
                                        if button:
                                            await button.click("left")
                                            logger.debug(
                                                "Clicked load more button",
                                                extra={"button_text": button_text},
                                            )
                                            await asyncio.sleep(self._config.page_load_wait)
                                            
                                        else:
                                            logger.warning(
                                                "Load more button not found, stopping",
                                                extra={"button_text": button_text},
                                            )    

                                    second_text_extracted = await self._extractor.extract()
                                    if text_extracted.structured_text != second_text_extracted.structured_text:
                                        # Analyze the apply page
                                        second_analysis = await self._analyzer.analyze(
                                            apply_url,
                                            second_text_extracted.structured_text,
                                            prompt_type=AnalysisPromptType.STRUCTURED,
                                            main_domain=domain
                                        )
                                        response = second_analysis.response
                                        response["job_url"] = job_url
                                        if second_analysis.error:
                                            response['error'] = analysis.error

                                        
                                except Exception as e:
                                    response = {
                                        "error": str(e),
                                        "message": "Error scraping job details",
                                        "job_url": job_url
                                    }
                        # Case 3: No ATS and no scraping needed
                        else:
                            logger.debug(
                                "Job details scraped successfully",
                                extra={"job_url": job_url, "is_ats": response.get("is_ats")},
                            )
                            
                        
                    else:
                        logger.warning(
                            "Job details analysis failed",
                            extra={"job_url": job_url, "error": analysis.error},
                        )
                        response = analysis.response
                        response["job_url"] = job_url
                        response['error'] = analysis.error

                        
                except Exception as e:
                    response = {
                        "error": str(e),
                        "message": "Error scraping job details",
                        "job_url": job_url
                    }
                    logger.error(
                        "Error scraping job details",
                        extra={"job_url": job_url, "error": str(e)},
                        exc_info=True,
                    )
            break
                
        return response
    
    # async def ats_checks(self, jobs: list["JobEntry"], domain: str):
    #     response = {}
    #     for i, job in enumerate(jobs):
    #         if not job.url:
    #             logger.debug(
    #                 "Skipping job without URL",
    #                 extra={"job_index": i, "job_title": job.title},
    #             )
    #             continue
                
    #         # Detect ATS and create document
    #         ats_info = ATSDetector.detect_ats(job.url, domain)
    #         logger.debug(
    #             "ATS detection completed",
    #             extra={
    #                 "job_url": job.url,
    #                 "is_ats": ats_info["is_ats"],
    #                 "ats_provider": ats_info["ats_provider"],
    #             },
    #         )
            
    #         if ats_info["is_ats"]:
    #             response["m_is_ats"] = ats_info["is_ats"]
    #             response["m_is_known_ats"] = ats_info["is_known_ats"]
    #             response['m_is_external_application'] = ats_info["is_external_application"]
    #             response['m_ats_provider'] = ats_info["ats_provider"]
    #             response['m_reasoning'] = ats_info["detection_reason"]
                
    #         else:
    #             try:
    #                 page = await self._browser.get_current_page()
    #                 page_url = await page.get_url()
                    
    #                 filter_domain = self._tracker.extract_domain(page_url)
    #                 job.url = self._tracker.normalize_full_path(job.url, filter_domain)
                    
                    
    #                 await self._navigate(job.url)
    #                 text_extracted = await self._extractor.extract()
    #                 analysis = await self._analyzer.analyze(
    #                     job.url,
    #                     text_extracted.structured_text,
    #                     prompt_type=AnalysisPromptType.STRUCTURED,
    #                     main_domain=domain
    #                 )

    #                 if analysis.success:
    #                     response = analysis.response
    #                     logger.debug(
    #                         "Job details scraped successfully",
    #                         extra={"job_url": job.url},
    #                     )
    #                 else:
    #                     response = {"error": analysis.error, "message": "AI api error"}
    #                     logger.warning(
    #                         "Job details analysis failed",
    #                         extra={"job_url": job.url, "error": analysis.error, "job_url": job.url},
    #                     )
    #             except Exception as e:
    #                 response = {"error": str(e), "message": "Error scraping job details", "job_url": job.url}
    #                 logger.error(
    #                     "Error scraping job details",
    #                     extra={"job_url": job.url, "error": str(e)},
    #                     exc_info=True,
    #                 )
    #             return response

            
    
    
    
    async def scrape_job_details(
        self,
        domain: str,
        jobs: list["JobEntry"],
        filter_url: str | None,
        skip_already_scraped: bool = False,
    ) -> list["JobEntry"]:
        logger.info(
            "Starting job details scrape",
            extra={"job_count": len(jobs), "skip_already_scraped": skip_already_scraped},
        )

        for i, job in enumerate(jobs):
            if not job.url:
                logger.debug(
                    "Skipping job without URL",
                    extra={"job_index": i, "job_title": job.title},
                )
                continue

            if skip_already_scraped and self._tracker.is_visited(job.url):
                logger.debug(
                    "Skipping already visited job",
                    extra={
                        "job_index": i + 1,
                        "total_jobs": len(jobs),
                        "job_title": job.title,
                        "job_url": job.url,
                    },
                )
                continue

            logger.debug(
                "Scraping job details",
                extra={
                    "job_index": i + 1,
                    "total_jobs": len(jobs),
                    "job_title": job.title,
                    "job_url": job.url,
                },
            )

            try:
                page = await self._browser.get_current_page()
                page_url = await page.get_url()
                
                filter_domain = self._tracker.extract_domain(page_url)
                job.url = self._tracker.normalize_full_path(job.url, filter_domain)
                
                
                await self._navigate(job.url)
                text_extracted = await self._extractor.extract()
                analysis = await self._analyzer.analyze(
                    job.url,
                    text_extracted.structured_text,
                    prompt_type=AnalysisPromptType.STRUCTURED,
                    main_domain=domain
                )
                
                # result = analysis.response
                # if analysis.success and result.get('page_category', '') == "not_job_related":
                #     logger.info(
                #         "Job detail page not job related, skipping",
                #         extra={"job_url": job.url},
                #     )
                #     continue
                if analysis.success:
                    job.details = analysis.response
                    logger.debug(
                        "Job details scraped successfully",
                        extra={"job_url": job.url},
                    )
                else:
                    job.details = {"error": analysis.error, "message": "AI api error"}
                    logger.warning(
                        "Job details analysis failed",
                        extra={"job_url": job.url, "error": analysis.error, "job_url": job.url},
                    )
            except Exception as e:
                job.details = {"error": str(e), "message": "Error scraping job details", "job_url": job.url}
                logger.error(
                    "Error scraping job details",
                    extra={"job_url": job.url, "error": str(e)},
                    exc_info=True,
                )
                
            # Detect ATS and create document
            ats_info = ATSDetector.detect_ats(job.url, domain)
            logger.debug(
                "ATS detection completed",
                extra={
                    "job_url": job.url,
                    "is_ats": ats_info["is_ats"],
                    "ats_provider": ats_info["ats_provider"],
                },
            )

            job_doc = job.details or {}
            if not job_doc:
                logger.debug("No job_doc details found",
                                extra={"url": job.url})
                continue

            job_doc['main_domain'] = domain
            job_doc["raw_text"] = text_extracted.structured_text
            job_doc['filter_domain'] = filter_url
            job_doc["url"] = job.url
            job_doc["is_known_ats"] = ats_info["is_known_ats"]
            job_doc['is_ats'] = ats_info["is_ats"]
            job_doc['is_external_application'] = ats_info["is_external_application"]
            job_doc['ats_provider'] = ats_info["ats_provider"]
            job_doc['detection_reason'] = ats_info["detection_reason"]
            
            job.details = job_doc
        
            logger.debug(
                "Job document created",
                extra={"job_url": job.url},
            )

        logger.info(
            "Job details scrape completed",
            extra={"job_count": len(jobs)},
        )
        return jobs























# # =============================================================================
# # Job Scraper
# # =============================================================================






# class JobScraper:
#     def __init__(
#         self,
#         browser: BrowserSession,
#         llm: ChatOpenAI,
#         extractor: "DOMContentExtractor",
#         analyzer: JobPageAnalyzer,
#         config: Optional[JobScraperConfig] = None,
#     ):
#         self._browser = browser
#         self._llm = llm
#         self._extractor = extractor
#         self._analyzer = analyzer
#         self._config = config or JobScraperConfig()
#         self._pagination_handler = PaginationHandler(browser, llm, extractor)
#         logger.debug(
#             "JobScraper initialized",
#             extra={
#                 "max_navigation": self._config.max_navigation,
#                 "page_load_wait": self._config.page_load_wait,
#                 "llm_model": self._config.llm_model,
#             },
#         )

#     async def _get_page(self):
#         return await self._browser.get_current_page()

#     async def _navigate(self, url: str) -> None:
#         logger.debug(
#             "Navigating to URL",
#             extra={"url": url},
#         )
#         page = await self._get_page()
#         await page.goto(url)
#         await asyncio.sleep(self._config.page_load_wait)
#         logger.debug(
#             "Navigation completed",
#             extra={"url": url, "wait_time": self._config.page_load_wait},
#         )

#     async def _extract_and_analyze(self, url: str) -> dict[str, Any]:
#         logger.debug(
#             "Starting extract and analyze",
#             extra={"url": url},
#         )
#         content = await self._extractor.extract()
#         result = await self._analyzer.analyze(url, content.structured_text)
#         if result.success:
#             logger.debug(
#                 "Extract and analyze succeeded",
#                 extra={"url": url},
#             )
#             return result.response
#         else:
#             logger.warning(
#                 "Extract and analyze failed",
#                 extra={"url": url, "error": result.error},
#             )
#             return {}

#     async def scrape_jobs(self, url: str) -> list[JobEntry]:
#         logger.info(
#             "Starting job scrape",
#             extra={"url": url},
#         )

#         await self._navigate(url)
#         nav_count = 0
#         all_jobs: list[JobEntry] = []

#         while True:
#             content = await self._extractor.extract()
#             logger.debug(
#                 "Content extracted for analysis",
#                 extra={"url": url, "content_length": len(content.structured_text)},
#             )
#             analysis = await self._analyzer.analyze(url, content.structured_text, json_resonse=True)

#             if not analysis.success:
#                 logger.error(
#                     "Analysis failed",
#                     extra={"url": url, "error": analysis.error},
#                 )
#                 break

#             result = analysis.response
#             page_category = result.get("page_category", "")
#             logger.debug(
#                 "Analysis result",
#                 extra={
#                     "url": url,
#                     "page_category": page_category,
#                     "next_action": result.get("next_action"),
#                 },
#             )

#             if page_category == "not_job_related":
#                 logger.info(
#                     "Page not job related",
#                     extra={"url": url, "page_category": page_category},
#                 )
#                 return all_jobs

#             if result.get("next_action") == "navigate":
#                 if nav_count >= self._config.max_navigation:
#                     logger.warning(
#                         "Max navigation reached",
#                         extra={
#                             "nav_count": nav_count,
#                             "max_navigation": self._config.max_navigation,
#                         },
#                     )
#                     return all_jobs

#                 nav_target = result.get("next_action_target", {})
#                 nav_url = nav_target.get("url", "")
#                 nav_url = TextProcessor.normalize_url(nav_url, result.get("domain_name", ""))

#                 if nav_url and nav_url != url:
#                     nav_count += 1
#                     url = nav_url
#                     await self._navigate(url)
#                     logger.info(
#                         "Navigated to new URL",
#                         extra={
#                             "url": url,
#                             "nav_count": nav_count,
#                             "max_navigation": self._config.max_navigation,
#                         },
#                     )
#                     continue

#                 link_text = nav_target.get("link_text", "")
#                 if link_text:
#                     nav_count += 1
#                     page = await self._get_page()
#                     prompt = (
#                         f"Find the clickable element whose visible text most closely matches "
#                         f"'{link_text}' and is used to navigate to the job listings page."
#                     )
#                     logger.debug(
#                         "Searching for navigation element",
#                         extra={"link_text": link_text, "prompt": prompt},
#                     )
#                     button = await page.get_element_by_prompt(prompt, llm=self._llm)
#                     if button:
#                         await button.click("left")
#                         await asyncio.sleep(self._config.page_load_wait)
#                         logger.info(
#                             "Clicked navigation element",
#                             extra={"link_text": link_text},
#                         )
#                         continue

#                 logger.debug(
#                     "No valid navigation target found",
#                     extra={"nav_target": nav_target},
#                 )
#                 return all_jobs

#             if page_category == "jobs_listed":
#                 jobs_on_page = result.get("jobs_listed_on_page", [])
#                 for job in jobs_on_page:
#                     all_jobs.append(JobEntry(
#                         title=job.get("title", ""),
#                         url=job.get("job_url", ""),
#                     ))
#                 logger.info(
#                     "Found jobs on page",
#                     extra={"job_count": len(jobs_on_page), "url": url},
#                 )

#                 pagination = result.get("pagination", {})
#                 logger.debug(
#                     "Pagination info",
#                     extra={
#                         "is_paginated_page": pagination.get("is_paginated_page"),
#                         "has_more_pages": pagination.get("has_more_pages"),
#                     },
#                 )

#                 if not pagination.get("is_paginated_page") and pagination.get("has_more_pages"):
#                     logger.info(
#                         "Handling load more pagination",
#                         extra={"url": url},
#                     )
#                     contents = await self._pagination_handler.handle_load_more(url)
#                     for chunk in contents:
#                         chunk_analysis = await self._analyzer.analyze(url, chunk)
#                         if chunk_analysis.success:
#                             for job in chunk_analysis.response.get("jobs_listed_on_page", []):
#                                 all_jobs.append(JobEntry(
#                                     title=job.get("title", ""),
#                                     url=job.get("job_url", ""),
#                                 ))
#                     logger.info(
#                         "Load more pagination completed",
#                         extra={"total_jobs": len(all_jobs)},
#                     )
#                     return all_jobs

#                 if pagination.get("is_paginated_page"):
#                     logger.info(
#                         "Handling standard pagination",
#                         extra={"url": url},
#                     )
#                     contents = await self._pagination_handler.handle_pagination(url)
#                     for page_content in contents[1:]:
#                         page_analysis = await self._analyzer.analyze(url, page_content)
#                         if page_analysis.success:
#                             for job in page_analysis.response.get("jobs_listed_on_page", []):
#                                 all_jobs.append(JobEntry(
#                                     title=job.get("title", ""),
#                                     url=job.get("job_url", ""),
#                                 ))
#                     logger.info(
#                         "Standard pagination completed",
#                         extra={"total_jobs": len(all_jobs)},
#                     )
#                     return all_jobs

#                 return all_jobs

#             logger.debug(
#                 "Breaking main loop - unhandled page category",
#                 extra={"page_category": page_category},
#             )
#             break

#         logger.info(
#             "Job scrape completed",
#             extra={"url": url, "total_jobs": len(all_jobs)},
#         )
#         return all_jobs

#     async def scrape_job_details(self, jobs: list[JobEntry]) -> list[JobEntry]:
#         logger.info(
#             "Starting job details scrape",
#             extra={"job_count": len(jobs)},
#         )

#         for i, job in enumerate(jobs):
#             if not job.url:
#                 logger.debug(
#                     "Skipping job without URL",
#                     extra={"job_index": i, "job_title": job.title},
#                 )
#                 continue

#             logger.debug(
#                 "Scraping job details",
#                 extra={
#                     "job_index": i + 1,
#                     "total_jobs": len(jobs),
#                     "job_title": job.title,
#                     "job_url": job.url,
#                 },
#             )

#             try:
#                 await self._navigate(job.url)
#                 analysis = await self._analyzer.analyze(
#                     job.url,
#                     (await self._extractor.extract()).structured_text,
#                     prompt_type=AnalysisPromptType.STRUCTURED,
#                 )
#                 if analysis.success:
#                     job.details = analysis.response
#                     logger.debug(
#                         "Job details scraped successfully",
#                         extra={"job_url": job.url},
#                     )
#                 else:
#                     logger.warning(
#                         "Job details analysis failed",
#                         extra={"job_url": job.url, "error": analysis.error},
#                     )
#             except Exception as e:
#                 logger.error(
#                     "Error scraping job details",
#                     extra={"job_url": job.url, "error": str(e)},
#                     exc_info=True,
#                 )

#         logger.info(
#             "Job details scrape completed",
#             extra={"job_count": len(jobs)},
#         )
#         return jobs

