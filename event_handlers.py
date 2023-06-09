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
        if not w3.is_connected():
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


macaroon = codecs.encode(open(config['lnd']['macaroon_path'], 'rb').read(), 'hex')
def metadata_callback(context, callback):
    callback([('macaroon', macaroon)], None)
auth_creds = grpc.metadata_call_credentials(metadata_callback)
os.environ['GRPC_SSL_CIPHER_SUITES'] = 'HIGH+ECDSA'
cert = open(config['lnd']['tls_cert_path'], 'rb').read()
ssl_creds = grpc.ssl_channel_credentials(cert)
combined_creds = grpc.composite_channel_credentials(ssl_creds, auth_creds)
channel = grpc.secure_channel(config['lnd']['ln_rpc_server'], combined_creds)
stub = lightningstub.LightningStub(channel)
rtstub = routerstub.RouterStub(channel)


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
    contract_address = event["address"]
    maker_wallet_address = config['maker_wallet_address']

    isNative = check_if_native_coin(contract_address)

    logging.info(f"Event received: DepositCreated, secretHash: {secret_hash}, depositor: {depositor}, beneficiary: {beneficiary}, token: {token}, amount: {amount}, deadline: {deadline}, invoice: {invoice}")

    if beneficiary.lower() != maker_wallet_address.lower():
        log_event_on_error("The beneficiary does not match the maker wallet address.", event)
        return True

    token_info = get_token_info(token, config['supported_assets'])
    if token_info is None:
        log_event_on_error(f"Doesn't support this TOKEN {token}.", event)
        return True

    oracle_price = get_oracle_price("btc", token_info["name"])
    if oracle_price is None:
        log_event_on_error("Failed to get price from oracle.", event)
        return False

    invoice_info = get_invoice_info(invoice)
    if invoice_info is None:
        log_event_on_error("Can't to decode invoice.", event)
        return False

    if invoice_info.num_satoshis > 50000:
        log_event_on_error("The invoice amount over the max amount 50,000 sats.", event)
        return False

    event_btc_price = calculate_event_btc_price(amount, token_info["decimals"], invoice_info.num_satoshis)
    logging.info(f"Oracle price is {oracle_price}, Order price is {event_btc_price}")
    if not validate_event_btc_price(event_btc_price, oracle_price):
        log_event_on_error("The BTC price in the event is lower than the Chainlink price.", event)
        return False

    if not validate_secret_hash(secret_hash, invoice_info.payment_hash):
        log_event_on_error("The secret hash from the event does not match the payment hash in the invoice.", event)
        return False

    if not validate_deadline(deadline):
        log_event_on_error("The deadline in the event is less than 30 minutes from now.", event)
        return False

    secret = pay_invoice(invoice)
    if secret is None:
        log_event_on_error("Failed to pay invoice and get secret.", event)
        return False

    if not delegate_withdraw(secret, maker_wallet_address, isNative):
        log_event_on_error("Failed to call delegateRefund.", event)
        return False

    return True

def check_if_native_coin(contract_address):
    native_contract_address = config["native_contract_address"].lower()
    token_contract_address = config["token_contract_address"].lower()
    if contract_address.lower() == native_contract_address:
        return True
    if contract_address.lower() == token_contract_address:
        return False
    return None

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

    return None

def calculate_event_btc_price(amount, token_decimals, invoice_amount):
    return amount / 10**token_decimals / (invoice_amount / 1e8)

def validate_event_btc_price(event_btc_price, oracle_price):
    return event_btc_price >= oracle_price

def validate_secret_hash(event_secret_hash, invoice_secret_hash):
    return event_secret_hash == invoice_secret_hash

def validate_deadline(deadline):
    return deadline >= time.time() + 1800

def pay_invoice(invoice):
    try:
        response = stub.SendPaymentSync(lnrpc.SendRequest(payment_request=invoice))
        result = ""
        for response in rtstub.TrackPaymentV2(routerrpc.TrackPaymentRequest(payment_hash=response.payment_hash)):
            secret = response.payment_preimage
            logging.info(f"{response.payment_hash}")
        
        return "0x" + str(secret)
    except Exception as e:
        errormsg = traceback.format_exc()
        logging.error(f"Failed to pay invoice and get secret: {str(e)}\n{errormsg}")
        return None

def delegate_withdraw(secret, maker_wallet_address, isNative):
    global config
    bot_address = config["maker_bot_address"]
    bot_private_key = config["maker_bot_privatekey"]
    contract_address = config["token_contract_address"]
    contract_abi = config["token_contract_abi"]

    if isNative:
        contract_address = config["native_contract_address"]
        contract_abi = config["native_contract_abi"]

    with open(contract_abi, "r") as abi_file:
        token_contract_abi = json.load(abi_file)

    contract_instance = w3.eth.contract(address=contract_address, abi=token_contract_abi)

    # Get the nonce for the transaction
    nonce = w3.eth.get_transaction_count(bot_address)

    # Estimate the gas limit
    gas_limit = int(contract_instance.functions.delegateWithdraw(secret, maker_wallet_address).estimate_gas() * 1.3)

    # Build the transaction dictionary
    transaction = contract_instance.functions.delegateWithdraw(secret, maker_wallet_address).build_transaction({
        'gas': gas_limit,
        'gasPrice': w3.eth.gas_price,
        'nonce': nonce
    })
    # transaction = contract_instance.encodeABI(fn_name="delegateWithdraw", args=[secret, maker_wallet_address])
    # Sign the transaction
    signed_transaction = w3.eth.account.sign_transaction(transaction, bot_private_key)

    # Send the transaction
    transaction_hash = w3.eth.send_raw_transaction(signed_transaction.rawTransaction)

    logging.info(f"sending withdraw transaction {transaction_hash.hex()}")

    # Wait for the transaction receipt
    transaction_receipt = w3.eth.wait_for_transaction_receipt(transaction_hash)

    # Check if the transaction was successful
    return transaction_receipt['status'] == 1

def log_event_on_error(error_message, event):
    new_event = attribute_dict_to_dict(event)
    event_id = new_event["args"]["secretHash"]
    logging.error(f"[{event_id}] : {error_message}")

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

def check_event_exists(event, target_folder):
    new_event = attribute_dict_to_dict(event)
    event_id = new_event["args"]["secretHash"]
    file_name = os.path.join(target_folder, f"{event_id}.json")

    return os.path.exists(file_name)

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
