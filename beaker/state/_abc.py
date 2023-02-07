from abc import ABC, abstractmethod
from typing import Literal, NamedTuple

from pyteal import TealType, Expr


class AppSpecSchemaFragment(NamedTuple):
    section: str
    data: dict


class StateStorage(ABC):
    @abstractmethod
    def app_spec_json(self) -> AppSpecSchemaFragment | None:
        ...

    @abstractmethod
    def num_keys(self) -> int:
        ...

    @abstractmethod
    def value_type(self) -> Literal[TealType.bytes, TealType.uint64]:
        ...


class ApplicationStateStorage(StateStorage):
    @abstractmethod
    def initialize(self) -> Expr | None:
        ...


class AccountStateStorage(StateStorage):
    @abstractmethod
    def initialize(self, acct: Expr) -> Expr | None:
        ...


# class BoxStorage(ABC):
#     pass
