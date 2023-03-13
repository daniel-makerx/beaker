import base64
import random

from algosdk import v2client
from pyteal import (
    App,
    Assert,
    Bytes,
    Concat,
    Expr,
    Extract,
    ExtractUint64,
    Global,
    If,
    Int,
    Itob,
    Log,
    Or,
    Return,
    ScratchVar,
    Seq,
    Subroutine,
    Suffix,
    TealType,
    abi,
)

from beaker import (
    Application,
    BuildOptions,
    GlobalStateValue,
    client,
    consts,
    sandbox,
    unconditional_create_approval,
)
from beaker.lib.math import Max


class SortedIntegersState:
    elements = GlobalStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        descr="The number of elements in the array",
    )


_box_name = "sorted_ints"
_box_size = 1024 * 4
_max_ints = _box_size // 8

BoxName = Bytes(_box_name)
BoxSize = Int(_box_size)
MaxInts = Int(_max_ints)

sorted_ints_app = Application(
    "SortedIntegers",
    build_options=BuildOptions(avm_version=8),
    state=SortedIntegersState(),
).apply(unconditional_create_approval)


@sorted_ints_app.external
def add_int(val: abi.Uint64, *, output: abi.DynamicArray[abi.Uint64]) -> Expr:
    return Seq(
        array_contents := App.box_get(BoxName),
        Assert(Or(Int(0), Int(1))),
        # figure out the correct index
        # Write the new array with the contents
        (idx := ScratchVar()).store(
            If(
                sorted_ints_app.state.elements == Int(0),
                Int(0),
                binary_search(
                    val.get(),
                    array_contents.value(),
                    Int(0),
                    sorted_ints_app.state.elements - Int(1),
                )
                * Int(8),
            )
        ),
        App.box_put(
            BoxName,
            # Take the bytes that would fit in the box
            insert_element(
                array_contents.value(),
                val.encode(),
                idx.load(),
            ),
        ),
        sorted_ints_app.state.elements.increment(),
        Log(Itob(Global.opcode_budget())),
        output.decode(
            # Prepend the bytes with the number of elements as a uint16,
            # according to ABI spec
            Concat(
                Suffix(Itob(Int(10)), Int(6)),
                App.box_extract(BoxName, Int(0), Int(8) * Int(10)),
            )
        ),
    )


@Subroutine(TealType.uint64)
def binary_search(val: Expr, arr: Expr, start: Expr, end: Expr) -> Expr:
    # Python equivalent:
    # def binary_search(arr, val, start, end):
    #     if start > end:
    #         return start
    #
    #     if start == end:
    #         if arr[start] > val:
    #             return start
    #         return start + 1
    #
    #     mid = (start + end) // 2
    #
    #     if arr[mid] < val:
    #         return binary_search(arr, val, mid + 1, end)
    #     elif arr[mid] > val:
    #         return binary_search(arr, val, start, mid - 1)
    #     else:
    #         return mid

    return Seq(
        If(start > end, Return(start)),
        If(
            start == end,
            Return(start + If(lookup_element(arr, start) > val, Int(0), Int(1))),
        ),
        (mididx := ScratchVar()).store((start + end) / Int(2)),
        (midval := ScratchVar()).store(lookup_element(arr, mididx.load())),
        If(midval.load() < val)
        .Then(
            binary_search(val, arr, mididx.load() + Int(1), end),
        )
        .ElseIf(midval.load() > val)
        .Then(
            binary_search(val, arr, start, Max(Int(1), mididx.load()) - Int(1)),
        )
        .Else(mididx.load()),
    )


def lookup_element(buff: Expr, idx: Expr) -> Expr:
    return ExtractUint64(buff, idx * Int(8))


def insert_element(buff: Expr, new_val: Expr, pos: Expr) -> Expr:
    return Concat(
        Extract(buff, Int(0), pos),
        new_val,
        # extract from pos -> max len of box leaving off
        Extract(buff, pos, (BoxSize - pos) - Int(8)),
    )


@sorted_ints_app.external
def box_create_test() -> Expr:
    return Seq(
        Assert(App.box_create(BoxName, BoxSize)),
        sorted_ints_app.state.elements.set(Int(0)),
    )


#
# Util funcs
#
def decode_int(b: str) -> int:
    return int.from_bytes(base64.b64decode(b), "big")


def decode_budget(tx_info: dict) -> int:
    return decode_int(tx_info["logs"][0])


def get_box(
    app_id: int, name: bytes, algod_client: v2client.algod.AlgodClient
) -> list[int]:
    box_contents = algod_client.application_box_by_name(app_id, name)

    vals = []
    data = base64.b64decode(box_contents["value"])
    for idx in range(len(data) // 8):
        vals.append(int.from_bytes(data[idx * 8 : (idx + 1) * 8], "big"))

    return vals


def demo() -> None:
    acct = sandbox.get_accounts().pop()

    app_client = client.ApplicationClient(
        sandbox.get_algod_client(), sorted_ints_app, signer=acct.signer
    )

    # Create && fund app acct
    app_client.create()
    app_client.fund(100 * consts.algo)
    print(f"AppID: {app_client.app_id}  AppAddr: {app_client.app_address}")

    # Create 4 box refs since we need to touch 4k
    boxes = [(app_client.app_id, _box_name)] * 4

    # Make App Create box
    result = app_client.call(
        box_create_test,
        boxes=boxes,
    )

    # Shuffle 0-511
    nums = list(range(512))
    random.shuffle(nums)
    budgets = []
    for idx, n in enumerate(nums):
        if idx % 32 == 0:
            print(f"Iteration {idx}: {n}")

        result = app_client.call(
            add_int,
            val=n,
            boxes=boxes,
        )

        budgets.append(decode_budget(result.tx_info))

    print(f"Budget left after each insert: {budgets}")

    # Get contents of box
    box = get_box(app_client.app_id, _box_name.encode(), app_client.client)
    # Make sure its sorted
    assert box == sorted(box)


if __name__ == "__main__":
    demo()
