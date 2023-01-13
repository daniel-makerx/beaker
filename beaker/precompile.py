import base64
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, List
from pyteal import (
    Seq,
    Bytes,
    Expr,
    ScratchVar,
    TealType,
    TealTypeError,
    TealInputError,
    Int,
    Concat,
    Len,
    Substring,
    Suffix,
    Subroutine,
    Sha512_256,
    TxnField,
    TxnType,
)
from algosdk.v2client.algod import AlgodClient
from algosdk.source_map import SourceMap
from algosdk.future.transaction import LogicSigAccount
from algosdk.constants import APP_PAGE_MAX_SIZE
from algosdk.atomic_transaction_composer import LogicSigTransactionSigner
from beaker.consts import PROGRAM_DOMAIN_SEPARATOR, num_extra_program_pages
from beaker.lib.strings import encode_uvarint

if TYPE_CHECKING:
    from beaker.application import Application
    from beaker.logic_signature import LogicSignature, RuntimeTemplateVariable


#: The opcode that should be present just before the byte template variable
PUSH_BYTES = "pushbytes"
#: The opcode that should be present just before the uint64 template variable
PUSH_INT = "pushint"

#: The zero value for byte type
ZERO_BYTES = '""'
#: The zero value for uint64 type
ZERO_INT = "0"


@dataclass
class PrecompileTemplateValue:
    #: The name of the template variable
    name: str = field(kw_only=True)
    #: Whether or not this variable is bytes (if false, its uint64)
    is_bytes: bool = field(kw_only=True)
    #: The line number in the source TEAL this variable is present
    line: int = field(kw_only=True)
    #: The pc of the variable in the assembled bytecode
    pc: int = 0


class Program:
    """
    Precompile takes a TEAL program and handles its compilation. Used by AppPrecompile
    and LSigPrecompile for Applications and Logic Signature programs, respectively.
    """

    def __init__(self, program: str):
        self.teal = program
        self.raw_binary: bytes | None = None
        self.binary_hash: str | None = None
        self.source_map: SourceMap | None = None

    def assemble(self, client: AlgodClient) -> None:
        """
        Fully compile the program source to binary and generate a
        source map for matching pc to line number
        """
        result = client.compile(self.teal, source_map=True)
        self.raw_binary = base64.b64decode(result["result"])
        self.binary_hash = result["hash"]
        self.source_map = SourceMap(result["sourcemap"])

    @cached_property
    def binary(self) -> Bytes:
        assert self.raw_binary
        return Bytes(self.raw_binary)

    @cached_property
    def assertions(self) -> dict[int, "ProgramAssertion"]:
        assert self.source_map is not None
        return _gather_asserts(self.teal, self.source_map)


class AppProgram(Program):
    @property
    def program_pages(self) -> list[Expr]:
        assert self.raw_binary is not None
        return [
            Bytes(self.raw_binary[i : i + APP_PAGE_MAX_SIZE])
            for i in range(0, len(self.raw_binary), APP_PAGE_MAX_SIZE)
        ]


