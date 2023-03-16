import requests
import time

cache = {}
cache_timeout = 60  # Cache timeout in seconds

def fetch_prices():
    if "timestamp" in cache and time.time() - cache["timestamp"] < cache_timeout:
        return cache["prices"]

    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": "bitcoin,ethereum,usd-coin"
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    prices_data = response.json()

    # Extract prices for ETH, BTC, and USDC
    extracted_prices = {}
    for price_data in prices_data:
        symbol = price_data["symbol"].upper()
        if symbol in {"ETH", "BTC", "USDC"}:
            extracted_prices[symbol] = float(price_data["current_price"])

    cache["timestamp"] = time.time()
    cache["prices"] = extracted_prices

    return extracted_prices

def get_relative_price(token1, token2):
    if token1.upper() not in {"ETH", "BTC", "USDC"} or token2.upper() not in {"ETH", "BTC", "USDC"}:
        raise ValueError("Invalid token symbol")

    prices = fetch_prices()
    return prices[token1.upper()]/prices[token2.upper()]
