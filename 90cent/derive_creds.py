import json
import os

from dotenv import load_dotenv
from py_clob_client.client import ClobClient


def main():
    load_dotenv()
    key = os.environ.get("POLYMARKET_PRIVATE_KEY")
    wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS")

    if not key:
        raise SystemExit("POLYMARKET_PRIVATE_KEY not set")

    key = key.strip()
    if not key.startswith("0x"):
        key = f"0x{key}"

    client = ClobClient("https://clob.polymarket.com", chain_id=137, key=key)
    creds = client.create_or_derive_api_creds()

    print(
        json.dumps(
            {
                "api_key": creds.api_key,
                "api_secret": creds.api_secret,
                "api_passphrase": creds.api_passphrase,
                "wallet_address": wallet,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

