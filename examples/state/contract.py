from typing import Final
from beaker import (
    Application,
    ApplicationStateValue,
    ReservedApplicationStateValue,
    AccountStateValue,
    ReservedAccountStateValue,
)
from pyteal import abi, TealType, Bytes, Int, Txn

from beaker.state import AccountStateBlob, ApplicationStateBlob


class StateExample(Application):

    declared_app_value: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.bytes,
        default=Bytes(
            "A declared state value that is protected with the `static` flag"
        ),
        descr="A static declared variable, nothing at the protocol level protects it, only the methods defined on ApplicationState do",
        static=True,
    )

    reserved_app_value: Final[
        ReservedApplicationStateValue
    ] = ReservedApplicationStateValue(
        stack_type=TealType.uint64,
        max_keys=32,
        descr="A reserved app state variable, with 32 possible keys",
    )

    application_blob: Final[ApplicationStateBlob] = ApplicationStateBlob(
        keys=16,
    )

    declared_account_value: Final[AccountStateValue] = AccountStateValue(
        stack_type=TealType.uint64,
        default=Int(1),
        descr="An int stored for each account that opts in",
    )

    reserved_account_value: Final[
        ReservedAccountStateValue
    ] = ReservedAccountStateValue(
        stack_type=TealType.bytes,
        max_keys=8,
        descr="A reserved state value, allowing 8 keys to be reserved, in this case byte type",
    )

    account_blob: Final[AccountStateBlob] = AccountStateBlob(keys=3)

    def __init__(self):
        super().__init__(
            name=self.__class__.__qualname__,
            descr=self.__doc__,
            state_class=self.__class__,
        )

        @self.create
        def create():
            return self.initialize_application_state()

        @self.opt_in
        def opt_in():
            return self.initialize_account_state()

        @self.external
        def write_acct_blob(v: abi.String):
            return self.account_blob.write(Int(0), v.get())

        @self.external
        def read_acct_blob(*, output: abi.DynamicBytes):
            return output.set(
                self.account_blob.read(
                    Int(0), self.account_blob.blob.max_bytes - Int(1)
                )
            )

        @self.external
        def write_app_blob(v: abi.String):
            return self.application_blob.write(Int(0), v.get())

        @self.external
        def read_app_blob(*, output: abi.DynamicBytes):
            return output.set(
                self.application_blob.read(
                    Int(0), self.application_blob.blob.max_bytes - Int(1)
                )
            )

        @self.external
        def set_app_state_val(v: abi.String):
            # This will fail, since it was declared as `static` and initialized to a default value during create
            return self.declared_app_value.set(v.get())

        @self.external(read_only=True)
        def get_app_state_val(*, output: abi.String):
            return output.set(self.declared_app_value)

        @self.external
        def set_reserved_app_state_val(k: abi.Uint8, v: abi.Uint64):
            # Accessing the key with square brackets, accepts both Expr and an ABI type
            # If the value is an Expr it must evaluate to `TealType.bytes`
            # If the value is an ABI type, the `encode` method is used to convert it to bytes
            return self.reserved_app_value[k].set(v.get())

        @self.external(read_only=True)
        def get_reserved_app_state_val(k: abi.Uint8, *, output: abi.Uint64):
            return output.set(self.reserved_app_value[k])

        @self.external
        def set_account_state_val(v: abi.Uint64):
            # Accessing with `[Txn.sender()]` is redundant but
            # more clear what is happening
            return self.declared_account_value[Txn.sender()].set(v.get())

        @self.external
        def incr_account_state_val(v: abi.Uint64):
            # Omitting [Txn.sender()] just for demo purposes
            return self.declared_account_value.increment(v.get())

        @self.external(read_only=True)
        def get_account_state_val(*, output: abi.Uint64):
            return output.set(self.declared_account_value[Txn.sender()])

        @self.external
        def set_reserved_account_state_val(k: abi.Uint8, v: abi.String):
            return self.reserved_account_value[k][Txn.sender()].set(v.get())

        @self.external(read_only=True)
        def get_reserved_account_state_val(k: abi.Uint8, *, output: abi.String):
            return output.set(self.reserved_account_value[k][Txn.sender()])


if __name__ == "__main__":
    se = StateExample()
    print(se.approval_program)
