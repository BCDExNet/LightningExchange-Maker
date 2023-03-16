import configparser
import time
import json
from web3 import Web3
from web3.middleware import geth_poa_middleware
from multicall import Call, Multicall


# Load config from JSON file
with open("config.json", "r") as config_file:
    config = json.load(config_file)

# Read assets information from config file
price_config = configparser.ConfigParser()
price_config.read("eth_tokenprices.ini")

# Set up connection to Ethereum node
eth_rpc = "https://rpc.ankr.com/arbitrum"
w3 = Web3(Web3.HTTPProvider(eth_rpc))
w3.middleware_onion.inject(geth_poa_middleware, layer=0)
multicall_contract_address = Web3.toChecksumAddress(price_config.get("MULTICALL", "ContractAddress"))
# multicall = Multicall()
# multicall.aggregator=Web3.toChecksumAddress(price_config.get("MULTICALL", "ContractAddress"))


# Verify connection
if not w3.isConnected():
    print("Not connected to Ethereum node")
    exit()

asset_data = dict(price_config["ASSETS"])


# Define the Chainlink Oracle contract ABI
chainlink_oracle_abi = [
    {
        "constant": True,
        "inputs": [],
        "name": "latestAnswer",
        "outputs": [{"name": "", "type": "int256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    }
]

# Cache variables
cache = {}
cache_timeout = 60


def fetch_prices():
    # Check if the prices are already cached and still valid
    if "prices" in cache and len(cache["prices"]) >= len(asset_data) and "timestamp" in cache and time.time() - cache["timestamp"] < cache_timeout:
        return cache["prices"]
  
    calls = []

    for symbol in asset_data:
        calls.append(Call(asset_data[symbol], ["latestAnswer()(int256)"], [[(symbol, None)]]))

    results = Multicall(calls, w3, multicall_contract_address)()
    prices = {symbol: float(w3.fromWei(result, "ether")) for symbol, result in zip(asset_data, results)}

    # Cache the results
    cache["timestamp"] = time.time()
    cache["prices"] = prices

    return prices

def get_relative_price(asset1, asset2):
    prices = fetch_prices()
    return prices[asset1.lower()] / prices[asset2.lower()]

# Example usage
if __name__ == "__main__":
    print(get_relative_price("ETH", "BTC"))
