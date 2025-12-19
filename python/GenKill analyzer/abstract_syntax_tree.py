# -----------------------------------------------------------------------------
# AST
# -----------------------------------------------------------------------------

@dataclass
class Expr:
    line: int

@dataclass
class Var(Expr):
    name: str

@dataclass
class Num(Expr):
    value: int

@dataclass
class Bool(Expr):
    value: bool

@dataclass
class BinOp(Expr):
    op: str
    left: Expr
    right: Expr

@dataclass
class RelOp(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class Stmt:
    line: int

@dataclass
class Assign(Stmt):
    target: str
    expr: Expr

@dataclass
class AssignCall(Stmt):
    target: str
    func: str
    args: List[Expr]

@dataclass
class CallStmt(Stmt):
    func: str
    args: List[Expr]

@dataclass
class SpawnCall:
    func: str
    args: List[Expr]
    line: int

@dataclass
class SpawnBlock:
    body: "Seq"
    line: int

@dataclass
class Spawn(Stmt):
    handle: Optional[str]                 # None if no handle assignment
    target: Union[SpawnCall, SpawnBlock]

@dataclass
class Await(Stmt):
    handle: str

@dataclass
class If(Stmt):
    cond: Expr
    then_s: Stmt
    else_s: Stmt

@dataclass
class While(Stmt):
    cond: Expr
    body: Stmt

@dataclass
class Seq(Stmt):
    stmts: List[Stmt]

@dataclass
class Return(Stmt):
    expr: Expr

@dataclass
class FunctionDef:
    name: str
    params: List[str]
    body: Seq
    line: int

@dataclass
class Program:
    functions: Dict[str, FunctionDef]
