import asyncio
import cloudscraper
import json
import time
import uuid
from loguru import logger

PING_INTERVAL = 130
RETRIES = 60
MAX_PROXY_PER_TOKEN = 20  # Setiap token hanya bisa menggunakan maksimal 20 proxy

DOMAIN_API = {
    "SESSION": "https://api.nodepay.ai/api/auth/session",
    "PING": [
        "http://13.215.134.222/api/network/ping",
        "http://52.77.10.116/api/network/ping"
    ]
}

CONNECTION_STATES = {
    "CONNECTED": 1,
    "DISCONNECTED": 2,
    "NONE_CONNECTION": 3
}

status_connect = CONNECTION_STATES["NONE_CONNECTION"]
account_info = {}

browser_id = {
    'ping_count': 0,
    'successful_pings': 0,
    'score': 0,
    'start_time': time.time(),
    'last_ping_status': 'Waiting...',
    'last_ping_time': None
}

def load_tokens():
    try:
        with open('Token.txt', 'r') as file:
            tokens = file.read().splitlines()
        return tokens
    except Exception as e:
        logger.error(f"Failed to load tokens: {e}")
        raise SystemExit("Exiting due to failure in loading tokens")

def load_proxies(proxy_file):
    try:
        with open(proxy_file, 'r') as file:
            proxies = file.read().splitlines()
        return proxies
    except Exception as e:
        logger.error(f"Failed to load proxies: {e}")
        raise SystemExit("Exiting due to failure in loading proxies")

def load_session_info(proxy):
    return {}

def save_session_info(proxy, data):
    pass

def save_status(proxy, status):
    pass

def is_valid_proxy(proxy):
    return True

def remove_proxy_from_list(proxy):
    pass

scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

def valid_resp(resp):
    if not resp or "code" not in resp or resp["code"] < 0:
        raise ValueError("Invalid response")
    return resp

async def render_profile_info(proxy, token, token_id):
    global account_info

    try:
        np_session_info = load_session_info(proxy)

        if not np_session_info:
            response = call_api(DOMAIN_API["SESSION"], {}, proxy, token)
            valid_resp(response)
            account_info = response["data"]
            if account_info.get("uid"):
                save_session_info(proxy, account_info)
                await start_ping(proxy, token, token_id)
            else:
                handle_logout(proxy)
        else:
            account_info = np_session_info
            await start_ping(proxy, token, token_id)
    except Exception as e:
        logger.error(f"Error in render_profile_info for proxy {proxy} with token {token_id}: {e}")
        error_message = str(e)
        if any(phrase in error_message for phrase in [
            "sent 1011 (internal error) keepalive ping timeout; no close frame received",
            "500 Internal Server Error"
        ]):
            logger.info(f"Removing error proxy from the list: {proxy}")
            remove_proxy_from_list(proxy)
            return None
        else:
            logger.error(f"Connection error: {e}")
            return proxy

def call_api(url, data, proxy, token):
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://app.nodepay.ai/",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://app.nodepay.ai",
        "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cors-site"
    }

    try:
        response = scraper.post(url, json=data, headers=headers, proxies={"http": proxy, "https": proxy}, timeout=10)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Error during API call for token {token}: {e}")
        raise ValueError(f"Failed API call to {url}")

    return valid_resp(response.json())

async def start_ping(proxy, token, token_id):
    try:
        logger.info(f"Starting ping for proxy {proxy} with token {token_id}")
        await ping(proxy, token, token_id)
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await ping(proxy, token, token_id)
    except asyncio.CancelledError:
        logger.info(f"Ping task for proxy {proxy} was cancelled for token {token_id}")
    except Exception as e:
        logger.error(f"Error in start_ping for proxy {proxy} with token {token_id}: {e}")

async def ping(proxy, token, token_id):
    global RETRIES, status_connect

    for url in DOMAIN_API["PING"]:
        try:
            data = {
                "id": account_info.get("uid"),
                "browser_id": browser_id,
                "timestamp": int(time.time())
            }
            
            response = call_api(url, data, proxy, token)
            if response["code"] == 0:
                logger.info(f"Token {token_id}: Ping successful via proxy {proxy} using URL {url}: {response}")
                RETRIES = 0
                status_connect = CONNECTION_STATES["CONNECTED"]
                return
            else:
                handle_ping_fail(proxy, response)
        except Exception as e:
            logger.error(f"Token {token_id}: Ping failed via proxy {proxy} using URL {url}: {e}")

    # If all URLs fail, increment retry count
    handle_ping_fail(proxy, None)

def handle_ping_fail(proxy, response):
    global RETRIES, status_connect

    RETRIES += 1
    if response and response.get("code") == 403:
        handle_logout(proxy)
    elif RETRIES < 2:
        status_connect = CONNECTION_STATES["DISCONNECTED"]
    else:
        status_connect = CONNECTION_STATES["DISCONNECTED"]

def handle_logout(proxy):
    global status_connect, account_info

    status_connect = CONNECTION_STATES["NONE_CONNECTION"]
    account_info = {}
    save_status(proxy, None)
    logger.info(f"Logged out and cleared session info for proxy {proxy}")

async def main():
    # Load tokens and proxies
    tokens = load_tokens()
    all_proxies = load_proxies('Proxy.txt')

    # Split proxies evenly across tokens (each token gets a maximum of 20 proxies)
    token_proxy_mapping = {}
    for i, token in enumerate(tokens):
        token_proxy_mapping[token] = all_proxies[i * MAX_PROXY_PER_TOKEN : (i + 1) * MAX_PROXY_PER_TOKEN]

    # Create tasks for each token-proxy pair
    tasks = []
    for token_id, (token, proxies) in enumerate(token_proxy_mapping.items(), start=1):
        for proxy in proxies:
            task = asyncio.create_task(render_profile_info(proxy, token, token_id))
            tasks.append(task)

    await asyncio.gather(*tasks)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Program terminated by user.")
