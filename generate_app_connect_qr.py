#!/usr/bin/env python3
"""
Generate an App Connect QR code payload compatible with Sources/PiStatusBar/PiStatusBarApp.swift.

Usage:
  ./generate_app_connect_qr.py <baseURL> <token> [tlsCertSHA256]

Example:
  ./generate_app_connect_qr.py \
    "https://100.64.0.1:8788" \
    "my-token" \
    "AA:BB:CC:..."
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def insecure_fallback_base_url(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "http://" + base_url[len("https://") :]
    return base_url


def build_payload(base_url: str, token: str, tls_cert_sha256: str) -> dict[str, str]:
    payload: dict[str, str] = {
        "baseURL": base_url,
        "token": token,
        "insecureFallbackBaseURL": insecure_fallback_base_url(base_url),
    }
    if tls_cert_sha256:
        payload["tlsCertSHA256"] = tls_cert_sha256
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate App Connect QR code PNG")
    parser.add_argument("baseURL", help="Base URL, e.g. https://100.64.0.1:8788")
    parser.add_argument("token", help="Bearer token")
    parser.add_argument("tlsCertSHA256", nargs="?", default="", help="Optional TLS cert SHA256 fingerprint")
    args = parser.parse_args()

    base_url = args.baseURL.strip()
    token = args.token.strip()
    tls = args.tlsCertSHA256.strip()

    if not base_url:
        print("Error: baseURL must not be empty", file=sys.stderr)
        return 2
    if not token:
        print("Error: token must not be empty", file=sys.stderr)
        return 2

    payload = build_payload(base_url, token, tls)
    qr_text = json.dumps(payload, separators=(",", ":"))

    try:
        import qrcode
    except ImportError:
        print("Missing dependency: qrcode", file=sys.stderr)
        print("Install with: pip install 'qrcode[pil]'", file=sys.stderr)
        print("Payload JSON:")
        print(qr_text)
        return 1

    img = qrcode.make(qr_text)
    out_path = Path.cwd() / "app_connect_qr.png"
    img.save(out_path)

    print(f"Wrote QR image: {out_path}")
    print("Payload JSON:")
    print(qr_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
