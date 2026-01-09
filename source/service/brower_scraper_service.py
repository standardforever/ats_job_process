import asyncio
import json
from enum import Enum
from typing import Any, Optional
from playwright.async_api import  Page
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from utils.logging import setup_logger

# Configure logging
logger = setup_logger(__name__)


from dataclasses import dataclass, field

@dataclass
class StructuredSection:
    """Represents a section of content with optional key-value pairs."""
    heading: Optional[str] = None
    content: list[str] = field(default_factory=list)
    key_values: dict[str, Any] = field(default_factory=dict)
    subsections: list["StructuredSection"] = field(default_factory=list)


# =============================================================================
# DOM Content Extractor
# =============================================================================


@dataclass
class ExtractedContent:
    structured_text: str
    raw_structure: dict[str, Any]

@dataclass
class ExtractionConfig:
    wait_seconds: float = 2.0
    handle_cookies: bool = True
    handle_popups: bool = True
    cookie_timeout: int = 3000
    popup_timeout: int = 2000
    scroll_to_load: bool = False
    scroll_delay: float = 0.5


class DOMContentExtractor:
    # Add these constants to filter navigation
    # SKIP_CONTAINER_TAGS = frozenset({"nav", "header", "footer", "aside"})
    SKIP_CONTAINER_TAGS = frozenset({"nav", "header", "footer" "aside"})
    COMMON_JOB_LABELS = frozenset({
        "date", "posted", "posted on", "date posted", "publish date",
        "job title", "title", "position", "role",
        "location", "city", "country", "region", "workplace",
        "department", "team", "division", "business unit",
        "employment type", "job type", "type", "contract type", "work type",
        "experience", "experience level", "seniority", "level",
        "salary", "compensation", "pay", "wage", "salary range",
        "company", "employer", "organization", "organisation",
        "job id", "job req. id", "requisition id", "req id", "reference", "job number",
        "closing date", "deadline", "apply by", "expires", "valid until",
        "start date", "availability",
        "industry", "sector", "field",
        "remote", "hybrid", "on-site", "work arrangement",
        "benefits", "perks", "reports to", "manager", "supervisor",
        "travel", "travel required",
    })

    # Cookie consent button selectors (ordered by specificity)
    COOKIE_SELECTORS = [
        # Common accept buttons
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('Accept cookies')",
        "button:has-text('Accept Cookies')",
        "button:has-text('Allow all')",
        "button:has-text('Allow All')",
        "button:has-text('I agree')",
        "button:has-text('I Accept')",
        "button:has-text('Got it')",
        "button:has-text('OK')",
        "button:has-text('Okay')",
        "button:has-text('Continue')",
        "button:has-text('Agree')",
        "button:has-text('Consent')",
        # Reject/necessary only (fallback)
        "button:has-text('Reject all')",
        "button:has-text('Reject All')",
        "button:has-text('Decline')",
        "button:has-text('Only necessary')",
        "button:has-text('Essential only')",
        # ID/class based selectors
        "[id*='accept-cookies']",
        "[id*='cookie-accept']",
        "[id*='gdpr-accept']",
        "[id*='consent-accept']",
        "[class*='cookie-accept']",
        "[class*='accept-cookie']",
        "[data-testid*='cookie-accept']",
        "[data-testid*='accept-cookies']",
        # Common cookie banner libraries
        "#onetrust-accept-btn-handler",
        ".onetrust-accept-btn-handler",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "#cookieconsent-button-accept",
        ".cc-accept",
        ".cc-allow",
        ".cc-dismiss",
        "#accept-cookies",
        "#cookie-consent-accept",
        ".cookie-consent-accept",
        "[aria-label='Accept cookies']",
        "[aria-label='Accept all cookies']",
    ]

    # Popup/modal close selectors
    POPUP_CLOSE_SELECTORS = [
        # Close buttons
        "button:has-text('Close')",
        "button:has-text('×')",
        "button:has-text('X')",
        "button:has-text('No thanks')",
        "button:has-text('No, thanks')",
        "button:has-text('Not now')",
        "button:has-text('Maybe later')",
        "button:has-text('Skip')",
        "button:has-text('Dismiss')",
        # Icon buttons
        "[aria-label='Close']",
        "[aria-label='close']",
        "[aria-label='Dismiss']",
        "[title='Close']",
        "[title='close']",
        # Class/ID based
        ".modal-close",
        ".popup-close",
        ".close-button",
        ".close-btn",
        ".dismiss-button",
        "[class*='close-modal']",
        "[class*='modal-close']",
        "[class*='popup-close']",
        "[class*='newsletter-close']",
        "[data-dismiss='modal']",
        "[data-close]",
        # SVG close icons
        "button svg[class*='close']",
        "button[class*='close'] svg",
    ]

    # Elements to remove before extraction (overlays, banners, etc.)
    OVERLAY_SELECTORS = [
        "[class*='cookie-banner']",
        "[class*='cookie-notice']",
        "[class*='cookie-consent']",
        "[class*='gdpr-banner']",
        "[class*='newsletter-popup']",
        "[class*='newsletter-modal']",
        "[class*='email-popup']",
        "[class*='subscribe-popup']",
        "[class*='overlay-modal']",
        "[id*='cookie-banner']",
        "[id*='cookie-notice']",
        "[id*='newsletter-popup']",
        "#onetrust-consent-sdk",
        "#CybotCookiebotDialog",
        ".modal-backdrop",
        ".overlay-backdrop",
    ]
    BLOCK_TAGS = frozenset({
        "div", "section", "article", "main", "aside",
        "figure", "figcaption", "address", "details", "summary",
    })
    HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
    LIST_CONTAINER_TAGS = frozenset({"ul", "ol"})
    INLINE_TAGS = frozenset({
        "span", "strong", "b", "em", "i", "u", "small", "mark", "code",
    })
    TABLE_SECTION_TAGS = frozenset({"thead", "tbody", "tfoot"})
    TABLE_CELL_TAGS = frozenset({"td", "th"})
    BOLD_TAGS = frozenset({"strong", "b"})
    SKIP_TEXT_PATTERNS = frozenset({"http", "https", "www", "ftp"})

    # EXTRACTION_SCRIPT = """
    #     () => {
    #         const SKIP_TAGS = new Set([
    #             'script', 'style', 'noscript', 'svg', 'path', 'footer','head', 'link', 'nav'
    #         ]);
    #         const INTERACTIVE_TAGS = new Set(['a', 'button']);

    #         function isVisible(element) {
    #             if (!element || element.nodeType !== Node.ELEMENT_NODE) return false;
                
    #             const style = window.getComputedStyle(element);
                
    #             // Check common ways elements are hidden
    #             if (style.display === 'none') return false;
    #             if (style.visibility === 'hidden' || style.visibility === 'collapse') return false;
    #             if (parseFloat(style.opacity) === 0) return false;
                
    #             // Check if element has no dimensions
    #             const rect = element.getBoundingClientRect();
    #             if (rect.width === 0 && rect.height === 0) return false;
                
    #             // Check for clip-path or clip hiding
    #             if (style.clipPath === 'inset(100%)') return false;
    #             if (style.clip === 'rect(0px, 0px, 0px, 0px)') return false;
                
    #             // Check for off-screen positioning (common screen-reader only technique)
    #             if (rect.right < 0 || rect.bottom < 0) return false;
                
    #             return true;
    #         }

    #         function extractAll(element) {
    #             if (!element) return null;

    #             const tagName = element.tagName?.toLowerCase();
    #             if (!tagName || SKIP_TAGS.has(tagName)) return null;

    #             // Skip hidden elements
    #             if (!isVisible(element)) return null;

    #             const node = { tag: tagName };

    #             const href = element.getAttribute('href');
    #             const src = element.getAttribute('src');
    #             const action = element.getAttribute('action');

    #             if (href && !href.startsWith('javascript:')) node.href = href;
    #             if (src && !src.startsWith('data:')) node.src = src;
    #             if (action) node.action = action;

    #             let text = '';
    #             for (const child of element.childNodes) {
    #                 if (child.nodeType === Node.TEXT_NODE) {
    #                     const t = child.textContent.trim();
    #                     if (t) text += (text ? ' ' : '') + t;
    #                 }
    #             }
    #             if (text) node.text = text;

    #             if (INTERACTIVE_TAGS.has(tagName)) {
    #                 const innerText = element.innerText?.trim();
    #                 if (innerText) node.innerText = innerText;
    #             }

    #             const children = [];
    #             for (const child of element.children) {
    #                 const result = extractAll(child);
    #                 if (result) children.push(result);
    #             }
    #             if (children.length > 0) node.children = children;

    #             return node;
    #         }

    #         return extractAll(document.body);
    #     }
    #     """
    # 'script', 'style', 'noscript', 'svg', 'path', 'footer','head', 'link', 'nav'
    EXTRACTION_SCRIPT = """
        () => {
            const SKIP_TAGS = new Set([
                'script', 'style', 'noscript', 'svg', 'path', 'footer','head', 'link', 'nav'
            ]);
            const INTERACTIVE_TAGS = new Set(['a', 'button']);

            function isVisible(element) {
                if (!element || element.nodeType !== Node.ELEMENT_NODE) return false;
                
                const style = window.getComputedStyle(element);
                
                // Only check for explicit hiding
                if (style.display === 'none') return false;
                if (style.visibility === 'hidden') return false;
                
                return true;
            }

            function extractAll(element) {
                if (!element) return null;

                const tagName = element.tagName?.toLowerCase();
                if (!tagName || SKIP_TAGS.has(tagName)) return null;

                // Skip hidden elements
                if (!isVisible(element)) return null;

                const node = { tag: tagName };

                const href = element.getAttribute('href');
                const src = element.getAttribute('src');
                const action = element.getAttribute('action');

                if (href && !href.startsWith('javascript:')) node.href = href;
                if (src && !src.startsWith('data:')) node.src = src;
                if (action) node.action = action;

                // Extract only direct text nodes (prevents duplication)
                let text = '';
                for (const child of element.childNodes) {
                    if (child.nodeType === Node.TEXT_NODE) {
                        const t = child.textContent.trim();
                        if (t) text += (text ? ' ' : '') + t;
                    }
                }
                if (text) node.text = text;

                // Capture useful attributes
                const classNames = element.className;
                const id = element.id;
                if (classNames && typeof classNames === 'string') node.class = classNames;
                if (id) node.id = id;

                if (INTERACTIVE_TAGS.has(tagName)) {
                    const ariaLabel = element.getAttribute('aria-label');
                    if (ariaLabel) node.ariaLabel = ariaLabel;
                }

                const children = [];
                for (const child of element.children) {
                    const result = extractAll(child);
                    if (result) children.push(result);
                }
                if (children.length > 0) node.children = children;

                return node;
            }

            return extractAll(document.body);
        }
        """

    def __init__(self, page: Page, config: Optional[ExtractionConfig] = None):
        self._page = page
        self._config = config or ExtractionConfig()
        logger.debug(
            "DOMContentExtractor initialized",
            extra={
                "handle_cookies": self._config.handle_cookies,
                "handle_popups": self._config.handle_popups,
                "scroll_to_load": self._config.scroll_to_load,
                "wait_seconds": self._config.wait_seconds,
            },
        )
    
    async def _handle_cookie_consent(self) -> bool:
        if not self._config.handle_cookies:
            logger.debug("Cookie handling disabled, skipping")
            return False

        logger.debug("Attempting to handle cookie consent")
        for selector in self.COOKIE_SELECTORS:
            try:
                button = self._page.locator(selector).first
                if await button.is_visible(timeout=500):
                    await button.click(timeout=self._config.cookie_timeout)
                    await asyncio.sleep(0.5)
                    logger.info(
                        "Cookie consent handled successfully",
                        extra={"selector": selector},
                    )
                    return True
            except (PlaywrightTimeoutError, Exception) as e:
                logger.debug(
                    "Cookie selector not found or failed",
                    extra={"selector": selector, "error": str(e)},
                )
                continue

        logger.debug("No cookie consent button found")
        return False

    async def _handle_popups(self) -> int:
        if not self._config.handle_popups:
            logger.debug("Popup handling disabled, skipping")
            return 0

        logger.debug("Attempting to handle popups")
        closed_count = 0

        for selector in self.POPUP_CLOSE_SELECTORS:
            try:
                buttons = self._page.locator(selector)
                count = await buttons.count()

                for i in range(min(count, 3)):  # Limit to 3 per selector
                    try:
                        button = buttons.nth(i)
                        if await button.is_visible(timeout=300):
                            await button.click(timeout=self._config.popup_timeout)
                            closed_count += 1
                            logger.debug(
                                "Popup closed",
                                extra={"selector": selector, "index": i},
                            )
                            await asyncio.sleep(0.3)
                    except Exception as e:
                        logger.debug(
                            "Failed to close popup",
                            extra={"selector": selector, "index": i, "error": str(e)},
                        )
                        continue
            except Exception as e:
                logger.debug(
                    "Popup selector failed",
                    extra={"selector": selector, "error": str(e)},
                )
                continue

        logger.debug(
            "Popup handling completed",
            extra={"closed_count": closed_count},
        )
        return closed_count

    async def _remove_overlays(self) -> int:
        logger.debug("Attempting to remove overlay elements")
        removed_count = 0

        for selector in self.OVERLAY_SELECTORS:
            try:
                count = await self._page.evaluate(
                    f"""
                    () => {{
                        const elements = document.querySelectorAll('{selector}');
                        let count = 0;
                        elements.forEach(el => {{
                            el.remove();
                            count++;
                        }});
                        return count;
                    }}
                    """
                )
                removed_count += count
                if count > 0:
                    logger.debug(
                        "Removed overlay elements",
                        extra={"selector": selector, "count": count},
                    )
            except Exception as e:
                logger.debug(
                    "Failed to remove overlay",
                    extra={"selector": selector, "error": str(e)},
                )
                continue

        logger.debug(
            "Overlay removal completed",
            extra={"total_removed": removed_count},
        )
        return removed_count
    

    async def _scroll_to_load_content(self) -> None:
        if not self._config.scroll_to_load:
            logger.debug("Scroll to load disabled, skipping")
            return

        logger.debug("Starting scroll to load content")
        try:
            # Get page height
            scroll_height = await self._page.evaluate("document.body.scrollHeight")
            viewport_height = await self._page.evaluate("window.innerHeight")
            logger.debug(
                "Initial page dimensions",
                extra={"scroll_height": scroll_height, "viewport_height": viewport_height},
            )

            # Scroll incrementally
            current_position = 0
            scroll_count = 0
            while current_position < scroll_height:
                current_position += viewport_height
                await self._page.evaluate(f"window.scrollTo(0, {current_position})")
                await asyncio.sleep(self._config.scroll_delay)
                scroll_count += 1

                # Check if page height increased (lazy loading)
                new_height = await self._page.evaluate("document.body.scrollHeight")
                if new_height > scroll_height:
                    logger.debug(
                        "Lazy content loaded, page height increased",
                        extra={"old_height": scroll_height, "new_height": new_height},
                    )
                    scroll_height = new_height

            # Scroll back to top
            await self._page.evaluate("window.scrollTo(0, 0)")
            logger.debug(
                "Scroll to load completed",
                extra={"scroll_count": scroll_count, "final_height": scroll_height},
            )
        except Exception as e:
            logger.warning(
                "Scroll to load failed",
                extra={"error": str(e)},
            )
            pass
    
    async def _wait_for_page_ready(self) -> None:
        logger.debug("Waiting for page to be ready")
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=10000)
            logger.debug("DOM content loaded")
        except PlaywrightTimeoutError:
            logger.warning("Timeout waiting for DOM content loaded")
            pass

        try:
            await self._page.wait_for_load_state("networkidle", timeout=5000)
            logger.debug("Network idle reached")
        except PlaywrightTimeoutError:
            logger.warning("Timeout waiting for network idle")
            pass

    async def extract(
        self,
        wait_seconds: Optional[float] = None,
        handle_cookies: Optional[bool] = None,
        handle_popups: Optional[bool] = None,
    ) -> ExtractedContent:
        logger.info(
            "Starting content extraction",
            extra={
                "wait_seconds": wait_seconds,
                "handle_cookies": handle_cookies,
                "handle_popups": handle_popups,
            },
        )
        wait_seconds = wait_seconds or self._config.wait_seconds
        should_handle_cookies = handle_cookies if handle_cookies is not None else self._config.handle_cookies
        should_handle_popups = handle_popups if handle_popups is not None else self._config.handle_popups

        # Wait for page to be ready
        await self._wait_for_page_ready()

        # Handle cookie consent
        if should_handle_cookies:
            cookie_handled = await self._handle_cookie_consent()
            if cookie_handled:
                await asyncio.sleep(0.5)

        # Handle popups
        if should_handle_popups:
            popups_closed = await self._handle_popups()
            logger.debug(
                "Popups handling result",
                extra={"popups_closed": popups_closed},
            )

        # Remove overlay elements
        overlays_removed = await self._remove_overlays()
        logger.debug(
            "Overlays removal result",
            extra={"overlays_removed": overlays_removed},
        )

        # Scroll to load lazy content if enabled
        await self._scroll_to_load_content()

        # Final wait
        logger.debug(
            "Final wait before extraction",
            extra={"wait_seconds": wait_seconds},
        )
        await asyncio.sleep(wait_seconds)

        # Extract content
        try:
            logger.debug("Executing extraction script")
            raw_content = await self._page.evaluate(self.EXTRACTION_SCRIPT)

            if isinstance(raw_content, str):
                raw_content = json.loads(raw_content)

            structured_text = self._structure_to_text(raw_content or {})

            logger.info(
                "Content extraction completed successfully",
                extra={
                    "structured_text_length": len(structured_text),
                    "has_raw_structure": bool(raw_content),
                },
            )

            return ExtractedContent(
                structured_text=structured_text,
                raw_structure=raw_content or {},
            )
        except Exception as e:
            logger.error(
                "Content extraction failed",
                extra={"error": str(e)},
                exc_info=True,
            )
            return ExtractedContent(
                structured_text="",
                raw_structure={"error": str(e)},
            )

    def _structure_to_text(self, node: dict[str, Any], depth: int = 0) -> str:
        if not node or not isinstance(node, dict):
            return ""

        tag = node.get("tag", "")
        text = node.get("text", "").strip()
        inner_text = node.get("innerText", "").strip()
        href = node.get("href", "")
        src = node.get("src", "")
        action = node.get("action", "")
        children = node.get("children", [])

        def process_children() -> str:
            child_texts = [
                self._structure_to_text(child, depth + 1)
                for child in children
            ]
            return " ".join(t for t in child_texts if t.strip())

        # Helper to combine multiple text sources
        def combine_all_text(*text_sources) -> str:
            parts = [t.strip() for t in text_sources if t and t.strip()]
            return " ".join(parts)

        if tag in self.HEADING_TAGS:
            level = int(tag[1])
            # Combine inner_text, direct text, AND children
            content = combine_all_text(inner_text, text, process_children())
            if content:
                return f"\n\n{'#' * level} {content}\n"
            return ""

        if tag == "a":
            # Combine all text sources for links
            link_text = combine_all_text(inner_text, text, process_children()) or "link"
            return f"[{link_text}]({href})" if href else link_text

        if tag == "button":
            # Combine all text sources for buttons
            btn_text = combine_all_text(inner_text, text, process_children()) or "button"
            return f"[BUTTON: {btn_text}]"

        if tag == "img":
            alt = text or "image"
            return f"[IMAGE: {alt}]({src})" if src else f"[IMAGE: {alt}]"

        if tag == "form":
            form_header = f"[FORM action={action}]" if action else "[FORM]"
            form_content = process_children()
            if form_content.strip():
                return f"\n{form_header}\n{form_content.strip()}\n[/FORM]\n"
            return ""

        if tag == "input":
            return "[INPUT]"

        if tag == "textarea":
            return "[TEXTAREA]"

        if tag == "select":
            child_content = process_children()
            return f"[SELECT: {child_content}]" if child_content else "[SELECT]"

        if tag == "option":
            # Combine text, inner_text, AND children
            return combine_all_text(text, inner_text, process_children())

        if tag in self.LIST_CONTAINER_TAGS:
            list_items = [
                self._structure_to_text(child, depth + 1)
                for child in children
            ]
            filtered_items = [item for item in list_items if item.strip()]
            return "\n" + "\n".join(filtered_items) + "\n" if filtered_items else ""

        if tag == "li":
            content = self._combine_text_and_children(text, process_children())
            return f"  • {content}" if content else ""

        if tag == "p":
            content = self._combine_text_and_children(text, process_children())
            return f"\n{content}\n" if content else ""

        if tag == "br":
            return "\n"

        if tag == "hr":
            return "\n---\n"

        if tag == "table":
            table_content = self._process_table(node)
            return f"\n[TABLE]\n{table_content}[/TABLE]\n" if table_content else ""

        if tag in self.TABLE_SECTION_TAGS:
            return process_children()

        if tag == "tr":
            cells = [
                self._structure_to_text(child, depth + 1).strip()
                for child in children
            ]
            filtered_cells = [c for c in cells if c is not None]
            return "| " + " | ".join(filtered_cells) + " |" if filtered_cells else ""

        if tag in self.TABLE_CELL_TAGS:
            return self._combine_text_and_children(text, process_children())

        if tag == "pre":
            # Combine text AND children
            content = self._combine_text_and_children(text, process_children())
            return f"\n```\n{content}\n```\n" if content else ""

        if tag == "code":
            # Combine all text sources
            content = combine_all_text(text, inner_text, process_children())
            return f"`{content}`" if content else ""

        if tag == "blockquote":
            # Combine text AND children
            content = self._combine_text_and_children(text, process_children())
            if content:
                quoted = "\n".join(f"> {line}" for line in content.split("\n"))
                return f"\n{quoted}\n"
            return ""

        if tag in self.INLINE_TAGS:
            # Combine text AND children (THIS WAS THE MAIN BUG)
            return self._combine_text_and_children(text, process_children())

        if tag in self.BLOCK_TAGS or tag == "body":
            content = self._combine_text_and_children(text, process_children())
            if content:
                return f"\n{content}\n" if tag in self.BLOCK_TAGS else content
            return ""

        return self._combine_text_and_children(text, process_children())


    def _combine_text_and_children(self, text: str, child_content: str) -> str:
        parts = []
        if text:
            parts.append(text)
        if child_content.strip():
            parts.append(child_content.strip())
        return " ".join(parts).strip()

    def _process_table(self, table_node: dict[str, Any]) -> str:
        logger.debug("Processing table node")
        rows: list[dict[str, Any]] = []

        def find_rows(node: dict[str, Any]) -> None:
            if node.get("tag") == "tr":
                rows.append(node)
            for child in node.get("children", []):
                find_rows(child)

        find_rows(table_node)

        if not rows:
            logger.debug("No rows found in table")
            return ""

        logger.debug(
            "Table rows found",
            extra={"row_count": len(rows)},
        )

        result_lines = []
        for i, row in enumerate(rows):
            cells = []
            for child in row.get("children", []):
                if child.get("tag") in self.TABLE_CELL_TAGS:
                    cell_text = child.get("text", "") or child.get("innerText", "")
                    if not cell_text and child.get("children"):
                        nested_parts = [
                            self._structure_to_text(nested, 0).strip()
                            for nested in child.get("children", [])
                        ]
                        cell_text = " ".join(p for p in nested_parts if p)
                    cells.append(cell_text.strip() if cell_text else "")

            if cells:
                result_lines.append("| " + " | ".join(cells) + " |")
                if i == 0:
                    result_lines.append("|" + "|".join(["---"] * len(cells)) + "|")

        logger.debug(
            "Table processing completed",
            extra={"result_lines": len(result_lines)},
        )
        return "\n".join(result_lines) + "\n"




