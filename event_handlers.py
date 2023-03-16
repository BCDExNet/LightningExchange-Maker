import json
import logging
from web3 import Web3
from web3.middleware import geth_poa_middleware
import web3.datastructures as wd
import time
import os
import traceback
import shutil
import requests
import codecs
import grpc
import lnd_grpc
import base64
import configparser
from coingeco_oracle import get_relative_price
from bolt11.core import decode
import bolt11

# import lightning_pb2 as lnrpc
# , lightning_pb2_grpc as lightningstub
# from lnd_grpc import rpc_pb2_grpc as lnrpc

# Load config from JSON file
with open("config.json", "r") as config_file:
    config = json.load(config_file)

# Set up connection to Ethereum node
w3 = Web3(Web3.HTTPProvider(config["provider"]))
w3.middleware_onion.inject(geth_poa_middleware, layer=0)

# Verify connection
if not w3.isConnected():
    print("Not connected to Ethereum node")
    exit()

# Your event handling function
def handle_DepositCreated(event):
    global config

    secret_hash = event["args"]["secretHash"]
    depositor = event["args"]["depositor"]
    beneficiary = event["args"]["beneficiary"]
    token = event["args"]["token"]
    amount = event["args"]["amount"]
    deadline = event["args"]["deadline"]
    invoice = event["args"]["invoice"]
    maker_wallet_address = config['maker_wallet_address']

    logging.info(f"Event received: DepositCreated, secretHash: {secret_hash}, depositor: {depositor}, beneficiary: {beneficiary}, token: {token}, amount: {amount}, deadline: {deadline}, invoice: {invoice}")

    if beneficiary.lower() != maker_wallet_address.lower():
        logging.error("The beneficiary does not match the maker wallet address.")
        return True

    supported_assets = config['support_accests']
    token_name = None
    token_decimals = None
    for item in supported_assets:
        for key, value in item.items():
            if token.lower() == value.lower():
                token_name = key
                print(item)
                break
        if token_name != None:
            token_decimals = item["decimals"]


    if token_name == None:
        logging.error("The TOKEN does not match the USDC contract address.")
        return True

    try:
        oracle_price = get_relative_price("btc", token_name)
        print("btc/usdc price", oracle_price)
    except Exception as e:
        errormsg = traceback.format_exc()
        logging.error(f"Failed to get price from Chainlink: {str(e)}\n{errormsg}")
        move_event(event, 'error_events')
        return False


    # Read the macaroon file and create the necessary metadata object
    # with open(config['macaroon_path'], 'rb') as f:
    #     macaroon_bytes = f.read()
    #     macaroon = codecs.encode(macaroon_bytes, 'hex')
    # auth_metadata_plugin = lnd_grpc.AuthMetadataPlugin(macaroon)
    # auth_metadata_plugin = grpc.metadata_call_credentials(auth_metadata_plugin)
    # channel_credentials = grpc.ssl_channel_credentials(open(config['tls_cert_path'], 'rb').read())
    # composite_credentials = grpc.composite_channel_credentials(channel_credentials, auth_metadata_plugin)
    # channel = grpc.secure_channel(config['ln_rpc_server'], composite_credentials)

    # # Create a stub for the LND RPC
    # stub = lnrpc.LightningStub(channel)


    try:
        decoded_invoice = bolt11.core.decode(invoice)
        
        print("invoice", "mainnet", decoded_invoice.is_mainnet())
        print("invoice", "mainnet", decoded_invoice.timestamp)
        print("invoice", "mainnet", decoded_invoice.expiry_time)
        print("invoice", "mainnet", decoded_invoice.payment_hash)
        print("invoice", "mainnet", decoded_invoice.amount)
        print("invoice", "mainnet", decoded_invoice.description)

        btc_amount = decoded_invoice.amount / 1e11
        event_btc_price = amount / 10**token_decimals / btc_amount
        print("order btc price", event_btc_price)
    except Exception as e:
        errormsg = traceback.format_exc()
        logging.error(f"Failed to decode invoice: {str(e)}\n{errormsg}")
        move_event(event, 'error_events')
        return False


    if event_btc_price < oracle_price * 0.99:
        errormsg = traceback.format_exc()
        logging.error("The BTC price in the event is lower than the Chainlink price by more than 1%.\n{errormsg}")
        move_event(event, 'error_events')
        return False

    if secret_hash != decoded_invoice.payment_hash:
        errormsg = traceback.format_exc()
        logging.error("The secret hash from the event does not match the payment hash in the invoice.\n{errormsg}")
        move_event(event, 'error_events')
        return False

    if deadline < time.time() + 1800:
        errormsg = traceback.format_exc()
        logging.error("The deadline in the event is less than 30 minutes from now.\n{errormsg}")
        move_event(event, 'error_events')
        return False

    try:
        secret = pay_invoice_and_get_secret(stub, invoice)
    except Exception as e:
        errormsg = traceback.format_exc()
        logging.error(f"Failed to pay invoice and get secret: {str(e)}\n{errormsg}")
        move_event(event, 'error_events')
        return False
    
    try:
        secret = "0x"+secret
        delegate_withdraw(secret, maker_wallet_address)
    except Exception as e:
        errormsg = traceback.format_exc()
        logging.error(f"Failed to call delegateRefund: {str(e)}\n{errormsg}")
        move_event(event, 'error_events')
        return False

    return True


