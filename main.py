import asyncio
import cloudscraper
import json
import time
import uuid
from loguru import logger
import requests
from curl_cffi import requests as curl_requests
from colorama import Fore, Style, init
from datetime import datetime

init(autoreset=True)

# Konstanta
PING_INTERVAL = 120
RETRIES = 60
MAX_PROXY_PER_TOKEN = 3  # Setiap token hanya bisa menggunakan maksimal 10 proxy

DOMAIN_API = {
    "SESSION": "http://api.nodepay.ai/api/auth/session",
    "PING": [
        "http://18.142.29.174/api/network/ping",
        "https://nw.nodepay.org/api/network/ping"
    ],
    "DEVICE_NETWORK": "https://api.nodepay.org/api/network/device-networks"
}

CONNECTION_STATES = {
    "CONNECTED": 1,
    "DISCONNECTED": 2,
    "NONE_CONNECTION": 3
}

class AccountInfo:
    def __init__(self, token, proxy_list):
        self.token = token
        self.proxy_list = proxy_list  # List of proxies assigned to this account
        self.active_proxies = proxy_list[:MAX_PROXY_PER_TOKEN]  # Keep only 10 proxies
        self.status_connect = CONNECTION_STATES["NONE_CONNECTION"]
        self.account_data = {}
        self.retries = 0
        self.last_ping_status = 'Waiting...'
        self.browser_id = {
            'ping_count': 0,
            'successful_pings': 0,
            'score': 0,
            'start_time': time.time(),
            'last_ping_time': None
        }

    def reset(self):
        self.status_connect = CONNECTION_STATES["NONE_CONNECTION"]
        self.account_data = {}
        self.retries = 0

    def remove_failed_proxy(self, failed_proxy):
        if failed_proxy in self.active_proxies:
            self.active_proxies.remove(failed_proxy)
            logger.info(f"Removed failed proxy {failed_proxy} from active proxies.")
            return True
        return False

    def add_new_proxy(self, new_proxy):
        if len(self.active_proxies) < MAX_PROXY_PER_TOKEN:
            self.active_proxies.append(new_proxy)
            logger.info(f"Added new proxy {new_proxy} to active proxies.")
            return True
        return False

scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

def check_proxy(proxy):
    try:
        proxy_config = {
            "http": f"http://{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}",
            "https": f"http://{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"
        } if proxy.get('username') and proxy.get('password') else {
            "http": f"http://{proxy['host']}:{proxy['port']}",
            "https": f"http://{proxy['host']}:{proxy['port']}"
        }
        
        response = requests.get("https://ipinfo.io/json", proxies=proxy_config, timeout=10)
        response.raise_for_status()
        ip_info = response.json()
        logger.info(f"Proxy {proxy['host']}:{proxy['port']} is working. IP: {ip_info['ip']}")
        return True
    except requests.RequestException as e:
        logger.error(f"Proxy {proxy['host']}:{proxy['port']} failed: {e}")
        return False

async def load_tokens():
    try:
        with open('Token.txt', 'r') as file:
            tokens = file.read().splitlines()
        return tokens
    except Exception as e:
        logger.error(f"Failed to load tokens: {e}")
        raise SystemExit("Exiting due to failure in loading tokens")

async def load_proxies(proxy_file):
    try:
        with open(proxy_file, 'r') as file:
            proxies = file.read().splitlines()
        return proxies
    except Exception as e:
        logger.error(f"Failed to load proxies: {e}")
        raise SystemExit("Exiting due to failure in loading proxies")

async def call_api(url, data, account_info):
    headers = {
        "Authorization": f"Bearer {account_info.token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://app.nodepay.ai/",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "chrome-extension://lgmpfmgeabnnlemejacfljbmonaomfmm",
        "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cors-site"
    }

    try:
        response = scraper.post(url, json=data, headers=headers, proxies={"http": account_info.active_proxies[0], "https": account_info.active_proxies[0]}, timeout=10)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Error during API call for token {account_info.token} with proxy {account_info.active_proxies[0]}: {e}")
        raise ValueError(f"Failed API call to {url}")

    return response.json()

async def render_profile_info(account_info):
    try:
        response = await call_api(DOMAIN_API["SESSION"], {}, account_info)
        if response.get("code") == 0:
            account_info.account_data = response["data"]
            if account_info.account_data.get("uid"):
                await start_ping(account_info)
            else:
                handle_logout(account_info)
        else:
            handle_logout(account_info)
    except Exception as e:
        logger.error(f"Error in render_profile_info for proxy {account_info.active_proxies[0]} with token {account_info.token}: {e}")

