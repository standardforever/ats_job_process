import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException


def _get_active_grid_sessions(grid_url: str) -> list:
    try:
        response = requests.get(f"{grid_url}/status", timeout=5)
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


def _kill_session(session_id: str, grid_url: str) -> None:
    try:
        requests.delete(f"{grid_url}/session/{session_id}", timeout=5)
        print(f"[create_session] 🔪 Killed session: {session_id}")
    except Exception as e:
        print(f"[create_session] ⚠️ Could not kill session {session_id}: {e}")


def create_session(grid_url: str = "http://localhost:4444") -> str | None:
    """
    Connects to the Selenium Grid. If an active session already exists,
    reuses it. Otherwise creates a fresh one.

    Args:
        grid_url: Selenium Grid URL (e.g. "http://localhost:4444")

    Returns:
        cdp_url (str) on success, None on failure
    """
    print("[create_session] Connecting to Grid...")

    existing_sessions = _get_active_grid_sessions(grid_url)

    # Reuse the first active session if one exists
    if existing_sessions:
        session_id = existing_sessions[0]
        cdp_url = f"ws://localhost:4444/session/{session_id}/se/cdp"
        print(f"[create_session] ♻️  Reusing existing session: {session_id}")
        print(f"[create_session] 🔗 CDP URL: {cdp_url}")
        return cdp_url

    # No existing session — create a fresh one
    try:
        driver = webdriver.Remote(
            command_executor=grid_url,
            options=Options(),
        )

        cdp_url = f"ws://localhost:4444/session/{driver.session_id}/se/cdp"
        print(f"[create_session] ✅ Session created: {driver.session_id}")
        print(f"[create_session] 🔗 CDP URL: {cdp_url}")
        return cdp_url

    except WebDriverException as e:
        print(f"[create_session] ❌ WebDriverException: {e}")
        return None

    except Exception as e:
        print(f"[create_session] ❌ Unexpected error: {e}")
        return None