def get_chainlink_btc_price(api_key):
    url = f"https://api.chain.link/v1/pricefeeds/btc-usdc?api_key={api_key}"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()["price"]


def pay_invoice_and_get_secret(stub, invoice):
    # Pay the invoice
    try:

        response = stub.SendPaymentSync(ln.SendRequest(payment_request=invoice))
        if response.payment_error:
            print(f"Payment error: {response.payment_error}")
            return None
        else:
            print(f"Payment successful! Payment preimage: {response.payment_preimage.hex()}")
            return response.payment_preimage.hex()
    except Exception as e:
        print(f"Error while trying to pay invoice: {e}")
        return None




def delegate_withdraw(secret, maker_wallet_address):
    global config
    # Set your Ethereum wallet's private key
    bot_address = config["maker_bot_address"]
    bot_private_key = config["maker_bot_privatekey"]
    contract_address = config["contract_address"]
    contract_abi = config["contract_abi"]


    # Initialize the contract instance
    contract_instance = w3.eth.contract(address=contract_address, abi=contract_abi)

    # Get the nonce for the transaction
    nonce = w3.eth.getTransactionCount(bot_address)

    # Estimate the gas limit
    gas_limit = contract_instance.functions.delegateWithdraw(secret, maker_wallet_address).estimateGas()

    # Build the transaction dictionary
    transaction = contract_instance.functions.delegateWithdraw(secret, maker_wallet_address).buildTransaction({
        'gas': gas_limit,
        'gasPrice': w3.eth.gasPrice,
        'nonce': nonce
    })

    # Sign the transaction
    signed_transaction = w3.eth.account.signTransaction(transaction, bot_private_key)

    # Send the transaction
    transaction_hash = w3.eth.sendRawTransaction(signed_transaction.rawTransaction)

    # Wait for the transaction receipt
    transaction_receipt = w3.eth.waitForTransactionReceipt(transaction_hash)

    # Check if the transaction was successful
    if transaction_receipt['status'] == 1:
        return True
    else:
        return False

def attribute_dict_to_dict(attribute_dict):
    result = {}
    for key, value in attribute_dict.items():
        if isinstance(value, wd.AttributeDict):
            result[key] = attribute_dict_to_dict(value)
        elif isinstance(value, dict):
            result[key] = attribute_dict_to_dict(value)
        elif isinstance(value, (bytes, bytearray)):
            result[key] = str(value.hex())
        else:
            result[key] = value
    return result

def save_event_to(event, target_folder):
    if not os.path.exists(target_folder):
        os.makedirs(target_folder)

    new_event = attribute_dict_to_dict(event)
    event_id = new_event["args"]["secretHash"]
    file_name = os.path.join(target_folder, f"{event_id}.json")

    with open(file_name, 'w') as f:
        f.write(json.dumps(new_event))

    return new_event


def move_event(event, target_folder):
    if not os.path.exists(target_folder):
        os.makedirs(target_folder)

    event_id = event["args"]["secretHash"]
    src = os.path.join('pending_events', f"{event_id}.json")
    dst = os.path.join(target_folder, f"{event_id}.json")

    shutil.move(src, dst)
    logging.info(f"Moved event file {event_id}.json from 'pending_events' to '{target_folder}'")

# def process_pending_events(maker_wallet_address, chainlink_api_key):
#     pending_events_folder = 'pending_events'
#     if not os.path.exists(pending_events_folder):
#         os.makedirs(pending_events_folder)

#     for event_file in os.listdir(pending_events_folder):
#         with open(os.path.join(pending_events_folder, event_file), 'r') as f:
#             event = json.load(f)
#         handle_DepositCreated(event, maker_wallet_address, chainlink_api_key)