async def start_ping(account_info):
    try:
        logger.info(f"Starting ping for proxy {account_info.active_proxies[0]} with token {account_info.token}")
        await ping(account_info)
        if account_info.status_connect == CONNECTION_STATES["CONNECTED"]:
            await validate_account(account_info)  # Validasi akun setelah ping sukses
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await ping(account_info)
            if account_info.status_connect == CONNECTION_STATES["CONNECTED"]:
                await validate_account(account_info)  # Validasi akun setelah ping sukses
    except asyncio.CancelledError:
        logger.info(f"Ping task for proxy {account_info.active_proxies[0]} was cancelled for token {account_info.token}")
    except Exception as e:
        logger.error(f"Error in start_ping for proxy {account_info.active_proxies[0]} with token {account_info.token}: {e}")


async def ping(account_info):
    global RETRIES

    for url in DOMAIN_API["PING"]:
        try:
            data = {
                "id": account_info.account_data.get("uid"),
                "browser_id": account_info.browser_id,
                "timestamp": int(time.time())
            }

            response = await call_api(url, data, account_info)
            if response["code"] == 0:
                logger.info(f"Token {account_info.token}: Ping successful via proxy {account_info.active_proxies[0]} using URL {url}")
                RETRIES = 0
                account_info.status_connect = CONNECTION_STATES["CONNECTED"]
                return
            else:
                handle_ping_fail(account_info, response)
        except Exception as e:
            logger.error(f"Token {account_info.token}: Ping failed via proxy {account_info.active_proxies[0]} using URL {url}: {e}")

    handle_ping_fail(account_info, None)


async def validate_account(account_info):
    try:
        logger.info(f"Validating account {account_info.token} via device network API")
        
        # Headers untuk permintaan
        headers = {
            "Authorization": f"Bearer {account_info.token}",  # Pastikan token dikirim di sini
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
            "Origin": "https://app.nodepay.ai",
            "Referer": "https://app.nodepay.ai/",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Sec-CH-UA": "\"Google Chrome\";v=\"131\", \"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\"",
            "Sec-CH-UA-Mobile": "?1",
            "Sec-CH-UA-Platform": "\"Android\""
        }

        # Parameter untuk query string
        params = {
            "page": 0,
            "limit": 10,
            "active": "true"
        }

        # Melakukan permintaan GET dengan parameter dan headers
        response = curl_requests.get(DOMAIN_API["DEVICE_NETWORK"], headers=headers, params=params)

        # Memeriksa status code dan memproses data
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                devices = data.get("data", [])
                logger.info(f"Account {account_info.token}: Total devices found: {len(devices)}")
                for device in devices:
                    logger.info(f"Account {account_info.token}: IP: {device.get('ip_address')}, IP Score: {device.get('ip_score')}, Total Points: {device.get('total_points')}")
            else:
                logger.error(f"Account {account_info.token}: Validation failed: {data.get('msg')}")
        elif response.status_code == 401:
            logger.error(f"Account {account_info.token}: Unauthorized - Invalid or expired token.")
            handle_logout(account_info)  # Reset akun jika token tidak sah
        elif response.status_code == 403:
            logger.error(f"Account {account_info.token}: Validation API failed with status code 403. Token or proxy may be blocked.")
            handle_logout(account_info)  # Reset akun jika 403
        else:
            logger.error(f"Account {account_info.token}: Validation API failed with status code {response.status_code}")
    except Exception as e:
        logger.error(f"Account {account_info.token}: Error validating account: {e}")



def handle_ping_fail(account_info, response):
    global RETRIES

    RETRIES += 1
    if response and response.get("code") == 403:
        handle_logout(account_info)
    elif RETRIES < 2:
        account_info.status_connect = CONNECTION_STATES["DISCONNECTED"]
    else:
        account_info.status_connect = CONNECTION_STATES["DISCONNECTED"]
        if len(account_info.active_proxies) < MAX_PROXY_PER_TOKEN:
            new_proxy = account_info.proxy_list[len(account_info.active_proxies)]
            if check_proxy(new_proxy):
                account_info.add_new_proxy(new_proxy)
        else:
            logger.warning(f"All proxies exhausted for account {account_info.token}.")


def handle_logout(account_info):
    account_info.reset()
    logger.info(f"Logged out and cleared session info for proxy {account_info.active_proxies[0]}")


async def main():
    tokens = await load_tokens()
    all_proxies = await load_proxies('Proxy.txt')

    token_proxy_mapping = {}
    for i, token in enumerate(tokens):
        proxies_for_token = all_proxies * (MAX_PROXY_PER_TOKEN // len(all_proxies)) + all_proxies[:MAX_PROXY_PER_TOKEN % len(all_proxies)]
        token_proxy_mapping[token] = proxies_for_token[:MAX_PROXY_PER_TOKEN]

    tasks = []
    for token_id, (token, proxies) in enumerate(token_proxy_mapping.items(), start=1):
        account_info = AccountInfo(token, proxies)
        task = asyncio.create_task(render_profile_info(account_info))
        tasks.append(task)

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())  # Ganti get_event_loop dengan asyncio.run
