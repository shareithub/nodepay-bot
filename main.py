import asyncio
import cloudscraper
import json
import time
from loguru import logger
import requests
from concurrent.futures import ThreadPoolExecutor

PING_INTERVAL = 60
RETRIES = 60
MAX_PROXY_PER_TOKEN = 3  # Misalnya batas maksimum proxy per token

DOMAIN_API = {
    "SESSION": "http://api.nodepay.ai/api/auth/session",
    "PING": [
        "https://nw.nodepay.org/api/network/ping"
    ]
}

CONNECTION_STATES = {
    "CONNECTED": 1,
    "DISCONNECTED": 2,
    "NONE_CONNECTION": 3
}

# Fungsi untuk membaca proxy dari file proxy.txt
async def load_proxies():
    try:
        with open('proxy.txt', 'r') as file:
            proxies = []
            for line in file:
                line = line.strip()
                if line and not line.startswith('#'):  # Mengabaikan baris kosong atau yang dimulai dengan '#'
                    proxies.append(line)
        return proxies
    except Exception as e:
        logger.error(f"Failed to load proxies: {e}")
        raise SystemExit("Exiting due to failure in loading proxies")

class AccountInfo:
    def __init__(self, token, proxies):
        self.token = token
        self.proxies = proxies  # Menggunakan proxy yang dibaca dari file
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
        self.retries = 3

    def get_proxy(self):
        """Mengambil proxy yang akan digunakan (akan mengambil dari daftar proxy yang dibaca)"""
        return self.proxies[0]  # Ambil proxy pertama dari daftar proxy

# Cloudscraper instance
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

async def load_tokens():
    try:
        with open('Token.txt', 'r') as file:
            tokens = []
            for line in file:
                line = line.strip()
                if line and not line.startswith('#'):  # Mengabaikan baris kosong atau yang dimulai dengan '#'
                    tokens.append(line)
        return tokens
    except Exception as e:
        logger.error(f"Failed to load tokens: {e}")
        raise SystemExit("Exiting due to failure in loading tokens")

async def call_api(url, data, account_info, proxy):
    headers = {
        "Authorization": f"Bearer {account_info.token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://app.nodepay.ai/",
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "chrome-extension://lgmpfmgeabnnlemejacfljbmonaomfmm",
    }

    proxy_config = {
        "http": proxy,
        "https": proxy
    }

    try:
        response = scraper.post(url, json=data, headers=headers, proxies=proxy_config, timeout=60)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Error during API call for token {account_info.token} with proxy {proxy}: {e}")
        raise ValueError(f"Failed API call to {url}")

    return response.json()


async def render_profile_info(account_info):
    try:
        for proxy in account_info.proxies:
            try:
                response = await call_api(DOMAIN_API["SESSION"], {}, account_info, proxy)
                if response.get("code") == 0:
                    account_info.account_data = response["data"]
                    if account_info.account_data.get("uid"):
                        await start_ping(account_info)
                        return
                else:
                    logger.warning(f"Session failed for token {account_info.token} using proxy {proxy}")
            except Exception as e:
                logger.error(f"Failed to render profile info for token {account_info.token} using proxy {proxy}: {e}")

        logger.error(f"All proxies failed for token {account_info.token}")
    except Exception as e:
        logger.error(f"Error in render_profile_info for token {account_info.token}: {e}")


async def start_ping(account_info):
    try:
        logger.info(f"Starting ping for token {account_info.token}")
        while True:
            for proxy in account_info.proxies:
                try:
                    await asyncio.sleep(PING_INTERVAL)
                    await ping(account_info, proxy)
                except Exception as e:
                    logger.error(f"Ping failed for token {account_info.token} using proxy {proxy}: {e}")
    except asyncio.CancelledError:
        logger.info(f"Ping task for token {account_info.token} was cancelled")
    except Exception as e:
        logger.error(f"Error in start_ping for token {account_info.token}: {e}")


async def ping(account_info, proxy):
    for url in DOMAIN_API["PING"]:
        try:
            data = {
                "id": account_info.account_data.get("uid"),
                "browser_id": account_info.browser_id,
                "timestamp": int(time.time())
            }
            response = await call_api(url, data, account_info, proxy)
            
            if isinstance(response, dict) and "code" in response:
                if response["code"] == 0:
                    logger.info(f"Ping successful for token {account_info.token} using proxy {proxy}")
                    return
                else:
                    logger.error(f"Ping failed for token {account_info.token} with code {response.get('code')} using proxy {proxy}")
            else:
                logger.error(f"Unexpected response format for token {account_info.token} using proxy {proxy}: {response}")
            
        except Exception as e:
            logger.error(f"Ping failed for token {account_info.token} using URL {url} and proxy {proxy}: {e}")
            handle_ping_fail(account_info, e)


def handle_ping_fail(account_info, response):
    global RETRIES

    RETRIES += 1

    if isinstance(response, dict) and response.get("code") == 403:
        handle_logout(account_info)
    elif RETRIES < 2:
        account_info.status_connect = CONNECTION_STATES["DISCONNECTED"]
    else:
        account_info.status_connect = CONNECTION_STATES["DISCONNECTED"]
        logger.warning(f"Retry limit exceeded for account {account_info.token}, continuing with the same proxy.")


def handle_logout(proxy):
    global status_connect, account_info
    status_connect = CONNECTION_STATES["NONE_CONNECTION"]
    account_info = {}
    save_status(proxy, None)
    logger.error(f"Logged out and cleared session info for proxy {proxy}")

def process_account(token, proxies):
    """
    Process a single account: Initialize proxies and start asyncio event loop for this account.
    """
    account_info = AccountInfo(token, proxies)
    asyncio.run(render_profile_info(account_info))


async def main():
    tokens = await load_tokens()
    proxies = await load_proxies()  # Membaca proxy dari file proxy.txt

    with ThreadPoolExecutor(max_workers=1000) as executor:
        futures = []
        for token in tokens:
            futures.append(executor.submit(process_account, token, proxies))

        # Tunggu hingga semua tugas selesai
        for future in futures:
            future.result()  # Block hingga setiap future selesai


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Program terminated by user.")
