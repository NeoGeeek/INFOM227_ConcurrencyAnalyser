#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SMALL Race Analyzer (modularized)
This file is now a thin wrapper delegating to the modular race analysis package.
"""

from __future__ import annotations

from race_analysis.cli import analyze_source, main
from race_analysis.conflicts import RaceWarning
from race_analysis.formatting import format_warning

if __name__ == "__main__":
    raise SystemExit(main())
from lexer import lex, LexerError
from abstract_syntax_tree import *
from parser import Parser, ParserError
from rules import enforce_no_spawn_await_in_if_while


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
    for _ in range(50):
        changed = False
        for fname, fdef in prog.functions.items():
            new_eff = compute_effect_seq(fdef.body, prog, effs, fdef)
            if not new_eff.equals(effs[fname]):
                effs[fname] = effs[fname].union(new_eff)
                changed = True
        if not changed:
            break
    return effs


# -----------------------------------------------------------------------------
# Concurrency race detection
# -----------------------------------------------------------------------------

@dataclass
class ThreadInfo:
    thread_id: str
    desc: str
    spawn_line: int
    reads: Set[str]
    writes: Set[str]
    read_sites: Dict[str, Set[int]]
    write_sites: Dict[str, Set[int]]

def threadinfo_from_effect(eff: Effect, tid: str, desc: str, spawn_line: int) -> ThreadInfo:
    return ThreadInfo(
        thread_id=tid,
        desc=desc,
        spawn_line=spawn_line,
        reads=set(eff.reads),
        writes=set(eff.writes),
        read_sites={k: set(v) for k, v in eff.read_sites.items()},
        write_sites={k: set(v) for k, v in eff.write_sites.items()},
    )

@dataclass
class ConcurState:
    active: Dict[str, ThreadInfo] = field(default_factory=dict)     # tid -> thread
    handle_env: Dict[str, Set[str]] = field(default_factory=dict)   # handle var -> {tid}

    def copy(self) -> "ConcurState":
        return ConcurState(
            active=dict(self.active),
            handle_env={k: set(v) for k, v in self.handle_env.items()},
        )

def join_states(a: ConcurState, b: ConcurState) -> ConcurState:
    out = ConcurState()
    out.active = dict(a.active)
    for tid, t in b.active.items():
        if tid in out.active:
            o = out.active[tid]
            merged = ThreadInfo(
                thread_id=tid,
                desc=o.desc,
                spawn_line=o.spawn_line,
                reads=set(o.reads) | set(t.reads),
                writes=set(o.writes) | set(t.writes),
                read_sites={k: set(v) for k, v in o.read_sites.items()},
                write_sites={k: set(v) for k, v in o.write_sites.items()},
            )
            for k, vs in t.read_sites.items():
                merged.read_sites.setdefault(k, set()).update(vs)
            for k, vs in t.write_sites.items():
                merged.write_sites.setdefault(k, set()).update(vs)
            out.active[tid] = merged
        else:
            out.active[tid] = t

    out.handle_env = {k: set(v) for k, v in a.handle_env.items()}
    for k, vs in b.handle_env.items():
        out.handle_env.setdefault(k, set()).update(vs)

    return out

@dataclass(frozen=True)
class RaceWarning:
    var: str
    kind: str
    line_a: int
    ctx_a: str
    lines_b: Tuple[int, ...]
    ctx_b: str

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

def compute_escaping_threads(prog: Program, effects: Dict[str, Effect]) -> Dict[str, List[ThreadInfo]]:
    """
    Optional conservative modeling: if a function spawns a handle that is never awaited
    in that same function (syntactically, under the project constraint), we treat it as
    an 'escaped' thread that may still run after the call returns.
    """
    esc: Dict[str, List[ThreadInfo]] = {}
    for fname, fdef in prog.functions.items():
        spawns, awaits = list_spawns_awaits(fdef.body, [], [])
        awaited = {a.handle for a in awaits}

        threads: List[ThreadInfo] = []
        for s in spawns:
            if s.handle is None:
                continue
            if s.handle in awaited:
                continue

            if isinstance(s.target, SpawnCall):
                callee_def = prog.functions[s.target.func]
                thr = substitute_effect(effects[s.target.func], callee_def, s.target.args)
                desc = f"escaped spawn {s.target.func}(...) from {fname}"
            else:
                thr = compute_effect_seq(s.target.body, prog, effects, fdef)
                desc = f"escaped spawn {{block}} from {fname}"

            tid = f"{fname}:{s.handle}@{s.line}"
            threads.append(threadinfo_from_effect(thr, tid, desc, s.line))

        esc[fname] = threads
    return esc

def mode_for(var: str, reads: Set[str], writes: Set[str]) -> Optional[str]:
    r = var in reads
    w = var in writes
    if r and w:
        return "RW"
    if r:
        return "R"
    if w:
        return "W"
    return None

def collect_other_lines(t: ThreadInfo, var: str) -> Set[int]:
    lines: Set[int] = set()
    if var in t.writes:
        lines |= set(t.write_sites.get(var, set()))
    if var in t.reads:
        lines |= set(t.read_sites.get(var, set()))
    if not lines:
        lines.add(t.spawn_line)
    return lines

def conflicts(mode: str, t: ThreadInfo, var: str) -> bool:
    if mode == "R":
        return var in t.writes
    if mode in ("W", "RW"):
        return (var in t.writes) or (var in t.reads)
    return False

def check_access(state: ConcurState, var: str, mode: str, line: int, ctx: str) -> List[RaceWarning]:
    out: List[RaceWarning] = []
    for t in state.active.values():
        if conflicts(mode, t, var):
            out.append(RaceWarning(
                var=var,
                kind=f"{mode} vs T",
                line_a=line,
                ctx_a=ctx,
                lines_b=tuple(sorted(collect_other_lines(t, var))),
                ctx_b=f"{t.desc} (spawn line {t.spawn_line})",
            ))
    return out

def check_thread_thread(newt: ThreadInfo, oldt: ThreadInfo, discover_line: int) -> List[RaceWarning]:
    # any overlap with at least one write:
    overlap = (newt.writes & (oldt.reads | oldt.writes)) | (newt.reads & oldt.writes)
    out: List[RaceWarning] = []
    for var in sorted(overlap):
        out.append(RaceWarning(
            var=var,
            kind="T vs T",
            line_a=discover_line,
            ctx_a=f"concurrent threads overlap starting at spawn line {discover_line}",
            lines_b=tuple(sorted(collect_other_lines(oldt, var) | collect_other_lines(newt, var))),
            ctx_b=f"{oldt.desc} (spawn {oldt.spawn_line}) || {newt.desc} (spawn {newt.spawn_line})",
        ))
    return out

def analyze_stmt(
    stmt: Stmt,
    prog: Program,
    effects: Dict[str, Effect],
    escapes: Dict[str, List[ThreadInfo]],
    current_func: FunctionDef,
    state: ConcurState,
    warnings: Set[RaceWarning],
) -> ConcurState:

    def add_all(ws: List[RaceWarning]) -> None:
        for w in ws:
            warnings.add(w)

    if isinstance(stmt, Assign):
        if stmt.target in state.handle_env:
            state.handle_env[stmt.target] = set()

        reads = vars_in_expr(stmt.expr)
        writes = {stmt.target}

        for var in sorted(reads | writes):
            m = mode_for(var, reads, writes)
            add_all(check_access(state, var, m, stmt.line, f"{current_func.name}:{m} at line {stmt.line}"))

        return state

    if isinstance(stmt, AssignCall):
        if stmt.target in state.handle_env:
            state.handle_env[stmt.target] = set()

        arg_reads: Set[str] = set()
        for a in stmt.args:
            arg_reads |= vars_in_expr(a)

        for var in sorted(arg_reads):
            add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(arg) at call site line {stmt.line}"))

        callee_def = prog.functions[stmt.func]
        callee_eff = substitute_effect(effects[stmt.func], callee_def, stmt.args)

        for var in sorted(callee_eff.reads | callee_eff.writes):
            m = mode_for(var, callee_eff.reads, callee_eff.writes)
            lines = (callee_eff.read_sites.get(var, set()) | callee_eff.write_sites.get(var, set())) or {stmt.line}
            ln = min(lines)
            add_all(check_access(state, var, m, ln, f"{stmt.func}:{m} during call from {current_func.name} at line {stmt.line}"))

        add_all(check_access(state, stmt.target, "W", stmt.line, f"{current_func.name}:W(ret) at line {stmt.line}"))

        # propagate escaped threads (approx)
        for t in escapes.get(stmt.func, []):
            base = Effect(
                reads=set(t.reads),
                writes=set(t.writes),
                read_sites={k: set(v) for k, v in t.read_sites.items()},
                write_sites={k: set(v) for k, v in t.write_sites.items()},
            )
            sub = substitute_effect(base, callee_def, stmt.args)
            tid = f"escaped:{t.thread_id}@call{stmt.line}"
            state.active[tid] = threadinfo_from_effect(sub, tid, t.desc, t.spawn_line)

        return state

    if isinstance(stmt, CallStmt):
        arg_reads: Set[str] = set()
        for a in stmt.args:
            arg_reads |= vars_in_expr(a)

        for var in sorted(arg_reads):
            add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(arg) at call site line {stmt.line}"))

        callee_def = prog.functions[stmt.func]
        callee_eff = substitute_effect(effects[stmt.func], callee_def, stmt.args)

        for var in sorted(callee_eff.reads | callee_eff.writes):
            m = mode_for(var, callee_eff.reads, callee_eff.writes)
            lines = (callee_eff.read_sites.get(var, set()) | callee_eff.write_sites.get(var, set())) or {stmt.line}
            ln = min(lines)
            add_all(check_access(state, var, m, ln, f"{stmt.func}:{m} during call from {current_func.name} at line {stmt.line}"))

        for t in escapes.get(stmt.func, []):
            base = Effect(
                reads=set(t.reads),
                writes=set(t.writes),
                read_sites={k: set(v) for k, v in t.read_sites.items()},
                write_sites={k: set(v) for k, v in t.write_sites.items()},
            )
            sub = substitute_effect(base, callee_def, stmt.args)
            tid = f"escaped:{t.thread_id}@call{stmt.line}"
            state.active[tid] = threadinfo_from_effect(sub, tid, t.desc, t.spawn_line)

        return state

    if isinstance(stmt, Spawn):
        if stmt.handle is not None:
            if stmt.handle in state.handle_env:
                state.handle_env[stmt.handle] = set()
            add_all(check_access(state, stmt.handle, "W", stmt.line, f"{current_func.name}:W(handle) at spawn line {stmt.line}"))

        # parent argument evaluation
        if isinstance(stmt.target, SpawnCall):
            arg_reads: Set[str] = set()
            for a in stmt.target.args:
                arg_reads |= vars_in_expr(a)

            for var in sorted(arg_reads):
                add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(arg) at spawn line {stmt.line}"))

            callee_def = prog.functions[stmt.target.func]
            thr = substitute_effect(effects[stmt.target.func], callee_def, stmt.target.args)
            desc = f"spawn {stmt.target.func}(...) in {current_func.name}"
            tid_base = stmt.handle if stmt.handle else stmt.target.func

        else:
            thr = compute_effect_seq(stmt.target.body, prog, effects, current_func)
            desc = f"spawn {{block}} in {current_func.name}"
            tid_base = stmt.handle if stmt.handle else "_anon"

        tid = f"{current_func.name}:{tid_base}@{stmt.line}"
        newt = threadinfo_from_effect(thr, tid, desc, stmt.line)

        for old in list(state.active.values()):
            add_all(check_thread_thread(newt, old, stmt.line))

        state.active[tid] = newt

        # bind handle
        if stmt.handle is not None:
            state.handle_env.setdefault(stmt.handle, set()).add(tid)
        else:
            # allow await <functionName> for "spawn f(...);" form
            if isinstance(stmt.target, SpawnCall):
                state.handle_env.setdefault(stmt.target.func, set()).add(tid)

        return state

    if isinstance(stmt, Await):
        tids = state.handle_env.get(stmt.handle, set())
        for tid in list(tids):
            state.active.pop(tid, None)
        state.handle_env[stmt.handle] = set()
        return state

    if isinstance(stmt, Return):
        reads = vars_in_expr(stmt.expr)
        for var in sorted(reads):
            add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(return) at line {stmt.line}"))
        return state

    if isinstance(stmt, Seq):
        for s in stmt.stmts:
            state = analyze_stmt(s, prog, effects, escapes, current_func, state, warnings)
        return state

    if isinstance(stmt, If):
        for var in sorted(vars_in_expr(stmt.cond)):
            add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(if-cond) at line {stmt.line}"))
        st_then = analyze_stmt(stmt.then_s, prog, effects, escapes, current_func, state.copy(), warnings)
        st_else = analyze_stmt(stmt.else_s, prog, effects, escapes, current_func, state.copy(), warnings)
        return join_states(st_then, st_else)

    if isinstance(stmt, While):
        for var in sorted(vars_in_expr(stmt.cond)):
            add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(while-cond) at line {stmt.line}"))
        st_body = analyze_stmt(stmt.body, prog, effects, escapes, current_func, state.copy(), warnings)
        return join_states(state, st_body)

    raise TypeError(stmt)


# -----------------------------------------------------------------------------
# CLI / Runner
# -----------------------------------------------------------------------------

def analyze_source(src: str) -> List[RaceWarning]:
    prog = Parser(lex(src)).parse_program()

    # enforce project constraint
    for f in prog.functions.values():
        enforce_no_spawn_await_in_if_while(f.body, inside_control=False)

    effects = compute_function_effects(prog)
    escapes = compute_escaping_threads(prog, effects)

    warnings: Set[RaceWarning] = set()
    for f in prog.functions.values():
        analyze_stmt(f.body, prog, effects, escapes, f, ConcurState(), warnings)

    return sorted(warnings, key=lambda w: (w.line_a, w.var, w.kind))

def format_warning(w: RaceWarning) -> str:
    b_lines = ", ".join(str(x) for x in w.lines_b) if w.lines_b else "?"
    return (
        f"[RACE] var='{w.var}' @ line {w.line_a} ({w.kind})\n"
        f"  A: {w.ctx_a}\n"
        f"  B: lines {{{b_lines}}} in {w.ctx_b}\n"
    )

def main() -> int:
    ap = argparse.ArgumentParser(description="Static race detector for SMALL + spawn/await.")
    ap.add_argument("file", help="Path to a .small source file")
    args = ap.parse_args()

    try:
        with open(args.file, "r", encoding="utf-8") as f:
            src = f.read()
        warnings = analyze_source(src)

        if not warnings:
            print("No race candidates found.")
            return 0

        print(f"{len(warnings)} race candidate(s) found:\n")
        for w in warnings:
            print(format_warning(w))

        return 2

    except (LexerError, ParserError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
