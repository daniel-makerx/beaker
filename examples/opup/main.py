from hashlib import sha256

from algosdk import transaction
from algosdk.atomic_transaction_composer import (
    TransactionWithSigner,
)

from beaker.client import ApplicationClient
from beaker.consts import milli_algo
from beaker.sandbox import get_accounts, get_algod_client
from examples.opup.contract import expensive_app, hash_it

client = get_algod_client()

acct = get_accounts().pop()


def demo() -> None:
    # Create an Application client containing both an algod client and my app
    sp = client.suggested_params()
    # we need to cover 255 inner transactions + ours
    sp.flat_fee = True
    sp.fee = 256 * milli_algo
    app_client = ApplicationClient(
        client, expensive_app, signer=acct.signer, suggested_params=sp
    )

    # Create the applicatiion on chain, set the app id for the app client
    create_result = app_client.create()
    print(
        f"Created App with id: {create_result.app_id} and "
        f"address addr: {create_result.app_address} in "
        f"tx: {create_result.transaction_id}"
    )

    txn = TransactionWithSigner(
        txn=transaction.PaymentTxn(
            acct.address, sp, create_result.app_address, int(1e6)
        ),
        signer=acct.signer,
    )
    result = app_client.call("opup_bootstrap", ptxn=txn)
    print(f"Created op up app: {result.return_value}")

    input = "stuff"
    iters = 10

    # You can compose calls if you pre-defined your ATC
    # atc = AtomicTransactionComposer()
    # app_client.add_method_call(atc, app.hash_it, input=input, iters=iters)
    # result = atc.execute(client, 4)

    result = app_client.call(hash_it, input=input, iters=iters)
    result_hash = bytes(result.return_value)

    local_hash = input.encode()
    for _ in range(iters):
        local_hash = sha256(local_hash).digest()

    assert (
        result_hash == local_hash
    ), f"Expected {local_hash.decode()} got {result_hash.decode()}"

    print(f"Successfully hashed {input},  {iters} times to produce {result_hash.hex()}")


if __name__ == "__main__":
    demo()
