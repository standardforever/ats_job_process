import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

GRID_URL = "http://localhost:4444"

driver = webdriver.Remote(
    command_executor=GRID_URL,
    options=Options(),
)

session_id = driver.session_id
cdp_url = f"ws://localhost:4444/session/{session_id}/se/cdp"

print(f"Session ID: {session_id}")
print(f"CDP URL:    {cdp_url}")