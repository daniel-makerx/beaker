from pathlib import Path
from typing import Any, cast

from algokit_utils.application_client import (
    ApplicationClient as AlgokitApplicationClient,
)
from algokit_utils.application_specification import (
    ApplicationSpecification,
)
from algosdk.atomic_transaction_composer import (
    TransactionSigner,
)
from algosdk.transaction import SuggestedParams
from algosdk.v2client.algod import AlgodClient

from beaker.application import Application


class ApplicationClient(AlgokitApplicationClient):
    def __init__(
        self,
        client: AlgodClient,
        app: ApplicationSpecification | str | Path | Application,
        app_id: int = 0,
        signer: TransactionSigner | None = None,
        sender: str | None = None,
        suggested_params: SuggestedParams | None = None,
    ):
        app_spec = app.build(client) if isinstance(app, Application) else app
        super().__init__(client, app_spec, app_id, signer, sender, suggested_params)

    def get_sender(
        self, sender: str | None = None, signer: TransactionSigner | None = None
    ) -> str:
        signer, sender = self._resolve_signer_sender(signer, sender)
        return sender

    def get_signer(self, signer: TransactionSigner | None = None) -> TransactionSigner:
        signer, sender = self._resolve_signer_sender(signer, None)
        return signer

    def prepare(
        self,
        signer: TransactionSigner | None = None,
        sender: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> "ApplicationClient":
        return cast("ApplicationClient", super().prepare(signer, sender, **kwargs))
