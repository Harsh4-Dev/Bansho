"""A software WebAuthn authenticator for tests.

Builds real ES256 attestation ("none" format) and assertion responses with
the ``cryptography`` package and CBOR, exactly as a platform authenticator
would, so py_webauthn verifies them end to end: RP ID hash, flags, signature
counter, COSE public key, clientDataJSON origin/challenge/type, signature.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

UP, UV, AT = 0x01, 0x04, 0x40


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SoftAuthenticator:
    def __init__(self, rp_id: str = "bansho.test", origin: str = "https://bansho.test"):
        self.rp_id = rp_id
        self.origin = origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.sign_count = 0
        self.user_handle: bytes | None = None

    @property
    def id(self) -> str:
        return b64u(self.cred_id)

    def _cose(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})

    def create(self, options: dict[str, Any], *, origin: str | None = None, rp_id: str | None = None,
               flags: int = UP | UV | AT, challenge: str | None = None) -> dict[str, Any]:
        self.user_handle = unb64u(options["user"]["id"])
        client_data = json.dumps({
            "type": "webauthn.create",
            "challenge": challenge or options["challenge"],
            "origin": origin or self.origin,
            "crossOrigin": False,
        }).encode()
        auth_data = (
            hashlib.sha256((rp_id or self.rp_id).encode()).digest()
            + bytes([flags])
            + self.sign_count.to_bytes(4, "big")
            + bytes(16)  # AAGUID
            + len(self.cred_id).to_bytes(2, "big")
            + self.cred_id
            + self._cose()
        )
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": self.id,
            "rawId": self.id,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64u(client_data),
                "attestationObject": b64u(att),
                "transports": ["internal", "hybrid"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def get(self, options: dict[str, Any], *, origin: str | None = None, rp_id: str | None = None,
            flags: int = UP | UV, counter: int | None = None, challenge: str | None = None) -> dict[str, Any]:
        if counter is None:
            self.sign_count += 1
            counter = self.sign_count
        client_data = json.dumps({
            "type": "webauthn.get",
            "challenge": challenge or options["challenge"],
            "origin": origin or self.origin,
            "crossOrigin": False,
        }).encode()
        auth_data = hashlib.sha256((rp_id or self.rp_id).encode()).digest() + bytes([flags]) + counter.to_bytes(4, "big")
        sig = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        resp: dict[str, Any] = {
            "clientDataJSON": b64u(client_data),
            "authenticatorData": b64u(auth_data),
            "signature": b64u(sig),
        }
        if self.user_handle is not None:
            resp["userHandle"] = b64u(self.user_handle)
        return {
            "id": self.id,
            "rawId": self.id,
            "type": "public-key",
            "response": resp,
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }
