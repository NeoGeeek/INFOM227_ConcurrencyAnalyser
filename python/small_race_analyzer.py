
import re
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Tuple

@dataclass
class Access:
    var: str
    mode: str  # 'R', 'W', or 'RW'
    func: str
    stmt_index: int
    locks: Set[str] = field(default_factory=set)
    in_atomic: bool = False

    def lockset(self) -> Set[str]:
        s = set(self.locks)
        if self.in_atomic:
            s.add("__ATOMIC__")
        return s

@dataclass
class SpawnSite:
    id: int
    func: str            # parent function name
    callee: str          # spawned function
    stmt_index: int      # index in parent function's stmts for spawn
    tid_var: str
    join_index: Optional[int] = None  # matching join within same function

@dataclass
class FunctionDef:
    name: str
    params: List[str]
    body_text: str
    stmts: List[str] = field(default_factory=list)

@dataclass
class Program:
    shared_vars: Set[str] = field(default_factory=set)
    functions: Dict[str, FunctionDef] = field(default_factory=dict)
    spawns: List[SpawnSite] = field(default_factory=list)
    accesses: List[Access] = field(default_factory=list)

FUNC_RE = re.compile(r'function\s+(\w+)\s*\(([^)]*)\)\s*\{', re.MULTILINE)
SHARED_VAR_RE = re.compile(r'/\*shared\*/\s*var\s+(\w+)\b')
SPAWN_RE = re.compile(r'\bvar\s+(\w+)\s*:=\s*spawn\s+(\w+)\s*\(')
JOIN_RE = re.compile(r'\bjoin\s+(\w+)\s*;')
LOCK_RE = re.compile(r'\block\s+(\w+)\s*;')
UNLOCK_RE = re.compile(r'\bunlock\s+(\w+)\s*;')
ASSIGN_RE = re.compile(r'(\w+)\s*:=\s*(.+);')

def extract_functions(code: str) -> Dict[str, FunctionDef]:
    functions = {}
    pos = 0
    while True:
        m = FUNC_RE.search(code, pos)
        if not m:
            break
        name = m.group(1)
        params = [p.strip() for p in m.group(2).split(",") if p.strip()]
        start = m.end()
        depth = 1
        i = start
        while i < len(code) and depth > 0:
            if code[i] == '{':
                depth += 1
            elif code[i] == '}':
                depth -= 1
            i += 1
        body = code[start:i-1]
        functions[name] = FunctionDef(name=name, params=params, body_text=body)
        pos = i
    return functions

def tokenize_statements(body_text: str) -> List[str]:
    tokens = []
    i = 0
    buf = ""
    while i < len(body_text):
        ch = body_text[i]
        if ch == ';':
            buf = buf.strip()
            if buf:
                tokens.append(buf + ';')
            buf = ""
            i += 1
        elif ch == '{' or ch == '}':
            if buf.strip():
                tokens.append(buf.strip())
                buf = ""
            tokens.append(ch)
            i += 1
        else:
            buf += ch
            i += 1
    if buf.strip():
        tokens.append(buf.strip())
    merged = []
    skip_next = False
    for idx, tok in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if tok.strip() == 'atomic' and idx + 1 < len(tokens) and tokens[idx+1] == '{':
            merged.append('atomic {')
            skip_next = True
        else:
            merged.append(tok)
    return merged

def parse_program(code: str) -> Program:
    prog = Program()
    prog.shared_vars = set(SHARED_VAR_RE.findall(code))
    functions = extract_functions(code)
    prog.functions = functions
    for f in functions.values():
        f.stmts = tokenize_statements(f.body_text)

    spawn_id = 0
    tid_to_spawn: Dict[Tuple[str, str], int] = {}
    for fname, f in functions.items():
        for idx, stmt in enumerate(f.stmts):
            m = SPAWN_RE.search(stmt)
            if m:
                tid, callee = m.group(1), m.group(2)
                ss = SpawnSite(id=spawn_id, func=fname, callee=callee, stmt_index=idx, tid_var=tid)
                prog.spawns.append(ss)
                tid_to_spawn[(fname, tid)] = spawn_id
                spawn_id += 1
        for idx, stmt in enumerate(f.stmts):
            m = JOIN_RE.search(stmt)
            if m:
                tid = m.group(1)
                key = (fname, tid)
                if key in tid_to_spawn:
                    sid = tid_to_spawn[key]
                    if prog.spawns[sid].join_index is None:
                        prog.spawns[sid].join_index = idx

    for fname, f in functions.items():
        lock_stack: List[str] = []
        in_atomic_stack: List[bool] = []
        current_atomic = False

        def current_locks():
            return set(lock_stack)

        idx = 0
        while idx < len(f.stmts):
            stmt = f.stmts[idx].strip()
            if stmt == 'atomic {':
                in_atomic_stack.append(True)
                current_atomic = True
                idx += 1
                continue
            if stmt == '}':
                if in_atomic_stack:
                    in_atomic_stack.pop()
                current_atomic = any(in_atomic_stack)
                idx += 1
                continue

            m = LOCK_RE.search(stmt)
            if m:
                lock_stack.append(m.group(1))
                idx += 1
                continue
            m = UNLOCK_RE.search(stmt)
            if m:
                lk = m.group(1)
                for j in range(len(lock_stack)-1, -1, -1):
                    if lock_stack[j] == lk:
                        del lock_stack[j]
                        break
                idx += 1
                continue

            m = ASSIGN_RE.search(stmt)
            if m:
                lhs = m.group(1)
                rhs = m.group(2)
                rhs_vars = re.findall(r'\b[a-zA-Z_]\w*\b', rhs)
                for v in rhs_vars:
                    if v in prog.shared_vars:
                        prog.accesses.append(Access(var=v, mode='R', func=fname, stmt_index=idx,
                                                     locks=current_locks(), in_atomic=current_atomic))
                if lhs in prog.shared_vars:
                    prog.accesses.append(Access(var=lhs, mode='W', func=fname, stmt_index=idx,
                                                 locks=current_locks(), in_atomic=current_atomic))
                idx += 1
                continue

            idx += 1

    return prog

