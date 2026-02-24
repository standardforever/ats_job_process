import random
import asyncio
from urllib.parse import urlparse, parse_qs, unquote
from playwright.async_api import Page


def _unwrap_ddg_url(href: str) -> str | None:
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com/l/" not in parsed.netloc + parsed.path:
        return href
    qs = parse_qs(parsed.query)
    uddg = qs.get("uddg")
    if not uddg:
        return None
    return unquote(uddg[0])


COOKIE_SELECTORS = [
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('I agree')",
    "button[aria-label*='Accept']",
    "#onetrust-accept-btn-handler",
]

SEARCH_BOX_SELECTORS = [
    'input[name="q"]',
    'textarea[name="q"]',
    "#searchbox_input",
]

RESULT_SELECTORS = [
    'article[data-testid="result"] a[href]',
    "ol.react-results--main a[href]",
    "div.nrn-react-div a[href]",
    "a.result__a[href]",
]


async def _find_search_box(page):
    for selector in SEARCH_BOX_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=3000):
                return locator
        except Exception:
            continue
    return None


async def _handle_cookie_popup(page) -> None:
    for selector in COOKIE_SELECTORS:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=1500):
                await asyncio.sleep(random.uniform(0.5, 1.0))
                await btn.click()
                await asyncio.sleep(random.uniform(0.3, 0.8))
                return
        except Exception:
            continue


async def _extract_results(page) -> list[str]:
    for selector in RESULT_SELECTORS:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            if count > 0:
                urls = []
                for i in range(count):
                    try:
                        href = await locator.nth(i).get_attribute("href")
                        real_url = _unwrap_ddg_url(href)
                        if real_url and real_url.startswith("http"):
                            urls.append(real_url)
                    except Exception:
                        continue
                if urls:
                    return urls
        except Exception:
            continue

    try:
        urls = await page.evaluate("""
            () => {
                const urls = [];
                for (const link of document.querySelectorAll('a[href]')) {
                    const href = link.href;
                    if (href && href.startsWith('http') && !href.includes('duckduckgo.com')) {
                        urls.push(href);
                    }
                }
                return [...new Set(urls)];
            }
        """)
        return urls or []
    except Exception:
        return []


async def duckduckgo_browser_search_node(page: Page, query: str) -> dict:
    """
    Search DuckDuckGo using a Playwright browser with human-like behaviour.

    Args:
        page:  Playwright Page instance
        query: Search query string

    Returns:
        {"success": bool, "results": list[str], "error": str | None}
    """
    print("🦆 Node: DuckDuckGo browser search...")

    try:
        print(f"  ℹ️  Navigating to DuckDuckGo for: {query}")
        await page.goto("https://duckduckgo.com/", wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(0.5, 1.5))

        await _handle_cookie_popup(page)
        await asyncio.sleep(random.uniform(0.3, 0.6))

        search_box = await _find_search_box(page)
        if not search_box:
            msg = "DuckDuckGo search box not found — page is likely blocked or showing captcha"
            print(f"  ✗ {msg}")
            return {"success": False, "results": [], "error": msg}

        await search_box.click()
        await asyncio.sleep(random.uniform(0.2, 0.5))
        for char in query:
            await search_box.press(char)
            await asyncio.sleep(random.randint(50, 150) / 1000)
            if random.random() < 0.1:
                await asyncio.sleep(random.uniform(0.2, 0.5))

        await asyncio.sleep(random.uniform(0.5, 1.0))
        await search_box.press("Enter")

        results_loaded = False
        for selector in ["ol.react-results--main", "div.nrn-react-div", "#react-duckduckhunt"]:
            try:
                await page.locator(selector).wait_for(state="visible", timeout=8000)
                results_loaded = True
                break
            except Exception:
                continue

        if not results_loaded:
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

        await asyncio.sleep(random.uniform(1.0, 2.0))

        post_search_box = await _find_search_box(page)
        if not post_search_box:
            msg = "DuckDuckGo search box gone after submission — likely captcha triggered"
            print(f"  ✗ {msg}")
            return {"success": False, "results": [], "error": msg}

        results = await _extract_results(page)

        seen = set()
        deduped = []
        for url in results:
            if url not in seen:
                seen.add(url)
                deduped.append(url)

        if not deduped:
            msg = "DuckDuckGo returned no results"
            print(f"  ⚠️  {msg}")
            return {"success": False, "results": [], "error": msg}

        print(f"  ✓ Found {len(deduped)} result(s)")
        return {"success": True, "results": deduped, "error": None}

    except Exception as e:
        msg = f"Unexpected error during DuckDuckGo browser search: {str(e)}"
        print(f"  ✗ {msg}")
        return {"success": False, "results": [], "error": msg}