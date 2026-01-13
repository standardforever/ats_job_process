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
    jobs: list["JobEntry"] = field(default_factory=list)
    visited_urls: list[str] = field(default_factory=list)
    llm_reasoning: list[str] = field(default_factory=list)
    job_detail_urls: list[str] = field(default_factory=list)
    error: Optional[str] = None
    message: Optional[str] = None
    success: Optional[bool] = True
    skip_url: bool = False
    total_token: int = 0
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
    def extract_domain(url: str, main_domain: bool = False) -> str:
        """
        Extract domain/host from URL.
        
        Args:
            url: The URL to extract domain from
            main_domain: If True, returns base domain (e.g., 'example.com' from 'jobs.example.com')
                        If False, returns full domain including subdomains
        
        Examples:
            extract_domain("https://www.example.com/Jobs/")  
                → "www.example.com"
            
            extract_domain("https://www.example.com/Jobs/", main_domain=True)  
                → "example.com"
            
            extract_domain("https://careers.example.com")  
                → "careers.example.com"
            
            extract_domain("https://careers.example.com", main_domain=True)  
                → "example.com"
            
            extract_domain("https://apply.example.co.uk/form", main_domain=True)  
                → "example.co.uk"  (handles compound TLDs)
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
            
            if main_domain:
                # Use tldextract to properly handle compound TLDs
                extracted = tldextract.extract(domain)
                
                if extracted.domain and extracted.suffix:
                    base_domain = f"{extracted.domain}.{extracted.suffix}"
                else:
                    # Fallback to simple extraction if tldextract fails
                    domain_clean = domain.replace("www.", "")
                    parts = domain_clean.split(".")
                    base_domain = ".".join(parts[-2:]) if len(parts) >= 2 else domain_clean
                
                logger.debug(
                    "Base domain extracted",
                    extra={
                        "original_url": url,
                        "full_domain": domain,
                        "base_domain": base_domain,
                        "subdomain": extracted.subdomain if extracted else None,
                    },
                )
                return base_domain
            else:
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
    def normalize_full_path(url: str, domain: str) -> str:
        if url.startswith("/") and domain:
            if not domain.startswith(("http://", "https://")):
                domain = f"https://{domain}"
            return domain.rstrip("/") + url
        return url

    @staticmethod
    def normalize_url(url: str) -> str:
        if not url:
            return ""

        url = url.strip().lower()

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        parsed = urlparse(url)
        return urlunparse((
            parsed.scheme,
            parsed.netloc.replace("www.", ""),
            parsed.path.rstrip("/"),
            "",
            "",
            "",
        ))


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
        try:
            self._current_visited = []
            logger.info(
                "Starting tracked job scrape",
                extra={"url": url},
            )
            await asyncio.sleep(self._config.page_load_wait)

            if self._tracker.should_skip(url):
                logger.info(
                    "Skipping already visited URL",
                    extra={"url": url},
                )
                return ScrapeResult(jobs=[], visited_urls=self._current_visited, job_detail_urls=[], skip_url=True, message="Skipping already visited URL")

            await self._navigate(url)

            nav_count = 0
            all_jobs: list[JobEntry] = []
            total_token = 0
            llm_reasoning = []

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
                llm_reasoning.append({
                    "token_usage": analysis.token_usage,
                    "reasoning": analysis.response.get('reasoning'),
                    "confidence": analysis.response.get('confidence'),
                    "url": url
                })
                total_token += analysis.token_usage
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
                    return ScrapeResult(jobs=all_jobs, visited_urls=self._current_visited, job_detail_urls=[j.url for j in all_jobs if j.url], message="Page not job related", success=False, total_token=total_token, llm_reasoning=llm_reasoning)

                elif page_category == "single_job_posting":
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
                        success=True,
                        total_token=total_token, llm_reasoning=llm_reasoning
                    )
                
                elif page_category == "jobs_listed":
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
                        success=True,
                        total_token=total_token, llm_reasoning=llm_reasoning
                    )

                elif page_category == "navigation_required" or page_category == "job_listings_preview_page":
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
                        "Found jobs on page stoping point",
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
                            success=False,
                            total_token=total_token, llm_reasoning=llm_reasoning
                        )

                    nav_target = result.get("next_action_target", {})
                    
                    nav_url = nav_target.get("url", "")
                    link_text = nav_target.get("link_text", "")
                    if nav_url:
                        current_page = await self._get_page()
                        
                        page_url = await current_page.get_url()
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
                                    total_token=total_token, llm_reasoning=llm_reasoning
                                )

                            nav_count += 1
                            url = nav_url
                            await self._navigate(url)
                            logger.info(
                                "Navigated to new URL",
                                extra={"url": url, "nav_count": nav_count},
                            )
                            continue
                    
                    elif link_text:
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
                        success=False,
                        total_token=total_token, llm_reasoning=llm_reasoning
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
            llm_reasoning.append(analysis.response)
            return ScrapeResult(
                jobs=all_jobs,
                visited_urls=self._current_visited,
                job_detail_urls=[j.url for j in all_jobs if j.url],
                message="No valid ai content meets",
                success=False,
                llm_reasoning=llm_reasoning,
                total_token=total_token
            )
        except Exception as e:
            logger.error(
                "Scrapping job error",
                extra={"error": str(e)},
            )
            return ScrapeResult(
                jobs=all_jobs,
                visited_urls=self._current_visited,
                job_detail_urls=[j.url for j in all_jobs if j.url],
                message="Error scrapping job pages",
                success=False,
                error=str(e),
                total_token=total_token,
                llm_reasoning=llm_reasoning,
            ) 


        
    async def ats_checks(self, jobs: list[str], domain: str):
        results = []  # Process all jobs, not just first one
        total_tokens = 0
        
        for i, job_url in enumerate(jobs):
            result = await self._process_single_job(job_url, domain)
            results.append(result)
            
            # Accumulate tokens
            total_tokens += result.get("token_usage", 0)
            
            # Optional: Stop on first successful
            if result.get("status") == "success":
                break
        
        return {
            "results": results,
            "total_tokens": total_tokens,
            "jobs_processed": len(results)
        }

    async def _process_single_job(self, job_url: str, domain: str):
        """Process a single job URL and return standardized result"""
        
        token_usage = 0
        
        # Initial ATS detection
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
            return {
                "status": "success",
                "job_url": job_url,
                "is_ats": True,
                "is_known_ats": ats_info["is_known_ats"],
                "is_external_application": ats_info["is_external_application"],
                "ats_provider": ats_info["ats_provider"],
                "reasoning": ats_info["detection_reason"],
                "confidence": "high",
                "detection_method": "url_pattern",
                "token_usage": token_usage
            }
        
        try:
            # Navigate and check for redirects
            await self._navigate(job_url)
            page = await self._browser.get_current_page()
            current_url = await page.get_url()
            
            # CRITICAL: Check if we were redirected
            redirect_info = self._check_redirect(job_url, current_url, domain)
            if redirect_info["redirected"]:
                return {
                    "status": "uncertain",
                    "job_url": job_url,
                    "current_url": current_url,
                    "is_ats": None,
                    "confidence": "uncertain",
                    "reasoning": redirect_info["reason"],
                    "redirect_type": redirect_info["type"],
                    "requires_manual_review": True,
                    "token_usage": token_usage
                }
            
            # Extract and analyze
            text_extracted = await self._extractor.extract()
            
            analysis = await self._analyzer.analyze(
                job_url,
                text_extracted.structured_text,
                prompt_type=AnalysisPromptType.STRUCTURED,
                main_domain=domain
            )
            
            # Track tokens from first analysis
            token_usage += analysis.token_usage if hasattr(analysis, 'token_usage') else 0

            if not analysis.success:
                return {
                    "status": "error",
                    "job_url": job_url,
                    "is_ats": None,
                    "confidence": "uncertain",
                    "error": analysis.error,
                    "reasoning": "AI analysis failed",
                    "token_usage": token_usage
                }
            
            response = analysis.response

            # Check if page is not job-related
            if response.get("is_job_related") == False:
                return {
                    "status": "uncertain",
                    "job_url": job_url,
                    "current_url": current_url,
                    "is_ats": None,
                    "confidence": "uncertain",
                    "reasoning": response.get("reasoning"),
                    "application_type": response.get("application_type"),
                    "requires_manual_review": True,
                    "detection_method": "ai_analysis",
                    "token_usage": token_usage
                }
            
            # Case 1: AI detected ATS with confidence
            if response.get("confidence") in ["high", "medium"]:
                return {
                    "status": "success",
                    "job_url": job_url,
                    "is_ats": response.get("is_ats"),
                    "ats_provider": response.get("ats_provider"),
                    "confidence": response.get("confidence"),
                    "application_type": response.get("application_type"),
                    "reasoning": response.get("reasoning"),
                    "indicators_found": response.get("indicators_found", []),
                    "detection_method": "ai_analysis",
                    "token_usage": token_usage
                }
            
            # Case 2: Requires additional scraping
            elif response.get("requires_scraping") == True:
                result = await self._handle_additional_scraping(
                    job_url, response, domain, text_extracted.structured_text, token_usage
                )
                return result

            # Case 3: Uncertain/Low confidence
            else:
                return {
                    "status": "uncertain",
                    "job_url": job_url,
                    "is_ats": response.get("is_ats"),
                    "ats_provider": response.get("ats_provider"),
                    "confidence": response.get("confidence"),
                    "application_type": response.get("application_type"),
                    "reasoning": response.get("reasoning"),
                    "indicators_found": response.get("indicators_found", []),
                    "detection_method": "ai_analysis",
                    "requires_manual_review": True,
                    "token_usage": token_usage
                }
                
        except Exception as e:
            logger.error(
                "Error processing job",
                extra={"job_url": job_url, "error": str(e)},
                exc_info=True,
            )
            return {
                "status": "error",
                "job_url": job_url,
                "is_ats": None,
                "confidence": "uncertain",
                "error": str(e),
                "reasoning": "Exception during processing",
                "token_usage": token_usage
            }

    def _check_redirect(self, original_url: str, current_url: str, domain: str) -> dict:
        """Check if navigation resulted in a problematic redirect"""
        
        from urllib.parse import urlparse, unquote
        
        # Parse both URLs
        original_parsed = urlparse(original_url.rstrip('/'))
        current_parsed = urlparse(current_url.rstrip('/'))
        
        # URL decode paths to handle encoded characters
        original_path = unquote(original_parsed.path.rstrip('/')).lower()
        current_path = unquote(current_parsed.path.rstrip('/')).lower()
        
        # Use existing domain extraction method
        original_domain = self._tracker.extract_domain(original_url, main_domain=True)
        current_domain = self._tracker.extract_domain(current_url, main_domain=True)
        
        # 1. Check domain change (only flag if base domain differs)
        if original_domain != current_domain:
            return {
                "redirected": True,
                "type": "external_domain",
                "reason": f"Domain changed from {original_domain} to {current_domain}",
                "original_url": original_url,
                "current_url": current_url
            }
        
        # 2. Same path = OK (even if query params differ)
        if original_path == current_path:
            return {"redirected": False}
        
        # 3. Path extended = OK (original path is prefix of current)
        # e.g., /jobs/123 -> /jobs/123/apply
        if current_path.startswith(original_path):
            extension = current_path[len(original_path):].strip('/')
            # Make sure it's a reasonable extension (not too many segments)
            if len(extension.split('/')) <= 5:
                return {"redirected": False}
        
        # 4. Path changed - flag it
        return {
            "redirected": True,
            "type": "path_changed",
            "reason": "URL path changed during navigation",
            "original_url": original_url,
            "current_url": current_url
        }

    async def _handle_additional_scraping(
        self, job_url: str, initial_response: dict, domain: str, original_text: str, token_usage: int = 0
    ) -> dict:
        """Handle cases where additional scraping is required"""
        
        try:
            apply_url = initial_response.get("apply_url")
            button_text = initial_response.get("apply_button_text")
            
            if not apply_url and not button_text:
                return {
                    "status": "uncertain",
                    "job_url": job_url,
                    "is_ats": None,
                    "confidence": "uncertain",
                    "reasoning": "Requires scraping but no apply_url or button_text provided",
                    "requires_manual_review": True,
                    "token_usage": token_usage
                }
            
            page = await self._browser.get_current_page()
            
            # Navigate to apply URL or click button
            if apply_url:
                page_url = await page.get_url()
                filter_domain = self._tracker.extract_domain(page_url)
                apply_url = self._tracker.normalize_full_path(apply_url, filter_domain)
                await self._navigate(apply_url)
            else:
                prompt = (
                    f"Find the clickable element whose visible text most closely matches "
                    f"'{button_text}' and is used to apply for a job."
                )
                button = await page.get_element_by_prompt(prompt, llm=self._llm)
                
                if not button:
                    return {
                        "status": "uncertain",
                        "job_url": job_url,
                        "is_ats": None,
                        "confidence": "uncertain",
                        "reasoning": f"Apply button '{button_text}' not found on page",
                        "requires_manual_review": True,
                        "token_usage": token_usage
                    }
                
                await button.click("left")
                await asyncio.sleep(self._config.page_load_wait)
            
            # Check for redirect after clicking/navigating
            current_url = await page.get_url()
            redirect_info = self._check_redirect(job_url, current_url, domain)
            
            # Extract new page content
            second_text_extracted = await self._extractor.extract()
            
            # Analyze the new page
            second_analysis = await self._analyzer.analyze(
                current_url,
                second_text_extracted.structured_text,
                prompt_type=AnalysisPromptType.STRUCTURED,
                main_domain=domain
            )
            
            # Track tokens from second analysis
            token_usage += second_analysis.token_usage if hasattr(second_analysis, 'token_usage') else 0
            
            if not second_analysis.success:
                return {
                    "status": "error",
                    "job_url": job_url,
                    "is_ats": None,
                    "confidence": "uncertain",
                    "error": second_analysis.error,
                    "reasoning": "Second analysis failed after navigation",
                    "token_usage": token_usage
                }
            
            response = second_analysis.response
            
            # Return with clear confidence
            if response.get("confidence") in ["high", "medium"]:
                return {
                    "status": "success",
                    "job_url": job_url,
                    "is_ats": response.get("is_ats"),
                    "ats_provider": response.get("ats_provider"),
                    "confidence": response.get("confidence"),
                    "application_type": response.get("application_type"),
                    "reasoning": response.get("reasoning"),
                    "indicators_found": response.get("indicators_found", []),
                    "detection_method": "ai_analysis_after_navigation",
                    "navigated_to": current_url,
                    "token_usage": token_usage
                }
            else:
                return {
                    "status": "uncertain",
                    "job_url": job_url,
                    "is_ats": None,
                    "confidence": "uncertain",
                    "reasoning": f"Low confidence after secondary scraping: {response.get('confidence')}",
                    "requires_manual_review": True,
                    "raw_response": response,
                    "token_usage": token_usage
                }
                
        except Exception as e:
            return {
                "status": "error",
                "job_url": job_url,
                "is_ats": None,
                "confidence": "uncertain",
                "error": str(e),
                "reasoning": "Error during additional scraping",
                "token_usage": token_usage
            }