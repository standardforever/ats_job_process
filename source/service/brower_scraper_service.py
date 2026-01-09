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
class SectionedContent:
    """Page content sectioned by headings"""
    sections: dict[str, str]
    metadata: dict[str, Any]  # For any intro content before first heading
    raw_structure: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections": self.sections,
            "metadata": self.metadata,
            "raw_structure": self.raw_structure,
        }


@dataclass
class JobPageContent:
    """Structured job page content as flat dictionary"""
    data: dict[str, Any]
    raw_structure: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.data
    



class TagCategory(Enum):
    BLOCK = "block"
    HEADING = "heading"
    LIST_CONTAINER = "list_container"
    INLINE = "inline"
    TABLE = "table"


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

    COMMON_SECTION_HEADINGS = frozenset({
        "job summary", "summary", "overview", "about the role", "about this role",
        "description", "job description", "role description", "position description",
        "responsibilities", "duties", "key responsibilities", "what you'll do",
        "principal duties", "principle duties", "duties and responsibilities",
        "requirements", "qualifications", "what we're looking for", "what you'll need",
        "required qualifications", "minimum qualifications", "must have",
        "preferred qualifications", "nice to have", "preferred", "bonus points",
        "skills", "required skills", "technical skills", "competencies",
        "experience", "required experience", "professional requirements",
        "education", "educational requirements", "education requirements",
        "benefits", "what we offer", "perks", "compensation and benefits",
        "about us", "about the company", "company overview", "who we are",
        "how to apply", "application process", "next steps",
        "equal opportunity", "eeo", "diversity",
        "closing date", "application deadline",
        "additional information", "other information", "notes",
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


    def extract_structured_data(self, node: dict[str, Any]) -> dict[str, Any]:
        """
        Extract page content into structured dictionary.
        Returns both key-value pairs and sectioned content.
        """
        result: dict[str, Any] = {}
        extracted_texts: set[str] = set()  # Track what's been extracted
        
        # Phase 1: Extract explicit key-value patterns
        self._extract_definition_lists(node, result, extracted_texts)
        self._extract_table_pairs(node, result, extracted_texts)
        self._extract_inline_label_values(node, result, extracted_texts)
        
        # Phase 2: Extract sectioned content (headings + bold pseudo-headings)
        sections = self._extract_all_sections(node, extracted_texts)
        
        # Phase 3: Merge sections into result
        for section in sections:
            self._merge_section_to_result(section, result, extracted_texts)
        
        # Phase 4: Capture any remaining content not yet structured
        self._extract_remaining_content(node, result, extracted_texts)
        
        return self._cleanup_structured_result(result)

    # =========================================================================
    # Phase 1: Explicit Key-Value Extraction
    # =========================================================================

    def _extract_definition_lists(self, node: dict[str, Any], result: dict[str, Any], extracted_texts: set[str]) -> None:
        """Extract from <dl><dt>Label</dt><dd>Value</dd></dl> patterns."""
        if not node or not isinstance(node, dict):
            return
        
        tag = node.get("tag", "")
        
        if tag == "dl":
            children = node.get("children", [])
            i = 0
            while i < len(children):
                child = children[i]
                if child.get("tag") == "dt":
                    label = self._get_node_text(child)
                    # Look for dd (might not be immediately after)
                    j = i + 1
                    while j < len(children) and children[j].get("tag") not in ("dt", "dd"):
                        j += 1
                    if j < len(children) and children[j].get("tag") == "dd":
                        value = self._get_text_or_list(children[j])
                        if label and value:
                            self._add_to_result(result, label, value)
                            # Track extracted content
                            extracted_texts.add(label.lower().strip())
                            if isinstance(value, str):
                                extracted_texts.add(value.lower().strip())
                            elif isinstance(value, list):
                                for v in value:
                                    extracted_texts.add(v.lower().strip())
                        i = j + 1
                        continue
                i += 1
            return
        
        for child in node.get("children", []):
            self._extract_definition_lists(child, result, extracted_texts)

    def _extract_table_pairs(self, node: dict[str, Any], result: dict[str, Any], extracted_texts: set[str]) -> None:
        """Extract from table rows with label-value pattern."""
        if not node or not isinstance(node, dict):
            return
        
        tag = node.get("tag", "")
        
        if tag == "tr":
            children = node.get("children", [])
            cells = [c for c in children if c.get("tag") in self.TABLE_CELL_TAGS]
            
            if len(cells) == 2:
                label = self._get_node_text(cells[0])
                value = self._get_text_or_list(cells[1])
                if label and value and self._is_likely_label(label):
                    self._add_to_result(result, label, value)
                    # Track extracted content
                    extracted_texts.add(label.lower().strip())
                    if isinstance(value, str):
                        extracted_texts.add(value.lower().strip())
                    elif isinstance(value, list):
                        for v in value:
                            extracted_texts.add(v.lower().strip())
                    return
        
        for child in node.get("children", []):
            self._extract_table_pairs(child, result, extracted_texts)

            

   


    def _is_key_value_text(self, text: str) -> bool:
        """
        Determine if text is a key-value pair vs a regular sentence or URL.
        """
        if not text or ":" not in text:
            return False
        
        text_lower = text.lower().strip()
        
        # Skip URLs
        if text_lower.startswith(("http:", "https:", "ftp:", "//")):
            return False
        
        # Skip time patterns like "12:00pm", "8:30"
        import re
        if re.match(r'^\d{1,2}:\d{2}', text):
            return False
        
        parts = text.split(":", 1)
        label = parts[0].strip()
        value = parts[1].strip() if len(parts) > 1 else ""
        
        # Label should be short (< 40 chars)
        if len(label) > 40:
            return False
        
        # Label shouldn't be just numbers (like "12" from "12:00pm")
        if label.isdigit():
            return False
        
        # Label shouldn't start with URL-like patterns
        if label.lower() in self.SKIP_TEXT_PATTERNS:
            return False
        
        # Label shouldn't contain sentence-ending punctuation
        if any(p in label for p in ".!?"):
            return False
        
        # Label shouldn't have too many words
        label_words = label.split()
        if len(label_words) > 5:
            return False
        
        # If label is a known field, it's likely key-value
        if self._is_likely_label(label):
            return True
        
        # If value is very long, probably a sentence
        if len(value) > 100:
            return False
        
        # If value starts with sentence patterns
        value_lower = value.lower()
        sentence_starters = ("it ", "this ", "that ", "these ", "those ", "there ", 
                            "here ", "he ", "she ", "they ", "we ", "i ", "you ")
        if any(value_lower.startswith(s) for s in sentence_starters):
            return False
        
        # Short total text with reasonable label
        if len(text) < 60 and len(label_words) <= 3:
            return True
        
        return False


    # def _is_likely_label(self, text: str) -> bool:
    #     """Check if text looks like a field label, not a sentence or number."""
    #     if not text:
    #         return False
        
    #     text_clean = text.strip()
    #     text_lower = text_clean.lower()
        
    #     # Reject pure numbers (like statistics "865,534")
    #     if text_clean.replace(",", "").replace(".", "").isdigit():
    #         return False
        
    #     # Reject URLs
    #     if text_lower.startswith(("http", "https", "www", "//", "ftp")):
    #         return False
        
    #     # Check against known labels first
    #     for label in self.COMMON_JOB_LABELS:
    #         if label in text_lower or text_lower in label:
    #             return True
        
    #     # Reject sentence-like patterns
    #     sentence_starters = (
    #         "this ", "that ", "these ", "those ", "there ", "here ",
    #         "it ", "he ", "she ", "they ", "we ", "i ", "you ",
    #         "the ", "a ", "an ", "my ", "your ", "his ", "her ", "our ", "their ",
    #         "if ", "when ", "while ", "after ", "before ", "because ", "since ",
    #         "what ", "how ", "why ", "where ", "who ", "which ",
    #     )
    #     if any(text_lower.startswith(s) for s in sentence_starters):
    #         return False
        
    #     # Reject if contains common verbs
    #     sentence_verbs = (" is ", " are ", " was ", " were ", " has ", " have ", 
    #                     " had ", " will ", " would ", " should ", " could ", " can ")
    #     if any(v in text_lower for v in sentence_verbs):
    #         return False
        
    #     # Short text with few words is more likely to be a label
    #     words = text_clean.split()
    #     if len(text_clean) < 40 and len(words) <= 4:
    #         return True
        
    #     return False


    def _should_skip_container(self, node: dict[str, Any]) -> bool:
        """Check if this container should be skipped entirely (nav, header, footer, etc.)"""
        if not node or not isinstance(node, dict):
            return False
        
        tag = node.get("tag", "")
        
        # Skip navigation containers
        if tag in self.SKIP_CONTAINER_TAGS:
            return True
        
        return False


    def _extract_inline_label_values(
        self, 
        node: dict[str, Any], 
        result: dict[str, Any],
        extracted_texts: set[str],
        parent_children: Optional[list] = None,
        index: int = 0
    ) -> None:
        """
        Extract key-value patterns from various HTML structures.
        """
        if not node or not isinstance(node, dict):
            return
        
        tag = node.get("tag", "")
        text = node.get("text", "").strip()
        children = node.get("children", [])
        
        # Skip navigation/header/footer containers entirely
        if self._should_skip_container(node):
            return
        
        # Pattern 1: "Label: Value" in same text node
        if text and ":" in text and not children:
            if self._is_key_value_text(text):
                parts = text.split(":", 1)
                label, value = parts[0].strip(), parts[1].strip()
                if label and value and self._is_likely_label(label):
                    self._add_to_result(result, label, value)
                    extracted_texts.add(label.lower().strip())
                    extracted_texts.add(value.lower().strip())
                    extracted_texts.add(text.lower().strip())
                    return
        
        # Pattern 2: <strong>Label:</strong> followed by value in same container
        if tag in self.BLOCK_TAGS or tag == "p":
            kv = self._extract_bold_label_value_in_block(node)
            if kv:
                self._add_to_result(result, kv[0], kv[1])
                extracted_texts.add(kv[0].lower().strip())
                extracted_texts.add(kv[1].lower().strip())
                full_text = self._get_node_text(node)
                if full_text:
                    extracted_texts.add(full_text.lower().strip())
                return
        
        # Pattern 3: <span>Label:</span><span>Value</span> as siblings
        if tag == "span" and text.endswith(":") and parent_children:
            if not self._is_fragmented_text_container(parent_children):
                label = text.rstrip(":").strip()
                
                # Skip if label doesn't look valid
                if not self._is_likely_label(label):
                    pass
                elif index + 1 < len(parent_children):
                    next_sibling = parent_children[index + 1]
                    next_text = self._get_node_text(next_sibling)
                    
                    # Skip if value is too short (fragment) or too long (paragraph)
                    if len(next_text.strip()) < 2 or len(next_text.strip()) > 200:
                        pass
                    elif next_sibling.get("tag") in self.INLINE_TAGS or not next_sibling.get("tag"):
                        self._add_to_result(result, label, next_text)
                        extracted_texts.add(label.lower().strip())
                        extracted_texts.add(next_text.lower().strip())
                        return
        
        # Pattern 4: Alternating <div>Label</div><div>Value</div> siblings
        # This handles job metadata tables common in job sites
        if tag == "div" and parent_children:
            self._extract_alternating_div_pairs(parent_children, result, extracted_texts)
        
        # Recurse into children
        for i, child in enumerate(children):
            self._extract_inline_label_values(child, result, extracted_texts, children, i)


    def _extract_alternating_div_pairs(
        self, 
        children: list[dict], 
        result: dict[str, Any],
        extracted_texts: set[str]
    ) -> None:
        """
        Extract key-value pairs from alternating div siblings:
        <div>Label</div><div>Value</div><div>Label2</div><div>Value2</div>
        """
        i = 0
        while i < len(children) - 1:
            current = children[i]
            next_item = children[i + 1]
            
            # Both must be divs
            if current.get("tag") != "div" or next_item.get("tag") != "div":
                i += 1
                continue
            
            current_text = current.get("text", "").strip()
            next_text = next_item.get("text", "").strip()
            
            # Current should have text that looks like a label
            # Next should have text that looks like a value (not a label)
            if (current_text 
                and next_text 
                and self._is_likely_label(current_text)
                and not self._is_likely_label(next_text)
                and current_text.lower() not in extracted_texts):
                
                # Check current div has minimal children (often just an icon span)
                current_children = current.get("children", [])
                has_only_empty_children = all(
                    not child.get("text", "").strip() 
                    for child in current_children
                )
                
                if len(current_children) <= 1 or has_only_empty_children:
                    self._add_to_result(result, current_text, next_text)
                    extracted_texts.add(current_text.lower().strip())
                    extracted_texts.add(next_text.lower().strip())
                    i += 2  # Skip both divs
                    continue
            
            i += 1







    def _extract_bold_label_value_in_block(self, node: dict[str, Any]) -> Optional[tuple[str, str]]:
        """
        Extract key-value from patterns like:
        <p><strong>Label:</strong> Value text here</p>
        <div><b>Label:</b> Value</div>
        
        Returns (label, value) tuple or None.
        """
        children = node.get("children", [])
        text = node.get("text", "").strip()
        
        if not children:
            return None
        
        first_child = children[0]
        if first_child.get("tag") not in self.BOLD_TAGS:
            return None
        
        bold_text = self._get_node_text(first_child).strip()
        
        # Must end with colon to be a label
        if not bold_text.endswith(":"):
            return None
        
        label = bold_text.rstrip(":").strip()
        
        if not self._is_likely_label(label):
            return None
        
        # Gather value from remaining content
        value_parts = []
        
        # Add any text directly in the parent after the bold
        if text:
            value_parts.append(text)
        
        # Add text from remaining children
        for child in children[1:]:
            child_text = self._get_node_text(child)
            if child_text:
                value_parts.append(child_text)
        
        value = " ".join(value_parts).strip()
        
        if value:
            return (label, value)
        
        return None

    # =========================================================================
    # Phase 2: Section Extraction (Headings and Bold Pseudo-Headings)
    # =========================================================================

    def _extract_all_sections(self, node: dict[str, Any], extracted_texts: set[str]) -> list[StructuredSection]:
        """
        Extract content organized by headings and bold pseudo-headings.
        Returns a list of sections, each potentially containing subsections.
        """
        # Flatten the DOM into a sequence of significant elements
        elements = self._flatten_to_elements(node, extracted_texts)
        
        # Build sections from the flattened elements
        sections = self._build_sections_from_elements(elements, extracted_texts)
        
        return sections

    def _flatten_to_elements(self, node: dict[str, Any], extracted_texts: set[str], depth: int = 0) -> list[dict]:
        """
        Flatten DOM into a list of significant elements:
        - headings (h1-h6)
        - bold_block (strong/b that acts as a header)
        - paragraph
        - list
        - line_break
        
        Skips content that was already extracted as key-values.
        """
        elements = []
        
        if not node or not isinstance(node, dict):
            return elements
        
        tag = node.get("tag", "")
        children = node.get("children", [])
        text = node.get("text", "").strip()
        
        # Actual headings
        if tag in self.HEADING_TAGS:
            heading_text = self._get_node_text(node)
            if heading_text:
                level = int(tag[1])
                elements.append({
                    "_type": "heading",
                    "level": level,
                    "text": heading_text,
                })
            return elements
        
        # Line breaks indicate content separation
        if tag == "br":
            elements.append({"_type": "line_break"})
            return elements
        
        # Lists
        if tag in self.LIST_CONTAINER_TAGS:
            items = self._extract_list_items(node)
            # Filter out items that were already extracted
            items = [item for item in items if item.lower().strip() not in extracted_texts]
            if items:
                elements.append({
                    "_type": "list",
                    "items": items,
                })
            return elements
        
        # Paragraphs - check for bold pseudo-heading pattern
        if tag == "p":
            # Check if this paragraph's content was already extracted
            full_text = self._get_node_text(node)
            if full_text and full_text.lower().strip() in extracted_texts:
                return elements  # Skip, already extracted
            
            elem = self._analyze_paragraph(node)
            if elem:
                # If it's a key_value_extracted marker, mark it but don't add content
                if elem.get("_type") == "key_value_extracted":
                    elements.append(elem)
                # Skip paragraphs whose content was already extracted
                elif elem.get("text", "").lower().strip() not in extracted_texts:
                    elements.append(elem)
            return elements
        
        # Block-level elements - check for bold at start
        if tag in self.BLOCK_TAGS:
            block_elements = self._analyze_block(node, extracted_texts)
            elements.extend(block_elements)
            return elements
        
        # Standalone bold/strong that could be a header
        if tag in self.BOLD_TAGS:
            bold_text = self._get_node_text(node)
            if bold_text and self._is_standalone_bold_header(node, bold_text):
                # Don't add as header if it's already been extracted as a key label
                if bold_text.lower().strip() not in extracted_texts:
                    elements.append({
                        "_type": "bold_header",
                        "text": bold_text,
                    })
            return elements
        
        # Recurse into children
        for child in children:
            elements.extend(self._flatten_to_elements(child, extracted_texts, depth + 1))
        
        # Handle any direct text content
        if text and tag not in self.HEADING_TAGS:
            if text.lower().strip() not in extracted_texts:
                elements.append({
                    "_type": "text",
                    "text": text,
                })
        
        return elements

    def _analyze_paragraph(self, node: dict[str, Any]) -> Optional[dict]:
        """
        Analyze a paragraph to determine its type:
        - bold_header: <p><strong>Header Text</strong></p>
        - key_value: <p><strong>Label:</strong> value</p> (marked to skip, handled in phase 1)
        - paragraph: regular paragraph text
        """
        children = node.get("children", [])
        text = node.get("text", "").strip()
        
        # Check if paragraph starts with bold
        if children and children[0].get("tag") in self.BOLD_TAGS:
            bold_child = children[0]
            bold_text = self._get_node_text(bold_child).strip()
            
            # Get remaining content after the bold
            remaining_parts = []
            if text:
                remaining_parts.append(text)
            for child in children[1:]:
                child_text = self._get_node_text(child)
                if child_text:
                    remaining_parts.append(child_text)
            remaining = " ".join(remaining_parts).strip()
            
            # Case 1: Bold with colon = key-value (handled in phase 1)
            # Mark as extracted so we don't duplicate
            if bold_text.endswith(":"):
                return {
                    "_type": "key_value_extracted",
                    "label": bold_text.rstrip(":").strip(),
                }
            
            # Case 2: Bold only, no remaining text = potential header
            elif not remaining or len(remaining) < 10:
                if self._looks_like_section_heading(bold_text):
                    return {
                        "_type": "bold_header",
                        "text": bold_text,
                    }
                # Short remaining text but bold isn't a heading - treat as paragraph
                elif remaining:
                    return {
                        "_type": "paragraph",
                        "text": f"{bold_text} {remaining}".strip(),
                    }
            
            # Case 3: Bold followed by significant text = paragraph with inline emphasis
            # This is NOT a header, just emphasis within flowing text
            else:
                full_text = f"{bold_text} {remaining}".strip()
                return {
                    "_type": "paragraph",
                    "text": full_text,
                    "has_inline_emphasis": True,  # Mark that bold is inline, not structural
                }
        
        # Regular paragraph
        full_text = self._get_node_text(node)
        if full_text:
            return {
                "_type": "paragraph",
                "text": full_text,
            }
        
        return None

    def _analyze_block(self, node: dict[str, Any], extracted_texts: set[str]) -> list[dict]:
        """
        Analyze a block element (div, section, etc.) for structure.
        Content found in this block stays within this block's scope.
        """
        elements = []
        children = node.get("children", [])
        text = node.get("text", "").strip()
        
        # Check if block starts with bold as a header
        if children and children[0].get("tag") in self.BOLD_TAGS:
            bold_child = children[0]
            bold_text = self._get_node_text(bold_child).strip()
            
            # Check if this is a standalone bold header (not inline emphasis)
            has_break_after = False
            remaining_starts_new_block = False
            
            if len(children) > 1:
                second_child = children[1]
                second_tag = second_child.get("tag", "")
                
                # Line break after bold indicates it's a header
                if second_tag == "br":
                    has_break_after = True
                # Block element after bold indicates it's a header
                elif second_tag in self.BLOCK_TAGS or second_tag in self.LIST_CONTAINER_TAGS:
                    remaining_starts_new_block = True
            
            # Bold with colon at block start = key-value line
            if bold_text.endswith(":") and not has_break_after:
                # Handled in phase 1, mark as extracted
                elements.append({
                    "_type": "key_value_extracted",
                    "label": bold_text.rstrip(":").strip(),
                })
                return elements
            
            # Bold followed by break or block = header for this container only
            elif (has_break_after or remaining_starts_new_block or len(children) == 1) and self._looks_like_section_heading(bold_text):
                # Don't add if already extracted
                if bold_text.lower().strip() not in extracted_texts:
                    elements.append({
                        "_type": "bold_header",
                        "text": bold_text,
                        "scoped": True,  # Mark as scoped to this container
                    })
                
                # Process remaining children within this container
                start_idx = 2 if has_break_after else 1
                for child in children[start_idx:]:
                    elements.extend(self._flatten_to_elements(child, extracted_texts))
                
                # Mark end of scoped section
                elements.append({"_type": "scope_end"})
                
                return elements
        
        # No special pattern, recurse normally
        for child in children:
            elements.extend(self._flatten_to_elements(child, extracted_texts))
        
        if text and text.lower().strip() not in extracted_texts:
            elements.append({"_type": "text", "text": text})
        
        return elements

    def _is_standalone_bold_header(self, node: dict[str, Any], text: str) -> bool:
        """
        Determine if a bold element is a standalone header vs inline emphasis.
        """
        # Must look like a section heading
        if not self._looks_like_section_heading(text):
            return False
        
        # Shouldn't end with colon (that's a label)
        if text.endswith(":"):
            return False
        
        # Shouldn't be too long
        if len(text) > 100:
            return False
        
        return True

    def _build_sections_from_elements(self, elements: list[dict], extracted_texts: set[str]) -> list[StructuredSection]:
        """
        Build structured sections from flattened elements.
        Respects container scoping for bold headers.
        """
        sections = []
        current_section: Optional[StructuredSection] = None
        in_scoped_section = False
        
        for elem in elements:
            elem_type = elem.get("_type")
            
            # Track key-values that were already extracted in phase 1
            if elem_type == "key_value_extracted":
                continue
            
            # End of scoped section - close current section if it was scoped
            if elem_type == "scope_end":
                if current_section and in_scoped_section:
                    sections.append(current_section)
                    current_section = None
                    in_scoped_section = False
                continue
            
            # Real headings (h1-h6) always start new sections and capture subsequent content
            if elem_type == "heading":
                # Save current section
                if current_section:
                    sections.append(current_section)
                
                # Start new section (not scoped - captures until next heading)
                current_section = StructuredSection(
                    heading=elem.get("text", "")
                )
                in_scoped_section = False
            
            # Bold headers - may be scoped to their container
            elif elem_type == "bold_header":
                # Save current section
                if current_section:
                    sections.append(current_section)
                
                # Start new section
                current_section = StructuredSection(
                    heading=elem.get("text", "")
                )
                # Check if this is a scoped section (only captures content in same container)
                in_scoped_section = elem.get("scoped", False)
            
            elif elem_type == "list":
                items = elem.get("items", [])
                # Filter out already extracted items
                items = [item for item in items if item.lower().strip() not in extracted_texts]
                if items:
                    if current_section:
                        current_section.content.extend(items)
                    else:
                        # List without header - create anonymous section
                        current_section = StructuredSection()
                        current_section.content.extend(items)
            
            elif elem_type in ("paragraph", "text"):
                text = elem.get("text", "")
                if text and text.lower().strip() not in extracted_texts:
                    # If we're in a scoped section, content goes there
                    # If not, content goes to current section or starts new anonymous section
                    if current_section:
                        current_section.content.append(text)
                    else:
                        current_section = StructuredSection()
                        current_section.content.append(text)
            
            elif elem_type == "line_break":
                # Line breaks are just separators, don't affect structure
                pass
        
        # Don't forget the last section
        if current_section:
            sections.append(current_section)
        
        return sections

    def _merge_section_to_result(self, section: StructuredSection, result: dict[str, Any], extracted_texts: set[str]) -> None:
        """
        Merge a section into the result dictionary.
        """
        if not section.heading:
            # Anonymous section - add content to a general key
            if section.content:
                if "_content" not in result:
                    result["_content"] = []
                for content in section.content:
                    if content.lower().strip() not in extracted_texts:
                        result["_content"].append(content)
                        extracted_texts.add(content.lower().strip())
            return
        
        heading = section.heading.rstrip(":").strip()
        
        # Skip if already exists with same or more content
        if heading in result:
            existing = result[heading]
            new_content = section.content
            if isinstance(existing, list) and isinstance(new_content, list):
                if len(existing) >= len(new_content):
                    return
            elif isinstance(existing, str) and isinstance(new_content, list):
                if len(existing) >= len(" ".join(new_content)):
                    return
        
        # Filter content that was already extracted
        filtered_content = [c for c in section.content if c.lower().strip() not in extracted_texts]
        
        if not filtered_content:
            return
        
        # Add section content
        if len(filtered_content) == 1:
            result[heading] = filtered_content[0]
            extracted_texts.add(filtered_content[0].lower().strip())
        elif filtered_content:
            result[heading] = filtered_content
            for c in filtered_content:
                extracted_texts.add(c.lower().strip())
        
        # Track the heading
        extracted_texts.add(heading.lower().strip())
        
        # Merge key-values
        for k, v in section.key_values.items():
            self._add_to_result(result, k, v)

    # =========================================================================
    # Phase 4: Remaining Content Extraction
    # =========================================================================

    def _extract_remaining_content(self, node: dict[str, Any], result: dict[str, Any], extracted_texts: set[str]) -> None:
        """
        Capture any content not already in result.
        Uses word-level overlap detection to avoid duplicates.
        """
        all_text_blocks = self._collect_all_text_blocks(node)
        
        # Find uncaptured content
        uncaptured = []
        for block in all_text_blocks:
            block_clean = block.strip()
            block_lower = block_clean.lower()
            if not block_clean:
                continue
            
            # Skip if exact match
            if block_lower in extracted_texts:
                continue
            
            # Check for significant word overlap
            block_words = set(block_lower.split())
            is_captured = False
            
            for cap in extracted_texts:
                # Skip very short captured texts for comparison
                if len(cap) < 10:
                    continue
                
                cap_words = set(cap.split())
                
                # Check for substring match
                if block_lower in cap or cap in block_lower:
                    is_captured = True
                    break
                
                # Check for significant word overlap (>70% of words match)
                if block_words and cap_words:
                    common_words = block_words & cap_words
                    overlap_ratio = len(common_words) / min(len(block_words), len(cap_words))
                    if overlap_ratio > 0.7:
                        is_captured = True
                        break
            
            if not is_captured:
                uncaptured.append(block_clean)
                extracted_texts.add(block_lower)
        
        if uncaptured:
            if "_additional_content" not in result:
                result["_additional_content"] = []
            result["_additional_content"].extend(uncaptured)

    def _collect_all_text_blocks(self, node: dict[str, Any]) -> list[str]:
        """
        Collect all text blocks from the DOM.
        """
        blocks = []
        
        if not node or not isinstance(node, dict):
            return blocks
        
        tag = node.get("tag", "")
        
        # Skip certain tags
        if tag in self.SKIP_CONTAINER_TAGS:
            return blocks
        
        # For paragraphs and list items, get full text
        if tag in ("p", "li"):
            text = self._get_node_text(node)
            if text and len(text) > 10:  # Skip very short fragments
                blocks.append(text)
            return blocks
        
        # Recurse
        for child in node.get("children", []):
            blocks.extend(self._collect_all_text_blocks(child))
        
        return blocks

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _get_node_text(self, node: dict[str, Any]) -> str:
        """Get all text from node, combining children then any direct text."""
        if not node or not isinstance(node, dict):
            return ""
        
        # Prefer innerText if available (it's already in correct order)
        if inner := node.get("innerText", "").strip():
            return inner
        
        parts = []
        
        # First, get text from children (they appear first in DOM order typically)
        for child in node.get("children", []):
            child_text = self._get_node_text(child)
            if child_text:
                parts.append(child_text)
        
        # Then add any direct text (appears after children in DOM structure)
        # Note: This is a simplification; real DOM might interleave text nodes
        if text := node.get("text", "").strip():
            parts.append(text)
        
        return " ".join(parts).strip()

    def _get_text_or_list(self, node: dict[str, Any]) -> Any:
        """Get text or extract as list if node contains ul/ol."""
        if not node or not isinstance(node, dict):
            return ""
        
        tag = node.get("tag", "")
        
        if tag in self.LIST_CONTAINER_TAGS:
            return self._extract_list_items(node)
        
        for child in node.get("children", []):
            if child.get("tag") in self.LIST_CONTAINER_TAGS:
                return self._extract_list_items(child)
        
        return self._get_node_text(node)

    def _extract_list_items(self, list_node: dict[str, Any]) -> list[str]:
        """Extract all li items from ul/ol."""
        items = []
        for child in list_node.get("children", []):
            if child.get("tag") == "li":
                text = self._get_node_text(child)
                if text:
                    items.append(text)
        return items

    def _is_likely_label(self, text: str) -> bool:
        """Check if text looks like a field label, not a sentence."""
        if not text:
            return False
        
        text_clean = text.strip()
        text_lower = text_clean.lower()
        
        # Check against known labels first
        for label in self.COMMON_JOB_LABELS:
            if label in text_lower or text_lower in label:
                return True
        
        # Reject if starts with sentence-like patterns
        sentence_starters = (
            "this ", "that ", "these ", "those ", "there ", "here ",
            "it ", "he ", "she ", "they ", "we ", "i ", "you ",
            "the ", "a ", "an ", "my ", "your ", "his ", "her ", "our ", "their ",
            "if ", "when ", "while ", "after ", "before ", "because ", "since ",
            "what ", "how ", "why ", "where ", "who ", "which ",
        )
        if any(text_lower.startswith(s) for s in sentence_starters):
            return False
        
        # Reject if contains common verbs that suggest it's a sentence
        sentence_verbs = (" is ", " are ", " was ", " were ", " has ", " have ", " had ", " will ", " would ", " should ", " could ", " can ")
        if any(v in text_lower for v in sentence_verbs):
            return False
        
        # Short text with few words is more likely to be a label
        words = text_clean.split()
        if len(text_clean) < 40 and len(words) <= 4:
            return True
        
        return False

    def _looks_like_section_heading(self, text: str) -> bool:
        """Check if text looks like a section heading."""
        if not text:
            return False
        
        text_lower = text.lower().strip()
        
        # Check against known headings
        for heading in self.COMMON_SECTION_HEADINGS:
            if heading in text_lower or text_lower in heading:
                return True
        
        # Heuristics for headings
        if len(text) > 100:
            return False
        
        if text.endswith("."):
            return False
        
        # Title case or all caps suggests heading
        if text.istitle() or text.isupper():
            return True
        
        # Short text without sentence punctuation
        if len(text) < 60 and not any(p in text for p in ".!?"):
            return True
        
        return False

    def _add_to_result(self, result: dict[str, Any], label: str, value: Any) -> None:
        """Add label-value pair, handling duplicates intelligently."""
        if not label or not value:
            return
        
        # Clean label
        label = label.rstrip(":").strip()
        label = " ".join(label.split())
        
        if not label:
            return
        
        # Handle existing value
        if label in result:
            existing = result[label]
            # Keep longer/more detailed value
            if isinstance(existing, str) and isinstance(value, str):
                if len(value) > len(existing):
                    result[label] = value
            elif isinstance(existing, list) and isinstance(value, list):
                if len(value) > len(existing):
                    result[label] = value
            # Keep existing if same type and same/longer
            return
        
        result[label] = value

    def _cleanup_structured_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Clean up the final result."""
        cleaned = {}
        
        for k, v in result.items():
            # Skip empty values
            if not v:
                continue
            
            # Skip very short keys
            if len(k) < 2:
                continue
            
            # Clean up string values
            if isinstance(v, str):
                v = " ".join(v.split()).strip()
                if not v:
                    continue
            
            # Clean up list values
            if isinstance(v, list):
                v = [item.strip() if isinstance(item, str) else item for item in v]
                v = [item for item in v if item]
                if not v:
                    continue
            
            cleaned[k] = v
        
        return cleaned
    
    def _is_fragmented_text_container(self, children: list[dict]) -> bool:
        """
        Detect Microsoft Word-style HTML where text is split across many spans.
        """
        if not children or len(children) <= 5:
            return False
        
        span_count = 0
        short_text_count = 0
        
        for child in children:
            if not isinstance(child, dict):
                continue
            if child.get("tag") == "span":
                span_count += 1
                text = child.get("text", "") or child.get("innerText", "")
                if len(text.strip()) < 20:
                    short_text_count += 1
        
        if span_count >= 3 and short_text_count / max(span_count, 1) > 0.5:
            return True
        
        return False