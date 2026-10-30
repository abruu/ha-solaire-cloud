"""Client for the Solaire Cloud public share endpoint.

The share page encrypts its requests with AES-128-CBC; the key and IV are supplied by the user and are
never part of this repository. Error messages never contain the share code, key or IV.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.parse
import uuid
from datetime import datetime
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

BASE = "https://www.solairecloud.com"
PATH = "/mesh/web/v1/public/share/display/houseHold/screen/getUserOverview"
REQUEST_TIMEOUT = 15


class SolaireError(Exception):
    """Base error."""


class SolaireConnectionError(SolaireError):
    """The server could not be reached or answered with an HTTP error."""


class SolaireApiError(SolaireError):
    """The server answered but refused the request (expired or wrong share code, or wrong key)."""


class SolaireCryptoError(SolaireError):
    """The response could not be decrypted (wrong AES key/IV, or the site changed)."""


def parse_share(value: str | None) -> str | None:
    """Share code from a full share link or a bare code; None if neither."""
    value = (value or "").strip()
    m = re.search(r"shareCode=([A-Za-z0-9]{8,64})", value) or re.fullmatch(r"([A-Za-z0-9]{8,64})", value)
    return m.group(1) if m else None


def valid_key(value: str | None) -> bool:
    """AES-128 key or IV: exactly 16 bytes."""
    return isinstance(value, str) and len(value.encode()) == 16


class SolaireClient:
    """Fetches the overview of one shared site."""

    def __init__(self, session: aiohttp.ClientSession, share_code: str, key: str, iv: str) -> None:
        if not valid_key(key) or not valid_key(iv):
            raise ValueError("AES key and IV must be 16 characters")
        self._session = session
        self._share_code = share_code
        self._key = key.encode()
        self._iv = iv.encode()

    def _encrypt(self, text: str) -> str:
        padder = padding.PKCS7(128).padder()
        enc = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).encryptor()
        data = enc.update(padder.update(text.encode()) + padder.finalize()) + enc.finalize()
        return base64.b64encode(data).decode()

    def _decrypt(self, b64: str) -> str:
        try:
            dec = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).decryptor()
            unpadder = padding.PKCS7(128).unpadder()
            data = unpadder.update(dec.update(base64.b64decode(b64)) + dec.finalize()) + unpadder.finalize()
            return data.decode()
        except ValueError as err:  # bad padding, bad base64 or bad utf-8
            raise SolaireCryptoError("response could not be decrypted") from err

    async def fetch(self, now: datetime) -> dict[str, Any]:
        """Return the `result` object of getUserOverview for the local date of `now`."""
        body = {
            "curDate": now.strftime("%Y-%m-%d"),
            "year": now.strftime("%Y"),
            "month": now.strftime("%m"),
            "day": now.strftime("%d"),
        }
        query = urllib.parse.urlencode(
            {
                "data": self._encrypt(
                    urllib.parse.urlencode({"timeStamp": int(time.time() * 1000), "requestId": uuid.uuid4().hex})
                )
            }
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) HomeAssistant-SolaireCloud",
            "shareCode": self._share_code,
            "sharePageCode": "1000",
            "crytoVer": "1",
            "deviceType": "3",
            "source": "0",
            "Lang": "en_US",
            "queryTime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "Origin": BASE,
            "Referer": BASE + "/",
        }
        payload = json.dumps({"data": self._encrypt(json.dumps(body, separators=(",", ":")))})
        try:
            async with self._session.post(
                f"{BASE}{PATH}?{query}",
                data=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                outer = json.loads(await resp.text())
        except aiohttp.ClientResponseError as err:
            raise SolaireConnectionError(f"HTTP {err.status} from Solaire Cloud") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SolaireConnectionError(f"network error ({type(err).__name__})") from err
        except ValueError as err:
            raise SolaireApiError("response was not JSON") from err

        if isinstance(outer, dict) and isinstance(outer.get("data"), str):
            try:
                outer = json.loads(self._decrypt(outer["data"]))
            except ValueError as err:
                raise SolaireCryptoError("decrypted response was not JSON") from err
        if not isinstance(outer, dict) or not outer.get("success") or not isinstance(outer.get("result"), dict):
            code = outer.get("code") if isinstance(outer, dict) else "?"
            raise SolaireApiError(f"API error code={code}")
        return outer["result"]
