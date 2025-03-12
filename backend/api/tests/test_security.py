import os
import pytest
import asyncio
import json
import requests
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

# API Endpoint (Replace with actual function URL)
BASE_URL = os.getenv("BASE_API_URL", "http://localhost:7071/api")

# Sample headers
HEADERS = {
    "Content-Type": "application/json",
    # "Authorization": f"Bearer {os.getenv('VALID_JWT_TOKEN', 'test-token')}",
}

# Malicious payloads for security testing
MALICIOUS_PAYLOADS = [
    "' OR '1'='1",  # SQL Injection
    "<script>alert('XSS')</script>",  # XSS
    "{ '$where': '1 == 1' }",  # NoSQL Injection
    "{ '__proto__': { 'admin': true } }",  # Prototype Pollution
]

# def test_invalid_auth_token():
#     """Ensure API rejects invalid JWT tokens."""
#     url = f"{BASE_URL}/counter"
#     headers = {
#         "Content-Type": "application/json",
#         "Authorization": "Bearer invalid-token",
#     }
#     response = requests.post(url, headers=headers, json={"id": "visitorCount"})

#     assert response.status_code in [401, 403], "Invalid token was accepted!"


def test_rate_limiting():
    """Ensure API enforces rate limits to prevent abuse."""
    url = f"{BASE_URL}/counter"
    for _ in range(10):  # Simulate rapid requests
        response = requests.post(url, headers=HEADERS, json={"id": "visitorCount"})

    assert response.status_code == 429, "Rate limiting is not enforced!"


def test_cors_policies():
    """Ensure only allowed origins can access the API."""
    url = f"{BASE_URL}/counter"
    headers = {
        "Origin": "https://malicious-site.com",
        "Content-Type": "application/json",
    }
    response = requests.options(url, headers=headers)

    assert "Access-Control-Allow-Origin" not in response.headers or response.headers[
        "Access-Control-Allow-Origin"
    ] != "*", "CORS policy allows any origin!"


@pytest.mark.parametrize("payload", MALICIOUS_PAYLOADS)
def test_path_traversal(payload):
    """Ensure API rejects directory traversal attempts."""
    url = f"{BASE_URL}/{quote(payload)}"
    response = requests.get(url, headers=HEADERS)

    assert response.status_code in [400, 404], f"Path traversal detected: {payload}"

def test_sensitive_data_exposure():
    """Ensure API does not expose sensitive information in error messages."""
    url = f"{BASE_URL}/counter"
    response = requests.post(url, headers=HEADERS, json={"id": "visitorCount"})

    assert "exception" not in response.text.lower(), "Sensitive error details exposed!"
    assert "traceback" not in response.text.lower(), "Debug mode might be enabled!"


@pytest.mark.parametrize("header", ["X-Forwarded-For", "X-Real-IP"])
def test_ip_spoofing(header):
    """Ensure API is protected against IP spoofing attacks."""
    url = f"{BASE_URL}/counter"
    headers = HEADERS.copy()
    headers[header] = "127.0.0.1"

    response = requests.post(url, headers=headers, json={"id": "visitorCount"})

    assert response.status_code not in [200], "IP spoofing might be possible!"


def test_cookies_secure_flag():
    """Ensure authentication cookies have secure attributes."""
    url = f"{BASE_URL}/counter"
    response = requests.post(url, headers=HEADERS, json={"id": "visitorCount"})

    cookies = response.cookies
    for cookie in cookies:
        assert cookies[cookie].secure, f"Secure flag missing for {cookie}"
        assert "httponly" in str(cookies[cookie]), f"HttpOnly flag missing for {cookie}"


@pytest.mark.parametrize(
    "user_agent",
    [
        "sqlmap/1.5.1",
        "nmap/7.80",
        "python-requests/2.26.0",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    ],
)
def test_bot_detection(user_agent):
    """Ensure API detects and blocks bot requests."""
    url = f"{BASE_URL}/counter"
    headers = HEADERS.copy()
    headers["User-Agent"] = user_agent

    response = requests.post(url, headers=headers, json={"id": "visitorCount"})

    assert response.status_code in [403, 429], f"Bot request allowed: {user_agent}"


def test_https_enforcement():
    """Ensure API does not allow insecure HTTP connections."""
    insecure_url = BASE_URL.replace("https://", "http://")
    response = requests.post(f"{insecure_url}/counter", headers=HEADERS)

    assert response.status_code in [301, 404, 426], "Insecure HTTP request allowed!"



