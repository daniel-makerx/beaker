from algosdk.abi import ABIType
from pyteal import Expr, Int, Seq, TealType, abi

from beaker import (
    Application,
    ReservedLocalStateValue,
    client,
    sandbox,
    unconditional_create_approval,
    unconditional_opt_in_approval,
)


# Our custom Struct
class Order(abi.NamedTuple):
    item: abi.Field[abi.String]
    quantity: abi.Field[abi.Uint16]


class StructerState:
    orders = ReservedLocalStateValue(
        stack_type=TealType.bytes,
        max_keys=16,
        prefix="",
    )


structer_app = (
    Application("Structer", state=StructerState())
    .apply(unconditional_create_approval)
    .apply(unconditional_opt_in_approval, initialize_local_state=True)
)


@structer_app.external
def place_order(order_number: abi.Uint8, order: Order) -> Expr:
    return structer_app.state.orders[order_number].set(order.encode())


@structer_app.external(read_only=True)
def read_item(order_number: abi.Uint8, *, output: Order) -> Expr:
    return output.decode(structer_app.state.orders[order_number])


@structer_app.external
def increase_quantity(order_number: abi.Uint8, *, output: Order) -> Expr:
    return Seq(
        # Read the order from state
        (new_order := Order()).decode(structer_app.state.orders[order_number]),
        # Select out in the quantity attribute, its a TupleElement type
        # so needs to be stored somewhere
        (quant := abi.Uint16()).set(new_order.quantity),
        # Add 1 to quantity
        quant.set(quant.get() + Int(1)),
        (item := abi.String()).set(new_order.item),
        # We've gotta set all of the fields at the same time, but we can
        # borrow the item we already know about
        new_order.set(item, quant),
        # Write the new order to state
        structer_app.state.orders[order_number].set(new_order.encode()),
        # Write new order to caller
        output.decode(new_order.encode()),
    )


def demo() -> None:

    # Create a codec from the python sdk
    order_codec = ABIType.from_string(str(Order().type_spec()))

    acct = sandbox.get_accounts().pop()

    # Create an Application client containing both an algod client and my app
    app_client = client.ApplicationClient(
        sandbox.get_algod_client(), structer_app, signer=acct.signer
    )

    # Create the applicatiion on chain, set the app id for the app client
    create_result = app_client.create()
    print(
        f"Created App with id: {app_client.app_id} "
        f"and address addr: {app_client.app_address} "
        f"in tx: {create_result.tx_ids[0]}"
    )

    # Since we're using local state, opt in
    app_client.opt_in()

    # Passing in a dict as an argument that should take a tuple
    # according to the type spec
    order_number = 12
    order = {"quantity": 8, "item": "cubes"}
    app_client.call(place_order, order_number=order_number, order=order)

    # Get the order from the state field
    state_key = order_number.to_bytes(1, "big")
    stored_order = app_client.get_local_state(raw=True)[state_key]
    assert isinstance(stored_order, bytes)
    state_decoded = order_codec.decode(stored_order)

    print(
        "We can get the order we stored from local "
        f"state of the sender: {state_decoded}"
    )

    # Or we could call the read-only method, passing the order number
    result = app_client.call(read_item, order_number=order_number)
    abi_decoded = order_codec.decode(result.raw_value)
    print(f"Decoded result: {abi_decoded}")

    # Update the order to increase the quantity
    result = app_client.call(increase_quantity, order_number=order_number)
    increased_decoded = order_codec.decode(result.raw_value)
    print(
        "Let's add 1 to the struct, update state, and "
        f"return the updated version: {increased_decoded}"
    )

    # And read it back out from state
    state_key = order_number.to_bytes(1, "big")
    stored_order = app_client.get_local_state(raw=True)[state_key]
    assert isinstance(stored_order, bytes)
    state_decoded = order_codec.decode(stored_order)
    print(f"And it's been updated: {state_decoded}")


if __name__ == "__main__":
    demo()
