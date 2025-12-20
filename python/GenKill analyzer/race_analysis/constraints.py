from __future__ import annotations
from typing import List, Tuple

from abstract_syntax_tree import *


def enforce_no_spawn_await_in_if_while(stmt: Stmt, inside_control: bool = False) -> None:
    if isinstance(stmt, (Spawn, Await)) and inside_control:
        raise ValueError(f"spawn/await not allowed inside if/while (line {stmt.line})")

    if isinstance(stmt, Seq):
        for s in stmt.stmts:
            enforce_no_spawn_await_in_if_while(s, inside_control)
    elif isinstance(stmt, If):
        enforce_no_spawn_await_in_if_while(stmt.then_s, True)
        enforce_no_spawn_await_in_if_while(stmt.else_s, True)
    elif isinstance(stmt, While):
        enforce_no_spawn_await_in_if_while(stmt.body, True)


def list_spawns_awaits(stmt: Stmt, spawns=None, awaits=None):
    if spawns is None:
        spawns = []
    if awaits is None:
        awaits = []
    if isinstance(stmt, Spawn):
        spawns.append(stmt)
    elif isinstance(stmt, Await):
        awaits.append(stmt)
    elif isinstance(stmt, Seq):
        for s in stmt.stmts:
            list_spawns_awaits(s, spawns, awaits)
    return spawns, awaits
