import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType, ApiCreds
from py_clob_client.exceptions import PolyApiException


def main():
    creds = None
    if (
        config.POLYMARKET_API_KEY
        and config.POLYMARKET_API_SECRET
        and config.POLYMARKET_API_PASSPHRASE
    ):
        creds = ApiCreds(
            api_key=config.POLYMARKET_API_KEY,
            api_secret=config.POLYMARKET_API_SECRET,
            api_passphrase=config.POLYMARKET_API_PASSPHRASE,
        )
    private_key = (config.POLYMARKET_PRIVATE_KEY or "").strip()
    if private_key and not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    client = ClobClient(
        config.POLYMARKET_API_URL,
        chain_id=config.POLYMARKET_CHAIN_ID,
        key=private_key,
        creds=creds,
        signature_type=config.POLYMARKET_SIGNATURE_TYPE,
        funder=config.POLYMARKET_PROXY_ADDRESS or None,
    )

    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)

    try:
        print(client.get_balance_allowance(params))
    except PolyApiException as exc:
        if exc.status_code == 401:
            derived = client.derive_api_key()
            client.set_api_creds(derived)
            print(client.get_balance_allowance(params))
        else:
            raise


if __name__ == "__main__":
    main()

