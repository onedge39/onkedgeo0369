from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

import requests


DEFAULT_CREDENTIALS_PATH = Path(__file__).with_name("demo_credentials.local.yaml")


@dataclass(frozen=True)
class KalshiCredentials:
    api_key_id: str
    private_key_pem: str
    base_url: str


def _strip_optional_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _extract_private_key_pem(raw_text: str) -> str:
    lines = raw_text.splitlines()
    stack = []
    blocks = []

    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped.startswith("-----BEGIN ") and stripped.endswith("-----"):
            label = stripped[len("-----BEGIN ") : -5]
            stack.append((label, index))
        elif stripped.startswith("-----END ") and stripped.endswith("-----"):
            label = stripped[len("-----END ") : -5]
            match_index = None
            for stack_index in range(len(stack) - 1, -1, -1):
                if stack[stack_index][0] == label:
                    match_index = stack_index
                    break
            if match_index is None:
                continue

            _, start_index = stack.pop(match_index)
            block_lines = [line.strip() for line in lines[start_index : index + 1] if line.strip()]
            inner_lines = block_lines[1:-1]
            has_nested_markers = any(
                inner.startswith("-----BEGIN ") or inner.startswith("-----END ")
                for inner in inner_lines
            )
            blocks.append(
                {
                    "label": label,
                    "pem": "\n".join(block_lines) + "\n",
                    "has_nested_markers": has_nested_markers,
                }
            )

    for block in reversed(blocks):
        if not block["has_nested_markers"] and "PRIVATE KEY" in block["label"]:
            return block["pem"]

    for block in reversed(blocks):
        if "PRIVATE KEY" in block["label"]:
            return block["pem"]

    raise ValueError("No PEM private key block found in credentials file")


def load_demo_credentials(path: Path = DEFAULT_CREDENTIALS_PATH) -> KalshiCredentials:
    if not path.exists():
        raise FileNotFoundError(f"Missing credentials file: {path}")

    raw_text = path.read_text(encoding="utf-8")
    api_key_id: Optional[str] = None
    base_url: Optional[str] = None

    for raw_line in raw_text.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        if line.startswith("api_key_id:"):
            api_key_id = _strip_optional_quotes(line.split(":", 1)[1])
        elif line.startswith("base_url:"):
            base_url = _strip_optional_quotes(line.split(":", 1)[1])
    private_key_pem = _extract_private_key_pem(raw_text).strip()

    if not api_key_id:
        raise ValueError(f"api_key_id is empty in {path}")
    if not base_url:
        raise ValueError(f"base_url is empty in {path}")
    if "PRIVATE KEY" not in private_key_pem:
        raise ValueError(f"private_key_pem in {path} does not look like a PEM private key")

    return KalshiCredentials(
        api_key_id=api_key_id,
        private_key_pem=private_key_pem + "\n",
        base_url=base_url.rstrip("/"),
    )


def sign_pss_sha256_base64(private_key_pem: str, message: str) -> str:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as key_file:
        key_file.write(private_key_pem)
        key_path = key_file.name

    try:
        os.chmod(key_path, 0o600)
        cmd = [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            key_path,
            "-sigopt",
            "rsa_padding_mode:pss",
            "-sigopt",
            "rsa_pss_saltlen:digest",
        ]
        result = subprocess.run(
            cmd,
            input=message.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        return base64.b64encode(result.stdout).decode("ascii")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"openssl signing failed: {stderr or exc}") from exc
    finally:
        try:
            os.remove(key_path)
        except FileNotFoundError:
            pass


def build_auth_headers(
    credentials: KalshiCredentials,
    method: str,
    path: str,
    timestamp_ms: Optional[int] = None,
) -> Dict[str, str]:
    method = method.upper()
    path_without_query = path.split("?", 1)[0]
    timestamp_ms = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    timestamp_str = str(timestamp_ms)
    payload = f"{timestamp_str}{method}{path_without_query}"
    signature = sign_pss_sha256_base64(credentials.private_key_pem, payload)
    return {
        "KALSHI-ACCESS-KEY": credentials.api_key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_str,
    }


def send_demo_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
) -> requests.Response:
    credentials = load_demo_credentials(credentials_path)
    url = f"{credentials.base_url}{path}"
    signed_path = urlparse(url).path
    headers = build_auth_headers(credentials, method, signed_path)
    response = requests.request(
        method=method.upper(),
        url=url,
        headers=headers,
        params=params,
        timeout=timeout,
    )
    return response
