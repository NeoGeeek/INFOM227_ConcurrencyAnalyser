from __future__ import annotations
from typing import Dict, List, Set

from abstract_syntax_tree import *

from effects import Effect, compute_effect_seq, compute_function_effects, substitute_effect, vars_in_expr
from constraints import enforce_no_spawn_await_in_if_while, list_spawns_awaits
from concurrency import ConcurState, ThreadInfo, join_states, threadinfo_from_effect
from conflicts import RaceWarning, mode_for, check_access, check_thread_thread


# -----------------------------------------------------------------------------
# Escaping threads (optional conservative modeling)
# -----------------------------------------------------------------------------

def compute_escaping_threads(prog: Program, effects: Dict[str, Effect]) -> Dict[str, List[ThreadInfo]]:
    """
    If a function spawns a handle that is never awaited in the same function
    (syntactically, under the project constraint), we treat it as an 'escaped'
    thread that may still run after the call returns.
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
                desc = f"escaped spawn {{block}} from {fname}".format(block="{block}")

            tid = f"{fname}:{s.handle}@{s.line}"
            threads.append(threadinfo_from_effect(thr, tid, desc, s.line))

        esc[fname] = threads
    return esc


# -----------------------------------------------------------------------------
# Core analyzer
# -----------------------------------------------------------------------------

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
# Orchestration helper: analyze a parsed Program
# -----------------------------------------------------------------------------

def analyze_program(prog: Program) -> List[RaceWarning]:
    # enforce project constraint
    for f in prog.functions.values():
        enforce_no_spawn_await_in_if_while(f.body, inside_control=False)

    effects = compute_function_effects(prog)
    escapes = compute_escaping_threads(prog, effects)

    warnings: Set[RaceWarning] = set()
    for f in prog.functions.values():
        analyze_stmt(f.body, prog, effects, escapes, f, ConcurState(), warnings)

    return sorted(warnings, key=lambda w: (w.line_a, w.var, w.kind))
