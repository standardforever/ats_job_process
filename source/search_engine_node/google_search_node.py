import random
import asyncio
from playwright.async_api import Page


SEARCH_ENGINE_DOMAINS = frozenset({
    "google.com", "google.co", "gstatic.com", "youtube.com",
    "duckduckgo.com", "accounts.google", "policies.google",
    "support.google", "webcache.googleusercontent", "translate.google",
})

RESULT_SELECTORS = [
    "div.g a[href]",
    "div.yuRUbf a[href]",
    "div[data-sokoban-container] a[href]",
    "a[jsname][href]",
    "h3 a[href]",
    "div#search a[href]",
]

COOKIE_SELECTORS = [
    "#L2AGLb", "#W0wltc",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('I agree')",
    "button:has-text('Reject all')",
    "button:has-text('Reject All')",
    "button[aria-label*='Accept']",
    "button[aria-label*='Reject']",
    "form[action*='consent'] button",
    "div[role='dialog'] button",
]


def _is_search_engine_url(url: str) -> bool:
    url_lower = url.lower()
    return any(domain in url_lower for domain in SEARCH_ENGINE_DOMAINS)


async def _handle_cookie_popup(page) -> None:
    try:
        consent_frame = page.frame_locator("iframe[src*='consent']")
        for selector in ["#L2AGLb", "button:has-text('Accept')", "button:has-text('Reject')"]:
            try:
                btn = consent_frame.locator(selector).first
                if await btn.is_visible(timeout=1000):
                    await asyncio.sleep(random.uniform(0.8, 1.5))
                    await btn.click()
                    await asyncio.sleep(random.uniform(0.5, 1.0))
                    return
            except Exception:
                continue
    except Exception:
        pass

    for selector in COOKIE_SELECTORS:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=800):
                await asyncio.sleep(random.uniform(0.6, 1.2))
                await btn.click()
                await asyncio.sleep(random.uniform(0.5, 1.0))
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
                        if href and href.startswith("http") and not _is_search_engine_url(href):
                            urls.append(href)
                    except Exception:
                        continue
                if urls:
                    return urls
        except Exception:
            continue

    try:
        urls = await page.evaluate("""
            () => {
                const searchEngineDomains = [
                    'google.com','google.co','gstatic.com','youtube.com',
                    'duckduckgo.com','accounts.google','policies.google',
                    'support.google','webcache.googleusercontent','translate.google'
                ];
                const urls = [];
                for (const link of document.querySelectorAll('a[href]')) {
                    const href = link.href;
                    const text = link.innerText?.trim() || '';
                    if (href && href.startsWith('http') && text.length > 3) {
                        const blocked = searchEngineDomains.some(d => href.toLowerCase().includes(d));
                        if (!blocked) urls.push(href);
                    }
                }
                return [...new Set(urls)];
            }
        """)
        return urls or []
    except Exception:
        return []


async def google_search_node(page: Page, query: str) -> dict:
    """
    Search Google using a Playwright browser with human-like behaviour.

    Args:
        page:  Playwright Page instance
        query: Search query string

    Returns:
        {"success": bool, "results": list[str], "error": str | None}
    """
    print("🔍 Node: Google search...")

    try:
        print(f"  ℹ️  Navigating to Google for: {query}")

        await page.set_viewport_size({
            "width": random.randint(1200, 1920),
            "height": random.randint(800, 1080),
        })

        await page.goto("https://www.google.com/", wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(0.5, 1.5))

        content = await page.content()
        if "captcha" in content.lower() or "unusual traffic" in content.lower():
            msg = "Google returned a captcha or unusual traffic page"
            print(f"  ✗ {msg}")
            return {"success": False, "results": [], "error": msg}

        await _handle_cookie_popup(page)
        await asyncio.sleep(random.uniform(0.3, 0.8))

        search_box = None
        for selector in ['textarea[name="q"]', 'input[name="q"]', '#APjFqb']:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=2000):
                    search_box = locator
                    break
            except Exception:
                continue

        if not search_box:
            msg = "Google search box not found — page may be blocked or layout changed"
            print(f"  ✗ {msg}")
            return {"success": False, "results": [], "error": msg}

        await search_box.click()
        await asyncio.sleep(random.uniform(0.3, 0.6))
        for char in query:
            await search_box.press(char)
            await asyncio.sleep(random.randint(50, 150) / 1000)

        await asyncio.sleep(random.uniform(0.5, 1.0))
        await search_box.press("Enter")

        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        await asyncio.sleep(random.uniform(1.0, 2.0))

        content = await page.content()
        if "captcha" in content.lower() or "unusual traffic" in content.lower():
            msg = "Google returned a captcha after search submission"
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
            msg = "Google returned no results — possible captcha or layout change"
            print(f"  ⚠️  {msg}")
            return {"success": False, "results": [], "error": msg}

        print(f"  ✓ Found {len(deduped)} result(s)")
        return {"success": True, "results": deduped, "error": None}

    except Exception as e:
        msg = f"Unexpected error during Google search: {str(e)}"
        print(f"  ✗ {msg}")
        return {"success": False, "results": [], "error": msg}