class LSigProgram(Program):
    def __init__(
        self,
        program: str,
        *,
        runtime_template_variables: List["RuntimeTemplateVariable"] | None = None,
    ):
        self._template_values: list[PrecompileTemplateValue] = []
        if runtime_template_variables:
            lines = program.splitlines()
            # Replace the teal program TMPL_* template variables with
            # the 0 value for the given type and save the list of TemplateValues
            for rtt_var in runtime_template_variables:
                token = rtt_var.token
                is_bytes = rtt_var.type_of() == TealType.bytes
                op = PUSH_BYTES if is_bytes else PUSH_INT
                statement = f"{op} {token} // {token}"
                idx = lines.index(statement)
                lines[idx] = lines[idx].replace(
                    token, ZERO_BYTES if is_bytes else ZERO_INT, 1
                )
                tv = PrecompileTemplateValue(
                    name=rtt_var.name, is_bytes=is_bytes, line=idx
                )
                self._template_values.append(tv)

            program = "\n".join(lines)

        super().__init__(program=program)

    def assemble(self, client: AlgodClient) -> None:
        super().assemble(client)
        assert self.source_map is not None

        for tv in self._template_values:
            # +1 to acount for the pushbytes/pushint op
            tv.pc = self.source_map.get_pcs_for_line(tv.line)[0] + 1

    def hash(self) -> Expr:
        """hash returns an expression for this Precompile.
        It will fail if any template_values are set.
        """
        if self.binary_hash is None:
            raise TealInputError("No address defined for precompile")

        from algosdk.encoding import decode_address

        return Bytes(decode_address(self.binary_hash))

    def populate_template(self, *args: str | bytes | int) -> bytes:
        """
        populate_template returns the bytes resulting from patching the set of
        arguments passed into the blank binary

        The args passed should be of the same type and in the same order as the
        template values declared.
        """

        assert self.raw_binary is not None
        assert len(self._template_values) > 0
        assert len(args) == len(self._template_values)

        # Get a copy of the binary so we can work on it in place
        populated_binary = list(self.raw_binary)
        # Any time we add bytes, we need to update the offset so the rest
        # of the pc values can be updated to account for the difference
        offset = 0
        for idx, tv in enumerate(self._template_values):
            arg: str | bytes | int = args[idx]

            if tv.is_bytes:
                if type(arg) is int:
                    raise TealTypeError(type(arg), bytes | str)

                if type(arg) is str:
                    arg = arg.encode("utf-8")

                assert type(arg) is bytes

                # Bytes are encoded as uvarint(len(bytes)) + bytes
                curr_val = py_encode_uvarint(len(arg)) + arg
            else:
                if type(arg) is not int:
                    raise TealTypeError(type(arg), int)
                # Ints are just the uvarint encoded number
                curr_val = py_encode_uvarint(arg)

            # update the working buffer to include the new value,
            # replacing the current 0 value
            populated_binary[tv.pc + offset : tv.pc + offset + 1] = curr_val

            # update the offset with the length(value) - 1 to account
            # for the existing 0 value and help keep track of how to shift the pc later
            offset += len(curr_val) - 1

        return bytes(populated_binary)

    def populate_template_expr(self, *args: Expr) -> Expr:
        """
        populate_template_expr returns the Expr that will patch a
        blank binary given a set of arguments.

        It is called by ``template_address`` to return a Expr that
        can be used to compare with a sender given some arguments.
        """

        # To understand how this works, first look at the pure python one above
        # it should produce an identical output in terms of populated binary.
        # This function just reproduces the same effects in pyteal

        assert self.raw_binary is not None
        assert len(self._template_values)
        assert len(args) == len(self._template_values)

        populate_program: list[Expr] = [
            (last_pos := ScratchVar(TealType.uint64)).store(Int(0)),
            (offset := ScratchVar(TealType.uint64)).store(Int(0)),
            (curr_val := ScratchVar(TealType.bytes)).store(Bytes("")),
            (buff := ScratchVar(TealType.bytes)).store(Bytes("")),
        ]

        for idx, tv in enumerate(self._template_values):
            # Add expressions to encode the values and insert
            # them into the working buffer
            populate_program += [
                curr_val.store(Concat(encode_uvarint(Len(args[idx])), args[idx]))
                if tv.is_bytes
                else curr_val.store(encode_uvarint(args[idx])),
                buff.store(
                    Concat(
                        buff.load(),
                        Substring(
                            self.binary,
                            last_pos.load(),
                            Int(tv.pc),
                        ),
                        curr_val.load(),
                    )
                ),
                offset.store(offset.load() + Len(curr_val.load()) - Int(1)),
                last_pos.store(Int(tv.pc) + Int(1)),
            ]

        # append the bytes from the last template variable to the end
        populate_program += [
            buff.store(Concat(buff.load(), Suffix(self.binary, last_pos.load()))),
            buff.load(),
        ]

        @Subroutine(TealType.bytes)
        def populate_template_program() -> Expr:
            return Seq(*populate_program)

        return populate_template_program()

    def template_hash(self, *args) -> Expr:  # type: ignore
        """
        returns an expression that will generate the expected
        hash given some set of values that should be included in the logic itself
        """
        return Sha512_256(
            Concat(Bytes(PROGRAM_DOMAIN_SEPARATOR), self.populate_template_expr(*args))
        )