@dataclass
class RaceReport:
    var: str
    kind: str   # 'parent-child' or 'child-child'
    parent_func: str
    spawn_a: int
    spawn_b: Optional[int]
    site_a_desc: str
    site_b_desc: str
    locks_a: Set[str]
    locks_b: Set[str]

def build_access_index(prog: Program) -> Dict[str, List[Access]]:
    byvar: Dict[str, List[Access]] = {}
    for acc in prog.accesses:
        byvar.setdefault(acc.var, []).append(acc)
    return byvar

def get_parent_region(prog: Program, s: SpawnSite) -> List[Access]:
    f = prog.functions[s.func]
    end = s.join_index if s.join_index is not None else len(f.stmts)
    accs = [a for a in prog.accesses if a.func == s.func and s.stmt_index < a.stmt_index < end]
    return accs

def get_child_region(prog: Program, s: SpawnSite) -> List[Access]:
    accs = [a for a in prog.accesses if a.func == s.callee]
    return accs

def intervals_overlap(a_start: int, a_end: Optional[int], b_start: int, b_end: Optional[int]) -> bool:
    A_end = a_end if a_end is not None else float('inf')
    B_end = b_end if b_end is not None else float('inf')
    return (a_start < B_end) and (b_start < A_end)

def may_happen_in_parallel_child_child(prog: Program, s1: SpawnSite, s2: SpawnSite, a: Access, b: Access) -> bool:
    if a.func != s1.callee or b.func != s2.callee:
        return False
    if s1.func != s2.func:
        return True
    if not intervals_overlap(s1.stmt_index, s1.join_index, s2.stmt_index, s2.join_index):
        return False
    return True

def access_writes(acc: Access) -> bool:
    return acc.mode in ('W', 'RW')

def analyze_races(code: str) -> Tuple[Program, List[RaceReport]]:
    prog = parse_program(code)
    byvar = build_access_index(prog)
    reports: List[RaceReport] = []

    parent_regions: Dict[int, List[Access]] = {}
    child_regions: Dict[int, List[Access]] = {}
    for s in prog.spawns:
        parent_regions[s.id] = get_parent_region(prog, s)
        child_regions[s.id] = get_child_region(prog, s)

    for s in prog.spawns:
        parent_accs = parent_regions[s.id]
        child_accs = child_regions[s.id]
        for a in child_accs:
            for b in parent_accs:
                if a.var == b.var and (access_writes(a) or access_writes(b)):
                    if a.lockset().intersection(b.lockset()):
                        continue
                    reports.append(RaceReport(
                        var=a.var,
                        kind='parent-child',
                        parent_func=s.func,
                        spawn_a=s.id,
                        spawn_b=None,
                        site_a_desc=f"child {s.callee} (spawn {s.id})",
                        site_b_desc=f"parent region of {s.func} (between spawn and join)",
                        locks_a=a.lockset(),
                        locks_b=b.lockset(),
                    ))

    n = len(prog.spawns)
    for i in range(n):
        for j in range(i+1, n):
            s1 = prog.spawns[i]
            s2 = prog.spawns[j]
            for a in child_regions[s1.id]:
                for b in child_regions[s2.id]:
                    if a.var == b.var and (access_writes(a) or access_writes(b)):
                        if not may_happen_in_parallel_child_child(prog, s1, s2, a, b):
                            continue
                        if a.lockset().intersection(b.lockset()):
                            continue
                        reports.append(RaceReport(
                            var=a.var,
                            kind='child-child',
                            parent_func=s1.func if s1.func == s2.func else f"{s1.func}|{s2.func}",
                            spawn_a=s1.id,
                            spawn_b=s2.id,
                            site_a_desc=f"child {s1.callee} (spawn {s1.id})",
                            site_b_desc=f"child {s2.callee} (spawn {s2.id})",
                            locks_a=a.lockset(),
                            locks_b=b.lockset(),
                        ))

    dedup = {}
    for r in reports:
        key = (r.var, r.kind, r.parent_func, r.spawn_a, r.spawn_b, r.site_a_desc, r.site_b_desc)
        dedup[key] = r
    reports = list(dedup.values())
    return prog, reports

if __name__ == "__main__":
    import sys, pathlib
    if len(sys.argv) < 2:
        print("Usage: python small_race_analyzer.py <small_source_file>"); sys.exit(1)
    code = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
    prog, reports = analyze_races(code)
    if not reports:
        print("No races detected.")
    else:
        for r in reports:
            print(f"- [{r.kind}] var {r.var} between {r.site_a_desc} and {r.site_b_desc}; locks: {r.locks_a} vs {r.locks_b}")
