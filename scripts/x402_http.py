#!/usr/bin/env python3
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib import error, request

import x402


def _normalize_hex(value: str) -> str:
    if value.startswith("0x"):
        return value[2:].lower()
    return value.lower()


class _X402Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        payload = json.loads(body.decode("utf-8")) if body else {}
        server = cast(Any, self.server)
        resource = self.path or "/"
        x_payment = self.headers.get("X-PAYMENT")

        def send_challenge(error_message: str | None = None, payment_response: dict | None = None):
            response = server.accepts_builder(payload, resource)
            if error_message:
                response = dict(response)
                response["error"] = error_message
            self.send_response(402)
            self.send_header("Content-Type", "application/json")
            if payment_response is not None:
                encoded = x402.encode_x_payment_response(payment_response)
                self.send_header("X-PAYMENT-RESPONSE", encoded)
            self.end_headers()
            self.wfile.write(json.dumps(response).encode("utf-8"))

        if not x_payment:
            send_challenge("X-PAYMENT header is required")
            return

        try:
            payment_payload = x402.decode_x_payment(x_payment)
        except Exception:
            send_challenge("Invalid X-PAYMENT header")
            return

        accepts = server.accepts_builder(payload, resource).get("accepts", [])
        accept = accepts[0] if accepts else {}
        scheme = accept.get("scheme")
        network = accept.get("network")
        max_amount = accept.get("maxAmountRequired")
        pay_to = accept.get("payTo")
        asset = accept.get("asset")

        if payment_payload.get("x402Version") != 1:
            send_challenge("Unsupported x402 version")
            return
        if payment_payload.get("scheme") != scheme:
            send_challenge("Unsupported payment scheme")
            return
        if payment_payload.get("network") != network:
            send_challenge("Unsupported network")
            return

        inner = payment_payload.get("payload") if isinstance(payment_payload, dict) else None
        if not isinstance(inner, dict):
            send_challenge("Invalid payment payload")
            return
        signature = inner.get("signature")
        authorization = inner.get("authorization")
        if not signature or not isinstance(authorization, dict):
            send_challenge("Missing authorization")
            return

        auth_from = authorization.get("from")
        auth_to = authorization.get("to")
        auth_value = authorization.get("value")
        auth_valid_after = authorization.get("validAfter")
        auth_valid_before = authorization.get("validBefore")
        auth_nonce = authorization.get("nonce")
        if (
            not auth_from
            or not auth_to
            or not auth_value
            or auth_valid_after is None
            or auth_valid_before is None
            or not auth_nonce
        ):
            send_challenge("Authorization fields missing")
            return
        if pay_to and str(auth_to).lower() != str(pay_to).lower():
            send_challenge("Authorization payee mismatch")
            return
        if max_amount is not None and int(auth_value) < int(max_amount):
            send_challenge(
                "Insufficient authorization amount",
                payment_response={
                    "success": False,
                    "transaction": None,
                    "network": network,
                    "payer": auth_from,
                    "errorReason": "Insufficient authorization amount",
                },
            )
            return

        now = int(time.time())
        try:
            if int(auth_valid_after) > now:
                send_challenge(
                    "Authorization not yet valid",
                    payment_response={
                        "success": False,
                        "transaction": None,
                        "network": network,
                        "payer": auth_from,
                        "errorReason": "Authorization not yet valid",
                    },
                )
                return
            if int(auth_valid_before) <= now:
                send_challenge(
                    "Authorization expired",
                    payment_response={
                        "success": False,
                        "transaction": None,
                        "network": network,
                        "payer": auth_from,
                        "errorReason": "Authorization expired",
                    },
                )
                return
        except Exception:
            send_challenge("Invalid authorization validity")
            return

        nonce_key = f"{auth_from.lower()}:{str(auth_nonce).lower()}"
        if nonce_key in server.used_nonces:
            send_challenge(
                "Nonce already used",
                payment_response={
                    "success": False,
                    "transaction": None,
                    "network": network,
                    "payer": auth_from,
                    "errorReason": "Nonce already used",
                },
            )
            return

        try:
            normalized_auth = {
                "from": auth_from,
                "to": auth_to,
                "value": int(auth_value),
                "validAfter": int(auth_valid_after),
                "validBefore": int(auth_valid_before),
                "nonce": auth_nonce,
            }
            signer = x402.recover_authorization_signer(
                server.chain_id,
                asset,
                normalized_auth,
                signature,
                name=server.token_name,
                version=server.token_version,
            )
        except Exception:
            send_challenge(
                "Invalid signature",
                payment_response={
                    "success": False,
                    "transaction": None,
                    "network": network,
                    "payer": auth_from,
                    "errorReason": "Invalid signature",
                },
            )
            return

        if signer.lower() != str(auth_from).lower():
            send_challenge(
                "Invalid signature",
                payment_response={
                    "success": False,
                    "transaction": None,
                    "network": network,
                    "payer": auth_from,
                    "errorReason": "Invalid signature",
                },
            )
            return

        try:
            result = server.payment_executor(payload, payment_payload, accept)
        except Exception as exc:
            send_challenge(
                f"Settlement failed: {exc}",
                payment_response={
                    "success": False,
                    "transaction": None,
                    "network": network,
                    "payer": auth_from,
                    "errorReason": "Settlement failed",
                },
            )
            return

        server.used_nonces.add(nonce_key)
        payment_response = {
            "success": True,
            "transaction": result.get("transaction"),
            "network": network,
            "payer": auth_from,
            "errorReason": None,
        }
        body = (
            server.resource_handler(payload, result)
            if server.resource_handler
            else {"status": "ok"}
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-PAYMENT-RESPONSE", x402.encode_x_payment_response(payment_response))
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def log_message(self, format, *args):
        return


class X402Server:
    def __init__(
        self,
        host="127.0.0.1",
        port=0,
        *,
        accepts_builder=None,
        payment_executor=None,
        resource_handler=None,
        chain_id=31337,
        token_name="MockUSDC",
        token_version="1",
    ):
        if accepts_builder is None:
            raise ValueError("accepts_builder is required")
        if payment_executor is None:
            raise ValueError("payment_executor is required")
        self._server: Any = ThreadingHTTPServer((host, port), _X402Handler)
        self._server.accepts_builder = accepts_builder
        self._server.payment_executor = payment_executor
        self._server.resource_handler = resource_handler
        self._server.used_nonces = set()
        self._server.chain_id = chain_id
        self._server.token_name = token_name
        self._server.token_version = token_version
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self):
        address = self._server.server_address
        host = address[0]
        port = address[1]
        return f"http://{host}:{port}"

    def start(self):
        self._thread.start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()


def _decode_payment_response(headers) -> dict | None:
    header_value = headers.get("X-PAYMENT-RESPONSE")
    if not header_value:
        return None
    try:
        return x402.decode_x_payment(header_value)
    except Exception:
        return None


def request_payment_challenge(base_url: str, payload: dict, resource: str = "/request") -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url}{resource}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code == 402:
            body = exc.read().decode("utf-8")
            return json.loads(body)
        raise


def send_payment(
    base_url: str,
    payload: dict,
    x_payment_header: str,
    resource: str = "/request",
) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url}{resource}",
        data=data,
        headers={"Content-Type": "application/json", "X-PAYMENT": x_payment_header},
        method="POST",
    )
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
            payment_response = _decode_payment_response(response.headers)
            return {"status": response.status, "body": body, "payment_response": payment_response}
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else "{}"
        payment_response = _decode_payment_response(exc.headers or {})
        return {
            "status": exc.code,
            "body": json.loads(body) if body else {},
            "payment_response": payment_response,
        }
