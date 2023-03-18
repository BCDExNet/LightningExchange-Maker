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
import base64
import configparser
from coingeco_oracle import get_relative_price
from bolt11.core import decode
import lightning_pb2 as lnrpc, lightning_pb2_grpc as lightningstub
import router_pb2 as routerrpc, router_pb2_grpc as routerstub

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

def setup_web3_connection():
    try:
        # Set up connection to Ethereum node
        w3 = Web3(Web3.HTTPProvider(config["provider"]))
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        # Verify connection
        if not w3.isConnected():
            logging.error("Not connected to Ethereum node. Exiting.")
            exit()

        return w3
    except Exception as e:
        errormsg = traceback.format_exc()
        logging.error(f"Failed to set up web3 connection: {str(e)}\n{errormsg}")
        return None

w3 = setup_web3_connection()
if w3 is None:
    logging.error("Unable to set up web3 connection. Exiting.")
    exit()


# Simplify error handling with a wrapper function
def move_event_on_error(error_message, event):
    logging.error(error_message)
    move_event(event, 'error_events')

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
        move_event_on_error("The beneficiary does not match the maker wallet address.", event)
        return True

    token_info = get_token_info(token, config['supported_assets'])
    if token_info is None:
        move_event_on_error("The TOKEN does not match the USDC contract address.", event)
        return True

    oracle_price = get_oracle_price("btc", token_info["name"])
    if oracle_price is None:
        move_event(event, 'error_events')
        return False

    invoice_info = get_invoice_info(invoice)
    if invoice_info is None:
        move_event(event, 'error_events')
        return False

    event_btc_price = calculate_event_btc_price(amount, token_info["decimals"], invoice_info["amount"])
    if not validate_event_btc_price(event_btc_price, oracle_price):
        move_event_on_error("The BTC price in the event is lower than the Chainlink price by more than 1%.", event)
        return False

    if not validate_secret_hash(secret_hash, invoice_info["payment_hash"]):
        move_event_on_error("The secret hash from the event does not match the payment hash in the invoice.", event)
        return False

    if not validate_deadline(deadline):
        move_event_on_error("The deadline in the event is less than 30 minutes from now.", event)
        return False

    if invoice_info.preimage:
        secret = invoice_info.preimage
        logging.info("Preimage already included in the invoice. Skipping payment.")
    else:
        secret = pay_invoice(invoice)

    if secret is None:
        move_event_on_error("Failed to pay invoice and get secret.", event)
        return False

    if not delegate_withdraw(secret, maker_wallet_address):
        move_event_on_error("Failed to call delegateRefund.", event)
        return False

    return True

def get_token_info(token, supported_assets):
    for item in supported_assets:
        if token.lower() == item["address"].lower():
            return item
    return None

def get_oracle_price(base_asset, quote_asset):
    try:
        return get_relative_price(base_asset, quote_asset)
    except Exception as e:
        errormsg = traceback.format_exc()
        logging.error(f"Failed to get price from Chainlink: {str(e)}\n{errormsg}")
    return None

def get_invoice_info(invoice):
    try:
        request = lnrpc.PayReqString(
            pay_req=invoice,
        )
        return stub.DecodePayReq(request)

    except Exception as e:
        errormsg = traceback.format_exc()
        logging.error(f"Failed to decode invoice: {str(e)}\n{errormsg}")
        move_event(event, 'error_events')
        return False

    # try:
    #     decoded_invoice = decode(invoice)
    # return {
    #     "is_mainnet": decoded_invoice.is_mainnet(),
    #     "timestamp": decoded_invoice.timestamp,
    #     "expiry_time": decoded_invoice.expiry_time,
    #     "payment_hash": decoded_invoice.payment_hash,
    #     "amount": decoded_invoice.amount,
    #     "description": decoded_invoice.description
    # }
    # except Exception as e:
    #     errormsg = traceback.format_exc()
    #     logging.error(f"Failed to decode invoice: {str(e)}\n{errormsg}")
    return None

def calculate_event_btc_price(amount, token_decimals, invoice_amount):
    return amount / 10**token_decimals / (invoice_amount / 1e11)

def validate_event_btc_price(event_btc_price, oracle_price):
    return event_btc_price >= oracle_price * 0.99

def validate_secret_hash(event_secret_hash, invoice_secret_hash):
    return event_secret_hash == invoice_secret_hash

def validate_deadline(deadline):
    return deadline >= time.time() + 1800

def pay_invoice(invoice):
    # create macaroon credentials
    macaroon = codecs.encode(open(config['lnd']['macaroon_path'], 'rb').read(), 'hex')

def metadata_callback(context, callback):
    callback([('macaroon', macaroon)], None)
    auth_creds = grpc.metadata_call_credentials(metadata_callback)
    # create SSL credentials
    os.environ['GRPC_SSL_CIPHER_SUITES'] = 'HIGH+ECDSA'
    cert = open(config['lnd']['tls_cert_path'], 'rb').read()
    ssl_creds = grpc.ssl_channel_credentials(cert)
    # combine macaroon and SSL credentials
    combined_creds = grpc.composite_channel_credentials(ssl_creds, auth_creds)
    # make the request
    channel = grpc.secure_channel(config['lnd']['ln_rpc_server'], combined_creds)
    stub = lightningstub.LightningStub(channel)
    rtstub = routerstub.RouterStub(channel)

    try:
        response = stub.SendPaymentSync(lnrpc.SendRequest(payment_request=invoice))
        result = ""
        for response in rtstub.TrackPaymentV2(routerrpc.TrackPaymentRequest(payment_hash=response.payment_hash)):
            secret = response.payment_preimage

        return "0x" + secret.hex()
    except Exception as e:
        errormsg = traceback.format_exc()
        logging.error(f"Failed to pay invoice and get secret: {str(e)}\n{errormsg}")
        return None

def delegate_withdraw(secret, maker_wallet_address):
    global config
    # Set your Ethereum wallet's private key
    bot_address = config["maker_bot_address"]
    bot_private_key = config["maker_bot_privatekey"]
    contract_address = config["contract_address"]
    contract_abi = config["contract_abi"]
    secret = "0x"+secret

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
    return transaction_receipt['status'] == 1

def move_event_on_error(error_message, event):
    logging.error(error_message)
    move_event(event, 'error_events')

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