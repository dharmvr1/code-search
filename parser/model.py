from dataclasses import dataclass, field
from typing import Optional


@dataclass(slots=True)
class Symbol:
    id: str
    symbol: str
    repo: str
    path: str
    language: str
    kind: str

    start_line: int
    end_line: int

    body: str
    doc: Optional[str]

    content_hash: str


@dataclass
class SymbolNode(Symbol):
    parent_id: Optional[str] = None  # Points to "Toolchain" if inside an impl/class

    # 2. BEHAVIORAL TRACKING (Calls made INSIDE this body)
    calls: list[str] = field(default_factory=list)


@dataclass
class SymbolEdge:
    source_id: str  # "rustup:src/auth.py::login_user"
    target_id: str  # "rustup:src/crypto.py::hash_password"
    edge_type: str


LANGUAGE_CHUNK_NODES = {
    "python": [
        "function_definition",
        "async_function_definition",
        "class_definition",
    ],
    "rust": [
        "function_item",
        "impl_item",
        "trait_item",
        "struct_item",
        "enum_item",
    ],
}
# Language -> Construct Category -> Tree-Sitter AST Node Types

LANGUAGE_CONSTRUCT_MAP = {
    "rust": {
        "function": ["function_item"],
        "struct": ["struct_item"],
        "enum": ["enum_item"],
        "trait": ["trait_item"],
        "impl": ["impl_item"],
        "module": ["mod_item"],
        "constant": ["const_item", "static_item"],
    },
    "python": {
        "function": ["function_definition", "async_function_definition"],
        "struct": ["class_definition"],  # Classes serve as structs/enums in Python
        "enum": ["class_definition"],
        "trait": ["class_definition"],  # Abstract Base Classes / Protocols
        "impl": [],  # Methods live directly inside classes in Python
        "module": [],  # The file itself is the module
        "constant": ["assignment"],  # Top-level ALL_CAPS assignments
    },
    "typescript": {
        "function": [
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
        ],
        "struct": ["class_declaration"],
        "enum": ["enum_declaration"],
        "trait": ["interface_declaration", "type_alias_declaration"],
        "impl": [],
        "module": ["export_statement"],
        "constant": ["lexical_declaration"],  # const declarations
    },
    "javascript": {
        "function": [
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
        ],
        "struct": ["class_declaration"],
        "enum": [],
        "trait": [],
        "impl": [],
        "module": ["export_statement"],
        "constant": ["lexical_declaration"],
    },
    "go": {
        "function": ["function_declaration", "method_declaration"],
        "struct": ["type_declaration"],  # Contains type_spec (struct)
        "enum": ["const_declaration"],  # Go uses const + iota
        "trait": ["type_declaration"],  # Contains interface_type
        "impl": [],  # Methods are standalone declarations bound to receivers
        "module": ["package_clause"],
        "constant": ["const_declaration"],
    },
    "java": {
        "function": ["method_declaration", "constructor_declaration"],
        "struct": ["class_declaration", "record_declaration"],
        "enum": ["enum_declaration"],
        "trait": ["interface_declaration"],
        "impl": [],
        "module": ["package_declaration"],
        "constant": ["field_declaration"],
    },
    "cpp": {
        "function": ["function_definition"],
        "struct": ["struct_specifier", "class_specifier"],
        "enum": ["enum_specifier"],
        "trait": ["concept_definition"],  # C++20 concepts
        "impl": [],
        "module": ["namespace_definition"],
        "constant": ["declaration"],
    },
}


# Categories whose ONLY job is to group other things.
# They should never become a chunk themselves — always look inside instead.
HOLDER_CATEGORIES = {"module", "impl"}
CONTAINER_KINDS = {"class", "class_definition", "impl_item", "struct"}
