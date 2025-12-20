from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Set

from abstract_syntax_tree import *


# -----------------------------------------------------------------------------
# Helpers: variable extraction
# -----------------------------------------------------------------------------


def vars_in_expr(e: Expr) -> Set[str]:
    if isinstance(e, Var):
        return {e.name}
    if isinstance(e, (Num, Bool)):
        return set()
    if isinstance(e, (BinOp, RelOp)):
        return vars_in_expr(e.left) | vars_in_expr(e.right)
    raise TypeError(e)


# -----------------------------------------------------------------------------
# Interprocedural effect analysis (R/W footprints with line sites)
# -----------------------------------------------------------------------------


@dataclass
class Effect:
    reads: Set[str] = field(default_factory=set)
    writes: Set[str] = field(default_factory=set)
    read_sites: Dict[str, Set[int]] = field(default_factory=dict)
    write_sites: Dict[str, Set[int]] = field(default_factory=dict)

    def add_read(self, var: str, line: int) -> None:
        self.reads.add(var)
        self.read_sites.setdefault(var, set()).add(line)

    def add_write(self, var: str, line: int) -> None:
        self.writes.add(var)
        self.write_sites.setdefault(var, set()).add(line)

    def union(self, other: "Effect") -> "Effect":
        out = Effect()
        out.reads = set(self.reads) | set(other.reads)
        out.writes = set(self.writes) | set(other.writes)

        out.read_sites = {k: set(v) for k, v in self.read_sites.items()}
        for k, v in other.read_sites.items():
            out.read_sites.setdefault(k, set()).update(v)

        out.write_sites = {k: set(v) for k, v in self.write_sites.items()}
        for k, v in other.write_sites.items():
            out.write_sites.setdefault(k, set()).update(v)

        return out

    def equals(self, other: "Effect") -> bool:
        return (
            self.reads == other.reads
            and self.writes == other.writes
            and self.read_sites == other.read_sites
            and self.write_sites == other.write_sites
        )


def substitute_effect(callee: Effect, callee_def: FunctionDef, actual_args: List[Expr]) -> Effect:
    """
    Conservative substitution:
      - formal p is mapped to variables appearing in the actual argument expression.
      - if actual is constant (no vars), p contributes nothing.
      - non-formal variables stay unchanged (treated as globals).
    """
    mapping: Dict[str, Set[str]] = {}
    for i, p in enumerate(callee_def.params):
        mapping[p] = vars_in_expr(actual_args[i]) if i < len(actual_args) else set()

    out = Effect()

    for v in callee.reads:
        targets = mapping.get(v, {v})
        for tv in targets:
            for ln in callee.read_sites.get(v, set()):
                out.add_read(tv, ln)

    for v in callee.writes:
        targets = mapping.get(v, {v})
        for tv in targets:
            for ln in callee.write_sites.get(v, set()):
                out.add_write(tv, ln)

    return out


def compute_effect_seq(seq: Seq, prog: Program, effects: Dict[str, Effect], current_func: FunctionDef) -> Effect:
    eff = Effect()
    for s in seq.stmts:
        eff = eff.union(compute_effect_stmt(s, prog, effects, current_func))
    return eff


def compute_effect_stmt(stmt: Stmt, prog: Program, effects: Dict[str, Effect], current_func: FunctionDef) -> Effect:
    eff = Effect()

    if isinstance(stmt, Assign):
        for v in vars_in_expr(stmt.expr):
            eff.add_read(v, stmt.line)
        eff.add_write(stmt.target, stmt.line)
        return eff

    if isinstance(stmt, AssignCall):
        for a in stmt.args:
            for v in vars_in_expr(a):
                eff.add_read(v, stmt.line)
        callee_def = prog.functions[stmt.func]
        eff = eff.union(substitute_effect(effects[stmt.func], callee_def, stmt.args))
        eff.add_write(stmt.target, stmt.line)
        return eff

    if isinstance(stmt, CallStmt):
        for a in stmt.args:
            for v in vars_in_expr(a):
                eff.add_read(v, stmt.line)
        callee_def = prog.functions[stmt.func]
        return eff.union(substitute_effect(effects[stmt.func], callee_def, stmt.args))

    if isinstance(stmt, Spawn):
        if stmt.handle is not None:
            eff.add_write(stmt.handle, stmt.line)

        if isinstance(stmt.target, SpawnCall):
            for a in stmt.target.args:
                for v in vars_in_expr(a):
                    eff.add_read(v, stmt.line)
            callee_def = prog.functions[stmt.target.func]
            thr = substitute_effect(effects[stmt.target.func], callee_def, stmt.target.args)
            return eff.union(thr)

        if isinstance(stmt.target, SpawnBlock):
            thr = compute_effect_seq(stmt.target.body, prog, effects, current_func)
            return eff.union(thr)

        raise TypeError(stmt.target)

    if isinstance(stmt, Await):
        return eff

    if isinstance(stmt, Return):
        for v in vars_in_expr(stmt.expr):
            eff.add_read(v, stmt.line)
        return eff

    if isinstance(stmt, Seq):
        for s in stmt.stmts:
            eff = eff.union(compute_effect_stmt(s, prog, effects, current_func))
        return eff

    if isinstance(stmt, If):
        for v in vars_in_expr(stmt.cond):
            eff.add_read(v, stmt.line)
        return (
            eff
            .union(compute_effect_stmt(stmt.then_s, prog, effects, current_func))
            .union(compute_effect_stmt(stmt.else_s, prog, effects, current_func))
        )

    if isinstance(stmt, While):
        for v in vars_in_expr(stmt.cond):
            eff.add_read(v, stmt.line)
        return eff.union(compute_effect_stmt(stmt.body, prog, effects, current_func))

    raise TypeError(stmt)


def compute_function_effects(prog: Program) -> Dict[str, Effect]:
    # monotone union fixpoint
    effs: Dict[str, Effect] = {name: Effect() for name in prog.functions}
    for _ in range(50): # we approximate fixpoint with a max number of iterations
        changed = False
        for fname, fdef in prog.functions.items():
            new_eff = compute_effect_seq(fdef.body, prog, effs, fdef)
            if not new_eff.equals(effs[fname]):
                effs[fname] = effs[fname].union(new_eff)
                changed = True
        if not changed:
            break
    return effs
