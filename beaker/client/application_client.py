import copy
from base64 import b64decode
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from algokit_utils import (
    ABITransactionResponse,
    Account,
    ApplicationSpecification,
    AppLookup,
)
from algokit_utils import ApplicationClient as AlgokitApplicationClient
from algosdk import transaction
from algosdk.abi import Method
from algosdk.atomic_transaction_composer import (
    ABIResult,
    AtomicTransactionComposer,
    TransactionSigner,
    TransactionWithSigner,
)
from algosdk.transaction import SuggestedParams
from algosdk.v2client.algod import AlgodClient
from algosdk.v2client.indexer import IndexerClient
from pyteal import ABIReturnSubroutine

from beaker.application import Application


class ApplicationClient(AlgokitApplicationClient):
    def __init__(
        self,
        algod_client: AlgodClient,
        app: ApplicationSpecification | str | Path | Application,
        *,
        app_id: int = 0,
        creator: str | Account | None = None,
        indexer_client: IndexerClient | None = None,
        existing_deployments: AppLookup | None = None,
        signer: TransactionSigner | None = None,
        sender: str | None = None,
        suggested_params: SuggestedParams | None = None,
    ):
        app_spec: ApplicationSpecification
        match app:
            case ApplicationSpecification() as compiled_app:
                app_spec = compiled_app
            case Application() as app:
                app_spec = app.build(algod_client)
            case Path() as path:
                if path.is_dir():
                    path = path / "application.json"
                app_spec = ApplicationSpecification.from_json(
                    path.read_text(encoding="utf8")
                )
            case str():
                app_spec = ApplicationSpecification.from_json(app)
            case _:
                raise Exception(f"Unexpected app type: {app}")
        if creator is None:
            super().__init__(
                algod_client,
                app_spec,
                app_id=app_id,
                signer=signer,
                sender=sender,
                suggested_params=suggested_params,
            )
        else:
            super().__init__(
                algod_client,
                app_spec,
                creator=creator,
                indexer_client=indexer_client,
                existing_deployments=existing_deployments,
                signer=signer,
                sender=sender,
                suggested_params=suggested_params,
            )

    @property
    def client(self) -> AlgodClient:
        return self.algod_client

    @property
    def app_addr(self) -> str:
        return self.app_address

    def get_sender(
        self, sender: str | None = None, signer: TransactionSigner | None = None
    ) -> str:
        signer, sender = self._resolve_signer_sender(signer, sender)
        return sender

    def get_signer(self, signer: TransactionSigner | None = None) -> TransactionSigner:
        signer, sender = self._resolve_signer_sender(signer, None)
        return signer

    def get_suggested_params(
        self,
        sp: transaction.SuggestedParams | None = None,
    ) -> transaction.SuggestedParams:

        if sp is not None:
            return sp

        if self.suggested_params is not None:
            return self.suggested_params

        return self.client.suggested_params()

    def add_transaction(
        self, atc: AtomicTransactionComposer, txn: transaction.Transaction
    ) -> AtomicTransactionComposer:
        if self.signer is None:
            raise Exception("No signer available")

        atc.add_transaction(TransactionWithSigner(txn=txn, signer=self.signer))
        return atc

    def call(  # type: ignore[override]
        self,
        method: Method | ABIReturnSubroutine | str,
        sender: str | None = None,
        signer: TransactionSigner | None = None,
        suggested_params: transaction.SuggestedParams | None = None,
        on_complete: transaction.OnComplete = transaction.OnComplete.NoOpOC,
        note: bytes | None = None,
        lease: bytes | None = None,
        rekey_to: str | None = None,
        accounts: list[str] | None = None,
        foreign_apps: list[int] | None = None,
        foreign_assets: list[int] | None = None,
        boxes: Sequence[tuple[int, bytes | bytearray | str | int]] | None = None,
        atc: AtomicTransactionComposer | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> ABIResult:
        if not atc:
            atc = AtomicTransactionComposer()
        super().compose_call(
            atc,
            abi_method=method.method_spec()
            if isinstance(method, ABIReturnSubroutine)
            else method,
            args=kwargs,
            sender=sender,
            signer=signer,
            suggested_params=suggested_params,
            on_complete=on_complete,
            note=note,
            lease=lease,
            accounts=accounts,
            foreign_apps=foreign_apps,
            foreign_assets=foreign_assets,
            boxes=boxes,
        )
        result = self._execute_atc_tr(atc)
        assert isinstance(result, ABITransactionResponse)
        return result.abi_result

    def fund(self, amt: int, addr: str | None = None) -> str:
        """convenience method to pay the address passed, defaults to paying the app address for this client from the current signer"""
        sender = self.get_sender()
        signer = self.get_signer()

        sp = self.client.suggested_params()

        rcv = self.app_addr if addr is None else addr

        atc = AtomicTransactionComposer()
        atc.add_transaction(
            TransactionWithSigner(
                txn=transaction.PaymentTxn(sender, sp, rcv, amt),
                signer=signer,
            )
        )
        atc.execute(self.client, 4)
        return atc.tx_ids.pop()

    def get_application_account_info(self) -> dict[str, Any]:
        """gets the account info for the application account"""
        return cast(dict[str, Any], self.client.account_info(self.app_addr))

    def get_box_names(self) -> list[bytes]:
        box_resp = cast(dict[str, Any], self.client.application_boxes(self.app_id))
        return [b64decode(box["name"]) for box in box_resp["boxes"]]

    def get_box_contents(self, name: bytes) -> bytes:
        contents = cast(
            dict[str, Any], self.client.application_box_by_name(self.app_id, name)
        )
        return b64decode(contents["value"])

    def prepare(
        self,
        signer: TransactionSigner | None = None,
        sender: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> "ApplicationClient":
        """makes a copy of the current ApplicationClient and the fields passed"""

        ac = copy.copy(self)
        signer, sender = self._resolve_signer_sender(signer, sender)
        ac.signer = signer
        ac.sender = sender
        ac.__dict__.update(**kwargs)
        return ac
