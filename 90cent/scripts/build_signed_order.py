"""
Utility to build and sign Polymarket CTF orders using py_order_utils.

Usage example:
python scripts/build_signed_order.py ^
    --token-id 0xabc... ^
    --side BUY ^
    --price 0.45 ^
    --size 10
"""

import argparse
import json
import os
import random
import time
from typing import Dict

from dotenv import load_dotenv

import config

from py_order_utils.builders import OrderBuilder
from py_order_utils.model.order import OrderData
from py_order_utils.model.sides import BUY, SELL
from py_order_utils.model.signatures import EOA, POLY_PROXY
from py_order_utils.signer import Signer

from py_clob_client.client import ClobClient
from py_clob_client.order_builder.helpers import (
    decimal_places,
    round_down,
    round_normal,
    round_up,
    to_token_decimals,
)

load_dotenv()

ROUNDING_RULES: Dict[str, Dict[str, int]] = {
    "0.1": {"price": 1, "size": 2, "amount": 3},
    "0.01": {"price": 2, "size": 2, "amount": 4},
    "0.001": {"price": 3, "size": 2, "amount": 5},
    "0.0001": {"price": 4, "size": 2, "amount": 6},
}

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _get_tick_size(clob: ClobClient, token_id: str) -> str:
    try:
        tick = clob.get_tick_size(token_id)
        return str(tick)
    except Exception:
        return "0.001"


def _get_rounding_rule(tick_size: str) -> Dict[str, int]:
    return ROUNDING_RULES.get(str(tick_size), ROUNDING_RULES["0.001"])


def _calculate_amounts(side: str, size: float, price: float, rounding: Dict[str, int]):
    rounded_price = round_normal(price, rounding["price"])

    if side == "BUY":
        raw_taker = round_down(size, rounding["size"])
        raw_maker = raw_taker * rounded_price
        if decimal_places(raw_maker) > rounding["amount"]:
            raw_maker = round_up(raw_maker, rounding["amount"] + 4)
            if decimal_places(raw_maker) > rounding["amount"]:
                raw_maker = round_down(raw_maker, rounding["amount"])

        maker_amount = to_token_decimals(raw_maker)
        taker_amount = to_token_decimals(raw_taker)
        return BUY, maker_amount, taker_amount

    raw_maker = round_down(size, rounding["size"])
    raw_taker = raw_maker * rounded_price
    if decimal_places(raw_taker) > rounding["amount"]:
        raw_taker = round_up(raw_taker, rounding["amount"] + 4)
        if decimal_places(raw_taker) > rounding["amount"]:
            raw_taker = round_down(raw_taker, rounding["amount"])

    maker_amount = to_token_decimals(raw_maker)
    taker_amount = to_token_decimals(raw_taker)
    return SELL, maker_amount, taker_amount


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and sign a Polymarket CTF order payload"
    )
    parser.add_argument("--token-id", required=True, help="CTF token id for YES/NO leg")
    parser.add_argument("--side", choices=["BUY", "SELL"], required=True)
    parser.add_argument("--price", type=float, required=True, help="Price between 0-1")
    parser.add_argument("--size", type=float, required=True, help="Number of shares")
    parser.add_argument("--fee-bps", type=int, default=0, help="Maker fee in basis points")
    parser.add_argument(
        "--nonce",
        type=int,
        default=None,
        help="Optional nonce (defaults to random 32-bit integer)",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=900,
        help="Time-to-live in seconds (expiration = now + ttl, 0 for GTC)",
    )
    parser.add_argument(
        "--taker",
        default=ZERO_ADDRESS,
        help="Specify taker address for private orders",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write the signed order JSON",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not config.POLYMARKET_PRIVATE_KEY:
        raise SystemExit("POLYMARKET_PRIVATE_KEY missing in environment")

    maker_address = (
        config.POLYMARKET_PROXY_ADDRESS or config.POLYMARKET_WALLET_ADDRESS
    )
    if not maker_address:
        raise SystemExit("POLYMARKET_WALLET_ADDRESS missing in environment")

    signature_type = (
        config.POLYMARKET_SIGNATURE_TYPE
        if config.POLYMARKET_SIGNATURE_TYPE is not None
        else EOA
    )

    signer = Signer(config.POLYMARKET_PRIVATE_KEY)
    builder = OrderBuilder(
        exchange_address=config.POLYMARKET_EXCHANGE_ADDRESS,
        chain_id=config.POLYMARKET_CHAIN_ID,
        signer=signer,
    )

    clob_client = ClobClient(
        host=config.POLYMARKET_API_URL,
        chain_id=config.POLYMARKET_CHAIN_ID,
        key=config.POLYMARKET_PRIVATE_KEY,
    )
    tick_size = _get_tick_size(clob_client, args.token_id)
    rounding = _get_rounding_rule(tick_size)

    order_side, maker_amount, taker_amount = _calculate_amounts(
        args.side, args.size, args.price, rounding
    )

    nonce = args.nonce if args.nonce is not None else random.getrandbits(32)
    expiration = 0
    if args.ttl > 0:
        expiration = int(time.time()) + int(args.ttl)

    if signature_type == POLY_PROXY and not config.POLYMARKET_PROXY_ADDRESS:
        raise SystemExit(
            "POLYMARKET_PROXY_ADDRESS must be set when using proxy signature type"
        )

    order = builder.build_signed_order(
        OrderData(
            maker=maker_address,
            taker=args.taker,
            tokenId=str(int(args.token_id, 16) if args.token_id.startswith("0x") else int(args.token_id)),
            makerAmount=str(maker_amount),
            takerAmount=str(taker_amount),
            side=order_side,
            feeRateBps=str(max(args.fee_bps, 0)),
            nonce=str(nonce),
            signer=signer.address(),
            expiration=str(expiration),
            signatureType=signature_type,
        )
    )

    payload = order.dict()
    output = json.dumps(payload, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(output)
        print(f"Signed order written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()










