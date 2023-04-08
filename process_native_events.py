import asyncio
import json
import logging
from queue import Queue
from web3 import Web3
from web3.auto import w3
from web3.middleware import geth_poa_middleware
from web3.exceptions import BlockNotFound
from web3._utils.filters import construct_event_filter_params
from web3._utils.events import get_event_data
import os
import shutil
import traceback

import event_handlers

# Load config from JSON file
with open("config.json", "r") as config_file:
    config = json.load(config_file)

# Set up connection to Ethereum node
w3 = Web3(Web3.HTTPProvider(config["provider"]))
w3.middleware_onion.inject(geth_poa_middleware, layer=0)

# Verify connection
if not w3.is_connected():
    print("Not connected to Ethereum node")
    exit()

# Initialize contract object
with open(config["native_contract_abi"], "r") as abi_file:
    token_contract_abi = json.load(abi_file)
contract = w3.eth.contract(
    address=w3.to_checksum_address(config["native_contract_address"]), 
    abi=token_contract_abi
)

# Initialize event queue
event_queue = Queue()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("event_listener.log"),
        logging.StreamHandler(),
    ],
)

def load_last_block_number(filename="last_block_number.txt"):
    try:
        with open(filename, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return "latest"

def save_last_block_number(block_number, filename="last_block_number.txt"):
    with open(filename, "w") as f:
        f.write(str(block_number))

# Initialize event listener with the last processed block number
last_block_number = load_last_block_number()
block_chunk_size=1000
check_interval = 5

def check_pending_events():
    pending_dir = 'pending_events'
    if not os.path.exists(pending_dir):
        os.makedirs(pending_dir)

    for file in os.listdir(pending_dir):
        filepath = os.path.join(pending_dir, file)
        with open(filepath, 'r') as f:
            event = json.load(f)
        event_queue.put(event)


async def fetch_old_events():
    global last_block_number

    start_block = last_block_number
    while True:
        try:
            # Get the latest block number
            latest_block = w3.eth.get_block_number()
            
            if start_block == 'latest':
                start_block = latest_block - 10

            if latest_block - start_block >= block_chunk_size:
                to_block = start_block + block_chunk_size
            else:
                to_block = latest_block
            logging.info(f"Fetching events from {start_block} to {to_block}")

            new_entries = w3.eth.get_logs({'fromBlock': start_block, 'toBlock': to_block, 'address': config["native_contract_address"]})
            event_abi = contract.events[config["event_name"]]._get_event_abi()
            for evt in new_entries:
                event = get_event_data(w3.codec, event_abi, evt)
                converted_event = event_handlers.save_event_to(event, 'pending_events')
                event_queue.put(converted_event)
            if new_entries:
                start_block = new_entries[-1]["blockNumber"]
                save_last_block_number(start_block)
                logging.info(f"Successfully fetched events up to block {start_block}")

            start_block = to_block + 1
            await asyncio.sleep(check_interval)  # Fetch new events every 5 seconds
        except Exception as e:
            errormsg = traceback.format_exc()
            logging.error(f"Failed to fetch events, retrying in 5 seconds\n{str(e)}\n{errormsg}")
            await asyncio.sleep(check_interval)  # Retry after 5 seconds in case of errors



async def fetch_events():
    global last_block_number

    await fetch_old_events()

    while True:
        try:
            event_filter = contract.events[config["event_name"]].createFilter(fromBlock=last_block_number)
            new_entries = event_filter.get_new_entries()
            for event in new_entries:
                converted_event = event_handlers.save_event_to(event, 'pending_events')
                event_queue.put(converted_event)
            if new_entries:
                last_block_number = new_entries[-1]["blockNumber"]
                save_last_block_number(last_block_number)
                logging.info(f"Successfully fetched events up to block {last_block_number}")
            await asyncio.sleep(check_interval)  # Fetch new events every 5 seconds
        except Exception as e:
            logging.error("Failed to fetch events, retrying in 5 seconds\n{e}")
            await asyncio.sleep(check_interval)  # Retry after 5 seconds in case of errors



async def process_events():
    while True:
        try:
            if not event_queue.empty():
                event = event_queue.get()
                if event_handlers.check_event_exists(event, 'completed_events'):
                    continue

                event_handler = getattr(event_handlers, f"handle_{config['event_name']}")
                success = event_handler(event)
                if success:
                    event_handlers.move_event(event, 'completed_events')
                else:
                    event_handlers.move_event(event, 'error_events')
            else:
                logging.info("Event queue is empty, waiting for 1 second")
                await asyncio.sleep(1)
        except Exception as e:
            errormsg = traceback.format_exc()
            logging.error(f"Failed to process events, retrying in 5 seconds\n{str(e)}\n{errormsg}")
            await asyncio.sleep(check_interval)  # Retry after 5 seconds in case of errors

async def main():
    check_pending_events()
    tasks = [
            asyncio.create_task(fetch_old_events()),
            asyncio.create_task(process_events())
        ]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
