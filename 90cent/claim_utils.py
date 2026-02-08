"""
Claim utilities for Polymarket position redemption.

For Proxy wallets (sig_type=1): batches multiple redeems into a single relayer call
via the proxy wallet's proxy() function which accepts an array of transactions.

For Safe wallets (sig_type=2): redeems individually since Safe relay transactions
go directly to target contracts without proxy wrapping.
"""
import logging
import time

logger = logging.getLogger(__name__)


def batch_redeem(web3_client, positions):
    """
    Redeem multiple positions, using batching when possible.

    For proxy wallets (sig_type=1): all redeems in a single relayer call.
    For safe wallets (sig_type=2): individual redeems with rate-limit handling.

    Args:
        web3_client: A PolymarketGaslessWeb3Client from the polymarket-apis package
        positions: List of dicts with keys: condition_id, amounts (list[float]), neg_risk (bool)

    Returns:
        Transaction receipt (batch) or list of receipts (individual) on success
    """
    if not positions:
        raise ValueError("No positions to redeem")

    # Safe wallets (type 2) can't use proxy() batching - redeem individually
    if getattr(web3_client, 'signature_type', 2) == 2:
        return _redeem_individual(web3_client, positions)

    # Proxy wallets (type 1) - batch all into single proxy() call
    return _redeem_batch_proxy(web3_client, positions)


def _redeem_batch_proxy(web3_client, positions):
    """Batch redeem via proxy wallet's proxy() function (sig_type=1 only)."""
    proxy_txns = []
    for pos in positions:
        condition_id = pos["condition_id"]
        amounts = pos["amounts"]
        neg_risk = pos.get("neg_risk", True)
        int_amounts = [int(a * 1e6) for a in amounts]

        if neg_risk:
            to = web3_client.neg_risk_adapter_address
            data = web3_client._encode_redeem_neg_risk(condition_id, int_amounts)
        else:
            to = web3_client.conditional_tokens_address
            data = web3_client._encode_redeem(condition_id)

        proxy_txns.append({
            "typeCode": 1,
            "to": to,
            "value": 0,
            "data": data,
        })

    encoded_data = web3_client.proxy_factory.encode_abi(
        abi_element_identifier="proxy",
        args=[proxy_txns],
    )

    logger.info("BATCH_REDEEM: Submitting %d redeems in single proxy() call", len(proxy_txns))
    receipt = web3_client._execute(
        web3_client.proxy_factory_address,
        encoded_data,
        f"Batch Redeem ({len(proxy_txns)} positions)",
        metadata="redeem",
    )
    return receipt


def _redeem_individual(web3_client, positions):
    """Redeem positions one at a time (for Safe wallets or as fallback)."""
    receipts = []
    for i, pos in enumerate(positions):
        condition_id = pos["condition_id"]
        amounts = pos["amounts"]
        neg_risk = pos.get("neg_risk", True)

        logger.info("REDEEM: Position %d/%d - %s (neg_risk=%s)",
                     i + 1, len(positions), condition_id[:16], neg_risk)

        receipt = web3_client.redeem_position(
            condition_id=condition_id,
            amounts=amounts,
            neg_risk=neg_risk,
        )
        receipts.append(receipt)

        # Delay between individual redeems to avoid relayer rate limiting
        # The Polymarket relayer enforces ~10s cooldown between transactions
        if i < len(positions) - 1:
            time.sleep(12)

    logger.info("REDEEM: Completed %d/%d positions", len(receipts), len(positions))
    return receipts
