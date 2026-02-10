#!/usr/bin/env python3
import base64
import hashlib
import json

from eth_account import Account
from eth_account.messages import encode_structured_data

from eth_account import Account
from eth_account.messages import encode_defunct


def canonical_json(payload: dict) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def request_hash(payload: dict) -> bytes:
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).digest()
    return digest


def quote_id(
    web3,
    request_hash_bytes: bytes,
    amount: int,
    token: str,
    payee: str,
    expiry: int,
    nonce: bytes,
) -> bytes:
    return web3.solidity_keccak(
        ["bytes32", "uint256", "address", "address", "uint256", "bytes32"],
        [request_hash_bytes, amount, token, payee, expiry, nonce],
    )


def sign_quote(account, quote_id_bytes: bytes) -> bytes:
    return account.sign_message(encode_defunct(quote_id_bytes)).signature


def verify_quote(account, quote_id_bytes: bytes, signature: bytes) -> bool:
    recovered = Account.recover_message(
        encode_defunct(quote_id_bytes), signature=signature
    )
    return recovered.lower() == account.address.lower()


def build_receipt(
    tx_hash: str,
    session_id: int,
    request_hash_bytes: bytes,
    quote_id_bytes: bytes,
    expiry: int,
) -> dict:
    return {
        "tx_hash": tx_hash,
        "session_id": session_id,
        "request_hash": request_hash_bytes.hex(),
        "quote_id": quote_id_bytes.hex(),
        "expiry": expiry,
    }


def build_eip3009_typed_data(
    chain_id: int,
    token_address: str,
    authorization: dict,
    *,
    name: str = "MockUSDC",
    version: str = "1",
) -> dict:
    message = dict(authorization)
    message["value"] = int(message["value"])
    message["validAfter"] = int(message["validAfter"])
    message["validBefore"] = int(message["validBefore"])
    nonce = message.get("nonce")
    if isinstance(nonce, str):
        value = nonce[2:] if nonce.startswith("0x") else nonce
        nonce_bytes = bytes.fromhex(value)
    elif isinstance(nonce, bytearray):
        nonce_bytes = bytes(nonce)
    elif isinstance(nonce, bytes):
        nonce_bytes = nonce
    else:
        nonce_bytes = int(nonce).to_bytes(32, "big")
    if len(nonce_bytes) != 32:
        raise ValueError("nonce must be 32 bytes")
    message["nonce"] = nonce_bytes
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": name,
            "version": version,
            "chainId": chain_id,
            "verifyingContract": token_address,
        },
        "message": message,
    }


def recover_authorization_signer(
    chain_id: int,
    token_address: str,
    authorization: dict,
    signature: str,
    *,
    name: str = "MockUSDC",
    version: str = "1",
) -> str:
    typed_data = build_eip3009_typed_data(
        chain_id, token_address, authorization, name=name, version=version
    )
    signer = Account.recover_message(
        encode_structured_data(typed_data), signature=signature
    )
    return signer


def encode_x_payment(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return base64.b64encode(raw).decode("utf-8")


def decode_x_payment(header_value: str) -> dict:
    raw = base64.b64decode(header_value.encode("utf-8"))
    return json.loads(raw.decode("utf-8"))


def encode_x_payment_response(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return base64.b64encode(raw).decode("utf-8")


def split_signature(signature_hex: str) -> tuple[int, bytes, bytes]:
    value = signature_hex
    if value.startswith("0x"):
        value = value[2:]
    raw = bytes.fromhex(value)
    if len(raw) != 65:
        raise ValueError("invalid signature length")
    r = raw[:32]
    s = raw[32:64]
    v = raw[64]
    return v, r, s
