from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Browser, Page, Playwright, async_playwright

from utils.logging import setup_logger

logger = setup_logger(__name__)


@dataclass
class CDPConfig:
    cdp_url: str


class ChromeCDPManager:
    def __init__(self, config: Optional[CDPConfig] = None):
        self.config = config or CDPConfig()
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        logger.debug("ChromeCDPManager initialized", extra={"cdp_url": self.config.cdp_url})

    @property
    def browser(self) -> Optional[Browser]:
        return self._browser

    @property
    def page(self) -> Optional[Page]:
        return self._page

    async def connect(self) -> Page:
        logger.info("Connecting Playwright to CDP", extra={"cdp_url": self.config.cdp_url})

        if self._browser is not None:
            raise RuntimeError("Playwright is already connected.")

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(self.config.cdp_url)

        # Use existing context from the CDP session, don't create a new one
        contexts = self._browser.contexts
        if contexts:
            context = contexts[0]
            logger.debug("Reusing existing browser context")
        else:
            context = await self._browser.new_context()
            logger.debug("No existing context found, created new one")

        self._page = await context.new_page()
        logger.debug("Created new page on existing context")

        logger.info("Playwright connected successfully")
        return self._page

    async def disconnect(self) -> None:
        logger.info("Disconnecting Playwright")

        if self._page is not None:
            await self._page.close()
            self._page = None

        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

        logger.info("Playwright disconnected successfully")

    async def __aenter__(self) -> "ChromeCDPManager":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
        
    @property
    def cdp_url(self) -> str:
        return self.config.cdp_url
    
    