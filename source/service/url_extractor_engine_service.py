from utils.logging import setup_logger
from playwright.async_api import Page
from service.brower_scraper_service import DOMContentExtractor
from utils.domain_name_filters import URLFilter
import tldextract
from urllib.parse import urlparse
import asyncio

# Import the three search engine nodes — do NOT modify them
from search_engine_node.duckduckgo_browser_search_node import duckduckgo_browser_search_node
from search_engine_node.duckduckgo_search_node import duckduckgo_search_node
from search_engine_node.google_search_node import google_search_node


logger = setup_logger(__name__)


class UrlExtractor:
    def __init__(self, page: Page, extractor: DOMContentExtractor):
        self._page = page
        self._extractor = extractor
        logger.debug("UrlExtractor initialized")

    # -------------------------------------------------------------------------
    # Public: domain-based discovery (unchanged)
    # -------------------------------------------------------------------------

    async def discover_job_urls_from_domain(
        self,
        domain: str,
        try_common_paths: bool = False,
        extract_from_homepage: bool = True,
    ) -> dict:
        logger.info(
            "Starting job URL discovery from domain",
            extra={
                "domain": domain,
                "try_common_paths": try_common_paths,
                "extract_from_homepage": extract_from_homepage,
            },
        )
        discovered_urls: set[str] = set()
        base_url = f"https://{domain.replace('https://', '').replace('http://', '').strip('/')}"
        logger.debug("Base URL constructed", extra={"base_url": base_url})

        if extract_from_homepage:
            logger.debug("Extracting URLs from homepage")
            response_value = await self._extract_urls_from_page(base_url)
            homepage_urls = response_value.get("result", [])
            if not homepage_urls:
                return response_value
            discovered_urls.update(homepage_urls)
            logger.debug("Homepage URLs extracted", extra={"urls_found": len(homepage_urls)})

        all_urls = list(discovered_urls)
        domain_filtered = URLFilter.filter_by_domain(all_urls, domain)
        web_filtered = URLFilter.filter_web_pages_only(domain_filtered)
        job_filtered = URLFilter.filter_job_urls(web_filtered)

        logger.info(
            "Job URL discovery completed",
            extra={
                "domain": domain,
                "total_discovered": len(all_urls),
                "domain_filtered": len(domain_filtered),
                "web_filtered": len(web_filtered),
                "job_filtered": len(job_filtered),
            },
        )
        response_value["result"] = job_filtered
        response_value["domain"] = domain
        response_value["meta_data"]["job_urls"] = len(job_filtered)
        return response_value

    # -------------------------------------------------------------------------
    # Public: search with engine fallback chain
    # -------------------------------------------------------------------------

    async def search_duckduckgo(self, query: str, domain: str) -> dict:
        """
        Search for job URLs using a three-engine fallback chain:
          1. DuckDuckGo browser  (duckduckgo_browser_search_node)
          2. Google browser      (google_search_node)
          3. DuckDuckGo HTTP     (duckduckgo_search_node)

        Each engine node is imported as-is; this method only wires them together,
        applies domain/job URL filtering, and normalises the return value.

        Returns the same dict shape as the original search_duckduckgo.
        """
        logger.info(
            "Starting search with engine fallback chain",
            extra={"query": query, "domain": domain},
        )

        # Minimal state expected by all three node functions
        base_state = {
            "search_query": query,
            "playwright_page": self._page,   # HTTP node ignores this key safely
        }

        # ------------------------------------------------------------------
        # Engine 1: DuckDuckGo browser
        # ------------------------------------------------------------------
        logger.debug("Trying engine 1: DuckDuckGo browser")
        result_state = await duckduckgo_browser_search_node(self._page, query)
        raw_urls: list[str] | None = result_state.get("results")

        if not raw_urls:
            logger.warning(
                "DuckDuckGo browser search failed, falling back to Google",
                extra={"error": result_state.get("search_error")},
            )

            # ----------------------------------------------------------------
            # Engine 2: Google browser
            # ----------------------------------------------------------------
            logger.debug("Trying engine 2: Google browser")
            result_state = await google_search_node(self._page, query)
            raw_urls = result_state.get("results")

            if not raw_urls:
                logger.warning(
                    "Google browser search failed, falling back to DuckDuckGo HTTP",
                    extra={"error": result_state.get("search_error")},
                )

                # ------------------------------------------------------------
                # Engine 3: DuckDuckGo HTTP (no browser)
                # ------------------------------------------------------------
                logger.debug("Trying engine 3: DuckDuckGo HTTP")
                result_state = await duckduckgo_search_node(query)
                raw_urls = result_state.get("results")

                if not raw_urls:
                    logger.error(
                        "All three search engines failed",
                        extra={"error": result_state.get("search_error")},
                    )
                    return {
                        "success": False,
                        "error": result_state.get("search_error", "All search engines failed"),
                        "status": "All search engines exhausted",
                        "result": [],
                        "meta_data": {"original_domain": domain, "job_urls": 0},
                    }

        # ------------------------------------------------------------------
        # Filter raw URLs down to job URLs on the target domain
        # ------------------------------------------------------------------
        domain_filtered = URLFilter.filter_by_domain(raw_urls, domain)
        web_filtered    = URLFilter.filter_web_pages_only(domain_filtered)
        job_filtered    = URLFilter.filter_job_urls(web_filtered)

        logger.info(
            "Job URL discovery completed via search engine chain",
            extra={
                "domain": domain,
                "engine_used": result_state.get("current_step", "unknown"),
                "total_raw": len(raw_urls),
                "domain_filtered": len(domain_filtered),
                "web_filtered": len(web_filtered),
                "job_filtered": len(job_filtered),
            },
        )

        return {
            "success": True,
            "result": job_filtered,
            "meta_data": {
                "original_domain": domain,
                "job_urls": len(job_filtered),
                "engine_used": result_state.get("current_step", "unknown"),
                "total_raw_results": len(raw_urls),
            },
        }

    # -------------------------------------------------------------------------
    # Helpers (unchanged)
    # -------------------------------------------------------------------------

    def normalize_domain(self, url: str) -> str:
        ext = tldextract.extract(url)
        return f"{ext.domain}.{ext.suffix}".lower()

    async def _extract_urls_from_page(self, url: str) -> dict:
        logger.debug("Extracting URLs from page", extra={"url": url})
        for i in range(3):
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(15 * i)

                original_domain = self.normalize_domain(urlparse(url).netloc.lower())
                final_url       = self._page.url
                final_domain    = self.normalize_domain(urlparse(final_url).netloc.lower())
                redirected      = original_domain != final_domain

                logger.debug(
                    "Page loaded",
                    extra={
                        "original_url": url,
                        "final_url": final_url,
                        "redirected": redirected,
                        "attempt": i + 1,
                    },
                )

                resp = await self._extract_urls_from_current_page()
                if not resp.get("success"):
                    raise RuntimeError(
                        f"(status={resp.get('status')}, body={resp.get('error')})"
                    )
                break

            except Exception as e:
                logger.warning(
                    "Failed to load page for URL extraction",
                    extra={"url": url, "error": str(e), "attempt": i + 1},
                )
                if i == 2:
                    return {
                        "error": str(e),
                        "status": f"Failed to load page for URL {url} extraction",
                        "success": False,
                    }

        return {
            "result": resp.get("result", []),
            "meta_data": {
                "redirected": redirected,
                "original_url": url,
                "final_url": final_url,
                "original_domain": original_domain,
                "final_domain": final_domain,
            },
            "success": True,
        }

    async def _extract_urls_from_current_page(self) -> dict:
        logger.debug("Extracting URLs from current page")
        try:
            urls = await self._page.evaluate(
                """
                () => {
                    const urls = [];
                    const links = document.querySelectorAll('a[href]');
                    links.forEach(link => {
                        const href = link.href;
                        if (href && href.startsWith('http')) {
                            urls.push(href);
                        }
                    });
                    return [...new Set(urls)];
                }
                """
            )
            result = urls or []
            logger.debug("URLs extracted from current page", extra={"urls_count": len(result)})
            return {"result": result, "success": True}

        except Exception as e:
            logger.warning("Failed to extract URLs from current page", extra={"error": str(e)})
            return {
                "status": "Failed to extract URLs from current page",
                "error": str(e),
                "success": False,
            }