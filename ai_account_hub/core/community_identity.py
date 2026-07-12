"""Machine-local signing identity for opt-in community submissions.

The private P-256 key is encrypted with Windows DPAPI before it is written to
disk. The public key becomes a pseudonymous installation identifier; no account
or provider authentication data is involved.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


IDENTITY_VERSION = 1
_DPAPI_DESCRIPTION = "AI Account Hub community signing key"


class CommunityIdentityError(RuntimeError):
    """Raised when secure local key storage is unavailable or damaged."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
        buffer,
    )


def _dpapi_protect(cleartext: bytes) -> bytes:
    if os.name != "nt":
        raise CommunityIdentityError(
            "Community sharing currently requires Windows DPAPI secure storage"
        )
    source, source_buffer = _blob(cleartext)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN keeps automatic uploads non-interactive.
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        _DPAPI_DESCRIPTION,
        None,
        None,
        None,
        0x1,
        ctypes.byref(output),
    ):
        raise CommunityIdentityError("Windows could not protect the community signing key")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
        del source_buffer


def _dpapi_unprotect(protected: bytes) -> bytes:
    if os.name != "nt":
        raise CommunityIdentityError(
            "Community sharing currently requires Windows DPAPI secure storage"
        )
    source, source_buffer = _blob(protected)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
    ):
        raise CommunityIdentityError(
            "The community signing key cannot be opened by this Windows user"
        )
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
        del source_buffer


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@dataclass(frozen=True)
class CommunityIdentity:
    """Pseudonymous installation identity and its signing key."""

    installation_id: str
    public_key: str
    _private_key: ec.EllipticCurvePrivateKey

    def sign(self, canonical: bytes) -> str:
        """Return Web Crypto's fixed-width IEEE-P1363 ECDSA signature."""

        der = self._private_key.sign(canonical, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return _b64(raw)


class CommunityIdentityStore:
    """Loads or creates a DPAPI-protected installation key atomically."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load_or_create(self) -> CommunityIdentity:
        if self.path.exists():
            return self._load()
        private_key = ec.generate_private_key(ec.SECP256R1())
        identity = self._identity(private_key)
        private_der = private_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        document = {
            "version": IDENTITY_VERSION,
            "installationId": identity.installation_id,
            "publicKey": identity.public_key,
            "protectedPrivateKey": _b64(_dpapi_protect(private_der)),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
        return identity

    def load(self) -> CommunityIdentity | None:
        return self._load() if self.path.exists() else None

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _load(self) -> CommunityIdentity:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if int(document.get("version") or 0) != IDENTITY_VERSION:
                raise ValueError("unsupported identity version")
            protected = base64.b64decode(
                str(document["protectedPrivateKey"]), validate=True
            )
            private_key = serialization.load_der_private_key(
                _dpapi_unprotect(protected), password=None
            )
            if not isinstance(private_key, ec.EllipticCurvePrivateKey):
                raise ValueError("identity is not an elliptic-curve key")
            identity = self._identity(private_key)
            if document.get("installationId") != identity.installation_id:
                raise ValueError("installation identifier does not match the key")
            if document.get("publicKey") != identity.public_key:
                raise ValueError("public key does not match the private key")
            return identity
        except CommunityIdentityError:
            raise
        except Exception as exc:
            raise CommunityIdentityError(
                "The local community signing identity is invalid or damaged"
            ) from exc

    @staticmethod
    def _identity(private_key: ec.EllipticCurvePrivateKey) -> CommunityIdentity:
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        installation_id = hashlib.sha256(public_der).hexdigest()[:32]
        return CommunityIdentity(installation_id, _b64(public_der), private_key)
