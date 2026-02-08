import os
import logging
from web3 import Web3
from eth_account import Account
import requests

logger = logging.getLogger(__name__)

# Polygon USDC (USDC.e)
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
]

class PolymarketGaslessWeb3Client:
    """
    web3 client for USDC transfers.
    Note: This replacement uses standard gas transactions (requires MATIC), not meta-transactions.
    """
    def __init__(self, private_key, signature_type=None, chain_id=137, rpc_url=None):
        self.private_key = private_key
        if not rpc_url:
            rpc_url = os.getenv("WEB3_PROVIDER_URI", "https://polygon-rpc.com")

        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        try:
            self.account = Account.from_key(private_key)
            self.address = self.account.address
        except Exception as e:
            logger.error(f"Invalid private key: {e}")
            self.address = None

        self.usdc_contract = self.w3.eth.contract(address=USDC_ADDRESS, abi=USDC_ABI)
        self.chain_id = chain_id

    def get_usdc_balance(self, address=None):
        if not address:
            address = self.address
        if not address:
            return 0.0

        try:
            balance_wei = self.usdc_contract.functions.balanceOf(address).call()
            return balance_wei / 1e6  # USDC has 6 decimals
        except Exception as e:
            logger.error(f"Failed to get USDC balance: {e}")
            return 0.0

    def transfer_usdc(self, recipient, amount):
        """
        Transfer USDC to recipient.
        Amount is in USDC (e.g. 1.50).
        """
        if not self.address:
            logger.error("No valid account for transfer")
            return None

        try:
            amount_wei = int(amount * 1e6)
            nonce = self.w3.eth.get_transaction_count(self.address)

            # Build transaction
            tx = self.usdc_contract.functions.transfer(
                recipient,
                amount_wei
            ).build_transaction({
                'chainId': self.chain_id,
                'gas': 100000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': nonce,
            })

            # Sign transaction
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)

            # Send transaction
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            logger.info(f"USDC Transfer sent: {tx_hash.hex()}")

            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            return receipt

        except Exception as e:
            logger.error(f"USDC Transfer failed: {e}")
            return None

class PolymarketDataClient:
    """Mock/Replacement for DataClient"""
    def __init__(self):
        pass

    def get_positions(self, address, redeemable=False):
        # Placeholder
        return []