class AppPrecompile:
    """
    AppPrecompile allows a smart contract to signal that some child Application
    should be fully compiled prior to constructing its own program.
    """

    def __init__(self, app: "Application"):
        #: The App to be used and compiled before it's parent
        self.app = app
        #: The App's approval program as a Precompile
        self.approval = AppProgram("")
        #: The App's clear program as a Precompile
        self.clear = AppProgram("")

    def compile(self, client: AlgodClient) -> None:
        """fully compile this app precompile by recursively
            compiling children depth first

        Note:
            Must be called (even indirectly) prior to using
                the ``approval`` and ``clear`` fields
        """
        for p in self.app.precompiles.values():
            p.compile(client)

        # at this point, we should have all the dependant logic built
        # so we can compile the app teal
        approval, clear = self.app.compile(client)
        self.approval = AppProgram(approval)
        self.clear = AppProgram(clear)
        self.approval.assemble(client)
        self.clear.assemble(client)

    def get_create_config(self) -> dict[TxnField, Expr | list[Expr]]:
        """get a dictionary of the fields and values that should be set when
        creating this application that can be passed directly to
        the InnerTxnBuilder.Execute method
        """
        assert self.approval.raw_binary is not None
        assert self.clear.raw_binary is not None
        return {
            TxnField.type_enum: TxnType.ApplicationCall,
            TxnField.local_num_byte_slices: Int(self.app.acct_state.num_byte_slices),
            TxnField.local_num_uints: Int(self.app.acct_state.num_uints),
            TxnField.global_num_byte_slices: Int(self.app.app_state.num_byte_slices),
            TxnField.global_num_uints: Int(self.app.app_state.num_uints),
            TxnField.approval_program_pages: self.approval.program_pages,
            TxnField.clear_state_program_pages: self.clear.program_pages,
            TxnField.extra_program_pages: Int(
                num_extra_program_pages(self.approval.raw_binary, self.clear.raw_binary)
            ),
        }


class LSigPrecompile:
    """
    LSigPrecompile allows a smart contract to signal that some child Logic Signature
    should be fully compiled prior to constructing its own program.
    """

    def __init__(self, lsig: "LogicSignature"):
        #: the LogicSignature to be used and compiled before it's parent
        self.lsig: "LogicSignature" = lsig

        #: The LogicSignature's logic as a Precompile
        self.logic = LSigProgram(
            lsig.program, runtime_template_variables=lsig.template_variables
        )

    def compile(self, client: AlgodClient) -> None:
        """
        fully compile this lsig precompile by recursively compiling children depth first

        Note:
            Must be called (even indirectly) prior to using the ``logic`` field
        """
        self.logic.assemble(client)

    def template_signer(self, *args: str | bytes | int) -> LogicSigTransactionSigner:
        """Get the Signer object for a populated version of the template contract"""
        return LogicSigTransactionSigner(
            LogicSigAccount(self.logic.populate_template(*args))
        )

    def signer(self) -> LogicSigTransactionSigner:
        """
        signer returns a LogicSigTransactionSigner to be used with
        an ApplicationClient or AtomicTransactionComposer.

        It should only be used for non templated Precompiles.
        """
        return LogicSigTransactionSigner(LogicSigAccount(self.logic.raw_binary))


@dataclass
class ProgramAssertion:
    line: int
    message: str


def _gather_asserts(program: str, src_map: SourceMap) -> dict[int, ProgramAssertion]:
    asserts: dict[int, ProgramAssertion] = {}

    program_lines = program.split("\n")
    for idx, line in enumerate(program_lines):
        # Take only the first chunk before spaces
        line, *_ = line.split(" ")
        if line != "assert":
            continue

        pc = src_map.get_pcs_for_line(idx)[0]

        # TODO: this will be wrong for multiline comments
        line_before = program_lines[idx - 1]
        if not line_before.startswith("//"):
            continue

        asserts[pc] = ProgramAssertion(idx, line_before.strip("// "))

    return asserts


def py_encode_uvarint(integer: int) -> bytes:
    """Encodes an integer as an uvarint.
    :param integer: the integer to encode
    :return: bytes containing the integer encoded as an uvarint
    """

    def to_byte(integer: int) -> int:
        return integer & 0b1111_1111

    buffer: bytearray = bytearray()

    while integer >= 0b1000_0000:
        buffer.append(to_byte(integer) | 0b1000_0000)
        integer >>= 7

    buffer.append(to_byte(integer))

    return bytes(buffer)
