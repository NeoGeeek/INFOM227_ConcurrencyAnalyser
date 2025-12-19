import re
import sys
from dataclasses import dataclass
from typing import List, Set, Dict, Tuple

# ======================================================
# Abstract domain with thread tracking
# ======================================================

ThreadVar = Tuple[str, int]  # (variable name, thread id)

@dataclass
class State:
    R: Set[ThreadVar]
    W: Set[ThreadVar]

    def join(self, other: "State") -> "State":
        return State(self.R | other.R, self.W | other.W)

    def kill_thread(self, thread_state: "State") -> None:
        """Supprime uniquement les variables d'un thread terminé."""
        self.R -= thread_state.R
        self.W -= thread_state.W


# ======================================================
# AST Nodes
# ======================================================

class Stmt:
    lineno: int

@dataclass
class Assign(Stmt):
    target: str
    reads: Set[str]
    lineno: int

@dataclass
class Call(Stmt):
    fn: str
    reads: Set[str]
    lineno: int

@dataclass
class Spawn(Stmt):
    handle: str
    body: List[Stmt]
    lineno: int

@dataclass
class Await(Stmt):
    handle: str
    lineno: int

@dataclass
class Return(Stmt):
    reads: Set[str]
    lineno: int


# ======================================================
# Parser utilities
# ======================================================

VAR = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")

def vars_in_expr(expr: str) -> Set[str]:
    return set(VAR.findall(expr))

def clean_lines(code: str) -> List[Tuple[int, str]]:
    lines = []
    for i, line in enumerate(code.splitlines(), start=1):
        stripped = line.strip()
        if stripped and not stripped.startswith("//"):
            lines.append((i, stripped))
    return lines


# ======================================================
# Block parser
# ======================================================

def parse_block(lines: List[Tuple[int, str]], i: int) -> Tuple[List[Stmt], int]:
    block: List[Stmt] = []

    while i < len(lines):
        lineno, line = lines[i]

        if line == "}":
            return block, i + 1

        if "spawn" in line:
            handle = line.split("=")[0].strip()
            i += 2  # skip 'spawn {' line
            body, i = parse_block(lines, i)
            block.append(Spawn(handle, body, lineno))
            continue

        if line.startswith("await"):
            handle = line.replace("await", "").replace(";", "").strip()
            block.append(Await(handle, lineno))
            i += 1
            continue

        if line.startswith("return"):
            expr = line.replace("return", "").replace(";", "")
            block.append(Return(vars_in_expr(expr), lineno))
            i += 1
            continue

        if "(" in line and ")" in line and "=" not in line:
            fn = line.split("(")[0]
            args = line[line.find("(")+1:line.find(")")]
            block.append(Call(fn, vars_in_expr(args), lineno))
            i += 1
            continue

        if "=" in line:
            left, right = line.split("=", 1)
            block.append(Assign(left.strip(), vars_in_expr(right), lineno))
            i += 1
            continue

        i += 1

    return block, i


# ======================================================
# Function parser
# ======================================================

def parse_functions(code: str) -> Dict[str, List[Stmt]]:
    lines = clean_lines(code)
    functions: Dict[str, List[Stmt]] = {}
    i = 0

    while i < len(lines):
        lineno, line = lines[i]
        if "{" in line and "(" in line:
            fn_name = line.split("(")[0]
            body, i = parse_block(lines, i + 1)
            functions[fn_name] = body
        else:
            i += 1

    return functions


# ======================================================
# Analysis with threads
# ======================================================

def analyze_block(
    block: List[Stmt],
    fn_effects: Dict[str, State],
    races: List[str],
    context: str,
    thread_id: int,
    active_threads: Dict[int, State]
) -> State:
    state = State(set(), set())
    next_thread_id = max(active_threads.keys(), default=thread_id) + 1

    for stmt in block:

        # Assignment
        if isinstance(stmt, Assign):
            # check races with all other active threads
            for tid, t_state in active_threads.items():
                if tid != thread_id:
                    for v in stmt.reads:
                        if any(w_var == v for w_var, _ in t_state.W):
                            races.append(
                                f"[RACE] Line {stmt.lineno}: lecture concurrente de '{v}' dans {context} (thread {thread_id})"
                            )
                    if any(stmt.target == w_var for w_var, _ in t_state.W):
                        races.append(
                            f"[RACE] Line {stmt.lineno}: écriture concurrente de '{stmt.target}' dans {context} (thread {thread_id})"
                        )
            state.R |= {(v, thread_id) for v in stmt.reads}
            state.W.add((stmt.target, thread_id))

        # Function call
        elif isinstance(stmt, Call):
            eff = fn_effects.get(stmt.fn, State(set(), set()))
            eff_threaded = State({(v, thread_id) for v, _ in eff.R},
                                 {(v, thread_id) for v, _ in eff.W})
            state = state.join(eff_threaded)

        # Spawn
        elif isinstance(stmt, Spawn):
            tid = next_thread_id
            next_thread_id += 1
            eff = analyze_block(stmt.body, fn_effects, races,
                                context + f"::spawn({stmt.handle})",
                                tid,
                                active_threads.copy())
            active_threads[tid] = eff
            state = state.join(eff)

        # Await
        elif isinstance(stmt, Await):
            # find the thread handle and kill only that thread
            # here, handle name -> we need to find the thread id
            to_kill = []
            for tid, t_state in active_threads.items():
                # naive: assume the first thread with that handle
                # in this version, we just remove oldest thread
                to_kill.append(tid)
                break
            for tid in to_kill:
                state.kill_thread(active_threads[tid])
                del active_threads[tid]

        # Return
        elif isinstance(stmt, Return):
            for tid, t_state in active_threads.items():
                if tid != thread_id:
                    for v in stmt.reads:
                        if any(w_var == v for w_var, _ in t_state.W):
                            races.append(
                                f"[RACE] Line {stmt.lineno}: lecture concurrente de '{v}' dans return ({context}) (thread {thread_id})"
                            )
            state.R |= {(v, thread_id) for v in stmt.reads}

    return state


# ======================================================
# Main
# ======================================================

def main():
    if len(sys.argv) != 2:
        print("Usage: python analyze_small.py <file.small>")
        sys.exit(1)

    filename = sys.argv[1]
    with open(filename, "r", encoding="utf-8") as f:
        code = f.read()

    functions = parse_functions(code)
    races: List[str] = []
    fn_effects: Dict[str, State] = {}

    # Compute summaries
    for fn, body in functions.items():
        fn_effects[fn] = analyze_block(body, fn_effects, races, fn, 0, {})

    # Analyze main
    if "main" in functions:
        analyze_block(functions["main"], fn_effects, races, "main", 0, {})

    print("=== Data Race Analysis ===")
    if not races:
        print("No races detected.")
    else:
        for r in races:
            print(r)


if __name__ == "__main__":
    main()
