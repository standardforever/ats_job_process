
from utils.logging import setup_logger
from playwright.async_api import  Page
from service.brower_scraper_service import DOMContentExtractor
from utils.domain_name_filters import URLFilter
import aiohttp
import tldextract
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import asyncio

# Configure logging
logger = setup_logger(__name__)

# =============================================================================
# Search Engine
# =============================================================================




class UrlExtractor:
    def __init__(self, page: Page, extractor: DOMContentExtractor):
        self._page = page
        self._extractor = extractor
        logger.debug("FallbackURLDiscovery initialized")

    async def discover_job_urls_from_domain(
        self,
        domain: str,
        try_common_paths: bool = False,
        extract_from_homepage: bool = True,
    ) -> dict:
        """
        Fallback: Navigate to domain and discover job URLs.
        
        Args:
            domain: The domain to explore (e.g., "openai.com")
            try_common_paths: Try common job page paths
            extract_from_homepage: Extract URLs from homepage first
            
        Returns:
            List of discovered job-related URLs
        """
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
        logger.debug(
            "Base URL constructed",
            extra={"base_url": base_url},
        )

        # Step 1: Try homepage and extract all links
        if extract_from_homepage:
            logger.debug("Extracting URLs from homepage")
            response_value = await self._extract_urls_from_page(base_url)
            homepage_urls = response_value.get("result", [])
            if not homepage_urls:
                return response_value
        
            discovered_urls.update(homepage_urls)
            logger.debug(
                "Homepage URLs extracted",
                extra={"urls_found": len(homepage_urls)},
            )

        # Filter discovered URLs
        all_urls = list(discovered_urls)
        logger.debug(
            "Starting URL filtering",
            extra={"total_discovered": len(all_urls)},
        )
        
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
                final_url = self._page.url
                final_domain = self.normalize_domain(urlparse(final_url).netloc.lower())

                redirected = original_domain != final_domain
        
                logger.debug(
                    "Page loaded",
                    extra={
                        "original_url": url,
                        "final_url": final_url,
                        "redirected": redirected,
                        "attempt": i + 1
                    },
                )
                resp = await self._extract_urls_from_current_page()
                if not resp.get("success"):
                    raise RuntimeError(
                        f"(status={resp.get("status")}, body={resp.get("error")})"
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
                        "success": False
                    }

        
   
        return {
            "result": resp.get('result', []),
            "meta_data": {
                "redirected": redirected,
                "original_url": url,
                "final_url": final_url,
                "original_domain": original_domain,
                "final_domain": final_domain,
            },
            "success": True
        }


    async def _extract_urls_from_current_page(self) ->  dict:
        """Extract all URLs from current page."""
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
            
            logger.debug(
                "URLs extracted from current page",
                extra={"urls_count": len(result)},
            )
            return {
                "result": result,
                "success": True
            }
        except Exception as e:
            logger.warning(
                "Failed to extract URLs from current page",
                extra={"error": str(e)},
            )
            return {
                "status": f"Failed to extract URLs from current page",
                "error": str(e),
                "success": False
            }   
            

    def _unwrap_ddg_url(self, href: str) -> str | None:
        """
        Extract the real destination URL from a DuckDuckGo redirect link.
        """
        from urllib.parse import urlparse, parse_qs, unquote
        if not href:
            return None

        # Handle protocol-relative URLs
        if href.startswith("//"):
            href = "https:" + href

        parsed = urlparse(href)

        if "duckduckgo.com/l/" not in parsed.netloc + parsed.path:
            return href  # already a real URL

        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg")

        if not uddg:
            return None

        return unquote(uddg[0])

        
    async def search_duckduckgo(
        self,
        query: str,
        domain: str,
        timeout: int = 15,
    ) -> dict:
        """
        Perform a DuckDuckGo search using HTTP (no browser)
        and extract result URLs.

        Returns:
            List[str]: deduplicated result URLs
        """
        logger.debug(
            "Starting DuckDuckGo HTTP search",
            extra={"query": query},
        )
        
        REAL_UA = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )


        headers = {
            "User-Agent": REAL_UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://duckduckgo.com/",
        }

        urls: list[str] = []

        for i in range(3):
            try:
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(
                        "https://duckduckgo.com/html/",
                        params={"q": query},
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as resp:
                        if resp.status != 200:
                            logger.error(
                                "DuckDuckGo HTTP search failed",
                                extra={
                                    "query": query,
                                    "status": resp.status,
                                    "attempt": i + 1,
                                },
                            )
                            error_text = await resp.text()

                            raise RuntimeError(
                                f"DuckDuckGo HTTP search failed "
                                f"(status={resp.status}, body={error_text[:500]})"
                            )

                        html = await resp.text()

                        # ✅ success → break out of retry loop
                        break

            except Exception as e:
                logger.warning(
                    "DuckDuckGo HTTP search attempt failed",
                    extra={
                        "query": query,
                        "attempt": i + 1,
                        "error": str(e),
                    },
                )

                # if this was the last attempt, return failure response
                if i == 2:
                    return {
                        "status": "error",
                        "success": False,
                        "error": str(e),
                    }
                
        logger.debug(
            "DuckDuckGo HTTP response received",
            extra={
                "query": query,
                "response_length": len(html),
            },
        )

        soup = BeautifulSoup(html, "lxml")

        for a in soup.select("a.result__a"):
            href = a.get("href")
            real_url = self._unwrap_ddg_url(href)

            if real_url and real_url.startswith("http"):
                urls.append(real_url)

        # Deduplicate (preserve order)
        seen = set()
        deduped = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                deduped.append(url)
                
        domain_filtered = URLFilter.filter_by_domain(deduped, domain)
        web_filtered = URLFilter.filter_web_pages_only(domain_filtered)
        job_filtered = URLFilter.filter_job_urls(web_filtered)
        
        logger.info(
            "Job URL discovery completed for duckduckgo",
            extra={
                "domain": domain,
                "total_discovered": len(deduped),
                "domain_filtered": len(domain_filtered),
                "web_filtered": len(web_filtered),
                "job_filtered": len(job_filtered),
            },
        )

        return  {
                "meta_data": {
                    "original_domain": domain,
                    "job_urls": len(job_filtered)
                },
                "success": True,
                "result": job_filtered,
            }
