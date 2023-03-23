import requests
import time
import traceback
import json

cache = {}
cache_timeout = 60  # Cache timeout in seconds

def load_config():
    try:
        # Load config from JSON file
        with open("config.json", "r") as config_file:
            return json.load(config_file)
    except Exception as e:
        errormsg = traceback.format_exc()
        logging.error(f"Failed to load config.json: {str(e)}\n{errormsg}")
        return None

config = load_config()

if config is None:
    logging.error("Unable to load config. Exiting.")
    exit()

def fetch_prices():
    if "timestamp" in cache and time.time() - cache["timestamp"] < cache_timeout:
        return cache["prices"]

    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": config["asset_names"]
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    prices_data = response.json()

    extracted_prices = {}
    supported_assets = get_supported_tokens()
    for price_data in prices_data:
        symbol = price_data["symbol"].upper()
        if symbol in supported_assets:
            extracted_prices[symbol] = float(price_data["current_price"])

    cache["timestamp"] = time.time()
    cache["prices"] = extracted_prices

    return extracted_prices

def get_supported_tokens():
    tokens = ["BTC"]
    for item in config['supported_assets']:
        tokens.append(item["name"].upper())
    return tokens

def get_relative_price(token1, token2):
    supported_assets = get_supported_tokens()
    if token1.upper() not in supported_assets or token2.upper() not in supported_assets:
        raise ValueError("Invalid token symbol")

    prices = fetch_prices()
    return prices[token1.upper()]/prices[token2.upper()]
