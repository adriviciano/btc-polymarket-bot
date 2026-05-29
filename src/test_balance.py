"""
Simple script to test Polymarket balance retrieval.
"""
import os
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds

load_dotenv()

def main():
    # Load configuration
    host = "https://clob.polymarket.com"
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    api_key = os.getenv("POLYMARKET_API_KEY")
    api_secret = os.getenv("POLYMARKET_API_SECRET")
    api_passphrase = os.getenv("POLYMARKET_API_PASSPHRASE")
    signature_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1"))
    funder = os.getenv("POLYMARKET_FUNDER", "")
    
    print("=" * 70)
    print("POLYMARKET BALANCE TEST")
    print("=" * 70)
    print(f"Host: {host}")
    print(f"Signature Type: {signature_type}")
    print(f"Private Key: {'✓' if private_key else '✗'}")
    print(f"API Key: {'✓' if api_key else '✗'}")
    print(f"API Secret: {'✓' if api_secret else '✗'}")
    print(f"API Passphrase: {'✓' if api_passphrase else '✗'}")
    print("=" * 70)
    
    try:
        # Create client (CLOB V2 — chain_id is still chain_id, not "chain")
        print("\n1. Creating ClobClient (CLOB V2)...")
        client = ClobClient(
            host,
            chain_id=137,
            key=private_key,
            signature_type=signature_type,
            funder=funder or None
        )
        print("   ✓ Client created")

        # Derive credentials from private key (V2: create_or_derive_api_key)
        print("\n2. Deriving API credentials from private key...")
        creds = client.create_or_derive_api_key()
        client.set_api_creds(creds)
        print(f"   ✓ API Key: {creds.api_key}")
        print(f"   ✓ Credentials configured")

        # Get wallet address
        print("\n3. Getting wallet address...")
        address = client.get_address()
        print(f"   ✓ Address: {address}")

        # Get balance - COLLATERAL (pUSD). In CLOB V2 you must sync the
        # balance/allowance first, otherwise the CLOB may report a stale $0.
        print("\n4. Getting pUSD balance (COLLATERAL)...")
        try:
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=signature_type
            )

            print("   → Syncing balance/allowance with the CLOB (V2)...")
            try:
                client.update_balance_allowance(params)
                print("   ✓ Sync requested")
            except Exception as sync_err:
                print(f"   ⚠️ Sync failed (continuing): {sync_err}")

            result = client.get_balance_allowance(params)
            print(f"   Response type: {type(result)}")
            print(f"   Response: {result}")
            
            if isinstance(result, dict):
                # Response should have 'balance' and 'allowance'
                balance_raw = result.get("balance", "0")
                balance_wei = float(balance_raw)
                # USDC has 6 decimals
                balance_usdc = balance_wei / 1_000_000
                
                print(f"\n   Balance raw: {balance_raw}")
                print(f"   Balance in wei: {balance_wei}")
                print(f"   💰 BALANCE pUSD: ${balance_usdc:.6f}")

                # Verify balance directly on blockchain
                print("\n5. Verifying balance directly on Polygon...")
                try:
                    from web3 import Web3
                    # Connect to Polygon
                    w3 = Web3(Web3.HTTPProvider('https://polygon-rpc.com'))

                    # pUSD (Polymarket USD) collateral token on Polygon (CLOB V2).
                    # Verified against py_clob_client_v2.config.get_contract_config(137).collateral
                    # and docs.polymarket.com/resources/contracts. Replaces legacy USDC.e.
                    pusd_address = Web3.to_checksum_address('0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB')

                    # Minimal ABI for balanceOf
                    erc20_abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]

                    pusd_contract = w3.eth.contract(address=pusd_address, abi=erc20_abi)

                    # Check the funder/proxy wallet if set, otherwise the signer address.
                    # In V2 collateral typically lives in the deposit/proxy wallet, not the EOA.
                    target_address = Web3.to_checksum_address(funder) if funder else Web3.to_checksum_address(address)

                    # Get real balance (pUSD has 6 decimals)
                    balance_onchain = pusd_contract.functions.balanceOf(target_address).call()
                    balance_onchain_usdc = balance_onchain / 1_000_000

                    print(f"   🔗 pUSD on-chain ({target_address}): ${balance_onchain_usdc:.6f}")

                except Exception as e:
                    print(f"   ⚠️ Could not verify on-chain: {e}")
            else:
                print(f"\n   ⚠️ Unexpected response: {result}")
        except Exception as e:
            print(f"   ✗ Error: {e}")
            import traceback
            traceback.print_exc()
        
        print("\n" + "=" * 70)
        print("TEST COMPLETED")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
