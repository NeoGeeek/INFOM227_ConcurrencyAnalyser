import re
import sys
from dataclasses import dataclass
from typing import List, Set, Dict, Tuple

# ======================================================
# Abstract domain
# ======================================================

@dataclass
class State:
    R: Set[str]
    W: Set[str]

    def join(self, other: "State") -> "State":
        return State(self.R | other.R, self.W | other.W)

    def kill(self, other: "State") -> None:
        self.R -= other.R
        self.W -= other.W


# ======================================================
# AST Nodes (with line numbers)
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
    """Return (lineno, line) while skipping empty lines and comments."""
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

        # spawn
        if "spawn" in line:
            handle = line.split("=")[0].strip()
            spawn_lineno = lineno
            i += 2  # skip 'spawn {' line
            body, i = parse_block(lines, i)
            block.append(Spawn(handle, body, spawn_lineno))
            continue

        # await
        if line.startswith("await"):
            handle = line.replace("await", "").replace(";", "").strip()
            block.append(Await(handle, lineno))
            i += 1
            continue

        # return
        if line.startswith("return"):
            expr = line.replace("return", "").replace(";", "")
            block.append(Return(vars_in_expr(expr), lineno))
            i += 1
            continue

        # function call
        if "(" in line and ")" in line and "=" not in line:
            fn = line.split("(")[0]
            args = line[line.find("(") + 1 : line.find(")")]
            block.append(Call(fn, vars_in_expr(args), lineno))
            i += 1
            continue

        # assignment
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
# Gen/Kill Analysis
# ======================================================

def analyze_block(
    block: List[Stmt],
    fn_effects: Dict[str, State],
    races: List[str],
    context: str
) -> State:
    state = State(set(), set())
    active_threads: Dict[str, State] = {}

    for stmt in block:

        # Assignment
        if isinstance(stmt, Assign):
            for v in stmt.reads:
                if v in state.W:
                    races.append(
                        f"[RACE] Line {stmt.lineno}: lecture concurrente de '{v}' dans {context}"
                    )
            state.R |= stmt.reads
            state.W.add(stmt.target)

        # Function call
        elif isinstance(stmt, Call):
            eff = fn_effects.get(stmt.fn, State(set(), set()))
            state = state.join(eff)

        # Spawn
        elif isinstance(stmt, Spawn):
            eff = analyze_block(
                stmt.body, fn_effects, races, context + "::spawn"
            )
            active_threads[stmt.handle] = eff
            state = state.join(eff)

        # Await
        elif isinstance(stmt, Await):
            if stmt.handle in active_threads:
                state.kill(active_threads[stmt.handle])
                del active_threads[stmt.handle]

        # Return
        elif isinstance(stmt, Return):
            for v in stmt.reads:
                if v in state.W:
                    races.append(
                        f"[RACE] Line {stmt.lineno}: lecture concurrente de '{v}' dans return ({context})"
                    )
            state.R |= stmt.reads

    return state


# ======================================================
# Main entry point
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
        fn_effects[fn] = analyze_block(body, fn_effects, races, fn)

    # Analyze main
    if "main" in functions:
        analyze_block(functions["main"], fn_effects, races, "main")

    print("=== Data Race Analysis ===")
    if not races:
        print("No races detected.")
    else:
        for r in races:
            print(r)


if __name__ == "__main__":
    main()
