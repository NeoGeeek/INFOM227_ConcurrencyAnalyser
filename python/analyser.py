from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Union, Iterable

# -------------------------
# AST (adapter à votre parseur)
# -------------------------

@dataclass(frozen=True)
class Program:
    functions: Dict[str, "FunctionDef"]  # name -> def

@dataclass(frozen=True)
class FunctionDef:
    name: str
    params: List[str]
    body: "Stmt"   # typiquement Seq([...]) même si la grammaire est stmtList

# ----- Statements -----

class Stmt:
    line: int

@dataclass(frozen=True)
class Seq(Stmt):
    stmts: List[Stmt]
    line: int = 0  # optionnel pour une séquence

@dataclass(frozen=True)
class AssignExpr(Stmt):
    target: str
    expr: "Expr"
    line: int

@dataclass(frozen=True)
class AssignCall(Stmt):
    target: str
    call: "FuncCall"
    line: int

@dataclass(frozen=True)
class If(Stmt):
    cond: "Expr"
    then_s: Stmt
    else_s: Stmt
    line: int

@dataclass(frozen=True)
class While(Stmt):
    cond: "Expr"
    body: Stmt
    line: int

@dataclass(frozen=True)
class Return(Stmt):
    expr: "Expr"
    line: int

# ----- Concurrency extension -----

@dataclass(frozen=True)
class Spawn(Stmt):
    call: "FuncCall"   # spawn f(args)
    line: int

@dataclass(frozen=True)
class Await(Stmt):
    func_name: str     # await f
    line: int

# ----- Function call (not an Expr in Small) -----
@dataclass(frozen=True)
class FuncCall:
    name: str
    args: List["Expr"]
    line: int

# ----- Expressions -----
class Expr:
    pass

@dataclass(frozen=True)
class Var(Expr):
    name: str

@dataclass(frozen=True)
class Num(Expr):
    value: int

@dataclass(frozen=True)
class Bool(Expr):
    value: bool

@dataclass(frozen=True)
class BinOp(Expr):
    left: Expr   # en Small: Var/Num/Bool uniquement (pas de nesting) :contentReference[oaicite:3]{index=3}
    op: str      # + - * / < > == != >= <= and or
    right: Expr
