from parser.model import LANGUAGE_CONSTRUCT_MAP
from tree_sitter_language_pack import Node

# Per-language, per-node-type overrides for constructs whose name is a
# RELATIONSHIP between two fields, not a single field (e.g. Rust `impl Trait
# for Type`, Go `func (receiver) Name()`). Any node type NOT listed here just
# uses the default "simple" shape (read the node's `name` field) — so most
# languages need zero entries here.
NAME_SHAPE = {
    "rust": {
        "impl_item": {
            "primary": "type",
            "secondary": "trait",
            "template": "{secondary} for {primary}",
        },
    },
    # Add more as you meet them, e.g.:
    "go": {
        "method_declaration": {
            "primary": "receiver", "secondary": "name",
            "template": "{primary}.{secondary}",
        },
    },
}

# Per-language node types where the identifier is BURIED inside the node
# rather than exposed as its own field (e.g. `const X = 1` is one
# "assignment"/"lexical_declaration" node — the name is a child, not a field).
DECLARATOR_NODE_TYPES = {
    "python": {"assignment"},
    "typescript": {"lexical_declaration"},
    "javascript": {"lexical_declaration"},
    "go": {"const_declaration", "var_declaration", "short_var_declaration"},
}


def _text(node, code_bytes: bytes) -> str:
    return code_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _extract_relational_name(node, code_bytes: bytes, shape_cfg: dict) -> str | None:
    """Handles constructs whose name is really two fields joined together."""
    primary_node = node.child_by_field_name(shape_cfg["primary"])
    secondary_node = node.child_by_field_name(shape_cfg["secondary"])

    if primary_node and secondary_node:
        return shape_cfg["template"].format(
            primary=_text(primary_node, code_bytes),
            secondary=_text(secondary_node, code_bytes),
        )
    if primary_node:
        return _text(primary_node, code_bytes)
    return None


def _extract_simple_name(node, code_bytes: bytes) -> str | None:
    """Default shape: the name sits in a `name` field. Covers most constructs
    in most languages (functions, structs, classes, enums, etc.)."""
    name_node = node.child_by_field_name("name")
    if name_node:
        return _text(name_node, code_bytes)
    return None


def _extract_declarator_name(node, code_bytes: bytes) -> str | None:
    """Handles constructs where the identifier is nested inside the node
    rather than its own field (e.g. `const X = 1`)."""
    for child in node.children:
        if "identifier" in child.type or child.type == "variable_declarator":
            raw = _text(child, code_bytes)
            return raw.split("=")[0].strip()
    return None


def extract_item_metadata(node: Node, code_bytes: bytes, lang: str) -> dict | None:
    """
    Identifies the construct category (function, struct, enum, trait, impl,
    constant, module) and extracts the clean item name + text content.

    Name extraction is driven by a per-node-type "shape" lookup instead of
    hardcoded per-language if-branches, so adding a new language never
    requires touching this function — only the NAME_SHAPE / DECLARATOR_NODE_TYPES
    tables above, and only for the node types that actually need it.
    """
    construct_rules = LANGUAGE_CONSTRUCT_MAP.get(lang, {})
    matched_category = None

    # 1. Determine the construct category
    for category, node_types in construct_rules.items():
        if node.type in node_types:
            matched_category = category
            break

    if not matched_category:
        return None  # Not a targeted construct

    item_name = None

    # 2a. Relational shape (e.g. Rust `impl Trait for Type`) — checked first
    # since it's the most specific case.
    shape_cfg = NAME_SHAPE.get(lang, {}).get(node.type)
    if shape_cfg:
        item_name = _extract_relational_name(node, code_bytes, shape_cfg)

    # 2b. Default simple-field shape — covers the vast majority of constructs.
    if not item_name:
        item_name = _extract_simple_name(node, code_bytes)

    # 2c. Declarator shape — only checked for node types explicitly flagged
    # per language as "identifier is buried inside, not a field".
    if not item_name and node.type in DECLARATOR_NODE_TYPES.get(lang, set()):
        item_name = _extract_declarator_name(node, code_bytes)

    # Fallback name if anonymous / unrecognized shape
    if not item_name:
        item_name = f"anonymous_{matched_category}"

    return {
        "category": matched_category,  # e.g. 'function', 'struct', 'enum', 'trait', 'impl'
        "ast_type": node.type,  # e.g. 'function_item', 'impl_item'
        "name": item_name,  # e.g. 'calculate_total' or 'Display for Point'
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
    }
