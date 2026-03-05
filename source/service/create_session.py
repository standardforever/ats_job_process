import os
from urllib.parse import urlparse

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.remote.webdriver import WebDriver


def _normalize_grid_url(raw_url: str) -> tuple[str, str, str]:
    url = (raw_url or "").strip()
    if not url:
        url = "http://127.0.0.1:4445/wd/hub"
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid Selenium URL: {raw_url}")

    executor_url = url
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    cdp_host = parsed.netloc
    return executor_url, base_url, f"{ws_scheme}://{cdp_host}"


def _get_active_grid_sessions(base_url: str) -> list:
    try:
        response = requests.get(f"{base_url}/status", timeout=5)
        if response.status_code != 200:
            return []

        nodes = response.json().get("value", {}).get("nodes", [])
        active_sessions = []
        for node in nodes:
            for slot in node.get("slots", []):
                session = slot.get("session")
                if session is not None:
                    active_sessions.append(session.get("sessionId"))

        return active_sessions

    except Exception as e:
        print(f"[create_session] ⚠️ Could not fetch grid sessions: {e}")
        return []


def _kill_session(session_id: str, base_url: str) -> None:
    try:
        requests.delete(f"{base_url}/session/{session_id}", timeout=5)
        print(f"[create_session] 🔪 Killed session: {session_id}")
    except Exception as e:
        print(f"[create_session] ⚠️ Could not kill session {session_id}: {e}")


def create_session(grid_url: str | None = None) -> str | None:
    """
    Connects to Selenium Grid. Reuses existing active session when possible,
    otherwise creates a new one and returns its CDP endpoint.

    Priority for grid URL:
      1) function arg
      2) SELENIUM_REMOTE_URL env var
      3) default http://127.0.0.1:4445/wd/hub
    """
    raw_grid = grid_url or os.getenv("SELENIUM_REMOTE_URL") or "http://127.0.0.1:4445/wd/hub"

    try:
        executor_url, base_url, cdp_base = _normalize_grid_url(raw_grid)
    except Exception as e:
        print(f"[create_session] ❌ Invalid grid URL {raw_grid}: {e}")
        return None

    print(f"[create_session] Connecting to Grid: {base_url}")

    existing_sessions = _get_active_grid_sessions(base_url)

    # Reuse the first active session if one exists
    if existing_sessions:
        session_id = existing_sessions[0]
        cdp_url = f"{cdp_base}/session/{session_id}/se/cdp"
        print(f"[create_session] ♻️  Reusing existing session: {session_id}")
        print(f"[create_session] 🔗 CDP URL: {cdp_url}")
        return cdp_url

    # No existing session — create a fresh one
    try:
        driver = webdriver.Remote(
            command_executor=executor_url,
            options=_build_stealth_options(),
        )
        patch_webdriver_flag(driver)

        cdp_url = f"{cdp_base}/session/{driver.session_id}/se/cdp"
        print(f"[create_session] ✅ Session created: {driver.session_id}")
        print(f"[create_session] 🔗 CDP URL: {cdp_url}")
        return cdp_url

    except WebDriverException as e:
        print(f"[create_session] ❌ WebDriverException: {e}")
        return None

    except Exception as e:
        print(f"[create_session] ❌ Unexpected error: {e}")
        return None
    

def _build_stealth_options() -> Options:
    options = Options()
    
    # Remove automation flags
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    # Disable infobars
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # Optional but helps blend in
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
    
    return options


def patch_webdriver_flag(driver: WebDriver) -> None:
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """
    })