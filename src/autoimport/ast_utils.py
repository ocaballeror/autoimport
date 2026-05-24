"""AST-based utilities for parsing Python source files."""

import ast
from pathlib import Path
from typing import NamedTuple


class _CallInfo(NamedTuple):
    positional_types: list[str | None]
    keyword_names: frozenset[str]


class _MethodSig(NamedTuple):
    param_types: list[str | None]
    min_positional: int
    max_positional: int | None  # None = unlimited via *args
    all_param_names: frozenset[str]
    has_var_keyword: bool


def _infer_arg_type(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant):
        return type(node.value).__name__
    if isinstance(node, ast.List):
        return "list"
    if isinstance(node, ast.Dict):
        return "dict"
    if isinstance(node, ast.Set):
        return "set"
    if isinstance(node, ast.Tuple):
        return "tuple"
    return None


def parse_module_definitions(file: Path, module: str) -> tuple[set[str], dict[str, str]]:
    try:
        tree = ast.parse(file.read_text())
    except Exception:
        return set(), {}

    module_parts = module.split(".")

    names: set[str] = set()
    reexports: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and not node.target.id.startswith("_"):
                names.add(node.target.id)
        elif isinstance(node, ast.TypeAlias):
            if isinstance(node.name, ast.Name) and not node.name.id.startswith("_"):
                names.add(node.name.id)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                source = node.module
                if not source:
                    continue
            else:
                # Relative import: resolve against the current module's package.
                # Init files represent the package itself, so level=1 means "this package";
                # non-init files need to go up one extra level.
                is_init = file.name == "__init__.py"
                strip = node.level - (1 if is_init else 0)
                base_parts = module_parts[:-strip] if strip > 0 else module_parts
                if not base_parts:
                    continue
                source = ".".join(base_parts) + ("." + node.module if node.module else "")

            for alias in node.names:
                if alias.name == "*" or alias.asname is not None:
                    continue
                reexports[alias.name] = source
    return names, reexports


def parse_class_attributes(file: Path, class_name: str) -> set[str]:
    try:
        tree = ast.parse(file.read_text())
    except Exception:
        return set()

    classes: dict[str, ast.ClassDef] = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }

    def extract_attrs(node: ast.ClassDef, seen: set[str]) -> set[str]:
        attrs: set[str] = set()
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                attrs.add(item.name)
                for subnode in ast.walk(item):
                    if isinstance(subnode, ast.Assign):
                        for target in subnode.targets:
                            if (
                                isinstance(target, ast.Attribute)
                                and isinstance(target.value, ast.Name)
                                and target.value.id == "self"
                            ):
                                attrs.add(target.attr)
                    elif isinstance(subnode, ast.AnnAssign):
                        if (
                            isinstance(subnode.target, ast.Attribute)
                            and isinstance(subnode.target.value, ast.Name)
                            and subnode.target.value.id == "self"
                        ):
                            attrs.add(subnode.target.attr)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        attrs.add(target.id)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                attrs.add(item.target.id)
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in classes and base.id not in seen:
                seen.add(base.id)
                attrs |= extract_attrs(classes[base.id], seen)
        return attrs

    if class_name not in classes:
        return set()

    return extract_attrs(classes[class_name], {class_name})


def parse_method_signatures(file: Path, class_name: str) -> dict[str, _MethodSig]:
    try:
        tree = ast.parse(file.read_text())
    except Exception:
        return {}

    classes: dict[str, ast.ClassDef] = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }

    def get_annotation(ann: ast.expr | None) -> str | None:
        return ann.id if isinstance(ann, ast.Name) else None

    def sig_from_func(func: ast.FunctionDef | ast.AsyncFunctionDef) -> _MethodSig:
        positional = func.args.args[1:]  # exclude self
        num_defaults = len(func.args.defaults)
        return _MethodSig(
            param_types=[get_annotation(p.annotation) for p in positional],
            min_positional=len(positional) - num_defaults,
            max_positional=None if func.args.vararg else len(positional),
            all_param_names=frozenset(p.arg for p in positional)
            | frozenset(p.arg for p in func.args.kwonlyargs),
            has_var_keyword=func.args.kwarg is not None,
        )

    def extract(node: ast.ClassDef, seen: set[str]) -> dict[str, _MethodSig]:
        sigs: dict[str, _MethodSig] = {}
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in classes and base.id not in seen:
                seen.add(base.id)
                sigs.update(extract(classes[base.id], seen))
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sigs[item.name] = sig_from_func(item)
        return sigs

    if class_name not in classes:
        return {}

    return extract(classes[class_name], {class_name})


def call_signatures_match(
    calls: dict[str, _CallInfo],
    sigs: dict[str, _MethodSig],
) -> bool:
    for method, call in calls.items():
        if method not in sigs:
            continue
        sig = sigs[method]
        n = len(call.positional_types)
        if n < sig.min_positional:
            return False
        if sig.max_positional is not None and n > sig.max_positional:
            return False
        if not sig.has_var_keyword and not call.keyword_names.issubset(sig.all_param_names):
            return False
        for i, call_type in enumerate(call.positional_types):
            if call_type is None or i >= len(sig.param_types) or sig.param_types[i] is None:
                continue
            if call_type != sig.param_types[i]:
                return False
    return True


def find_usage(file: Path, target: str) -> list[str]:
    try:
        mo = ast.parse(file.read_text(), file.name)
    except SyntaxError:
        return []
    track: str | None = None
    uses = []
    for node in ast.walk(mo):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            var = node.targets[0].id
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                if node.value.func.id == target:
                    track = var
                elif var == track:
                    track = None
            elif var == track:
                track = None

        if track and isinstance(node, ast.Expr):
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == track
            ):
                uses.append(node.value.func.attr)
            elif (
                isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == track
            ):
                uses.append(node.value.attr)

    return uses


def find_method_calls(file: Path, target: str) -> dict[str, _CallInfo]:
    try:
        mo = ast.parse(file.read_text(), file.name)
    except SyntaxError:
        return {}

    tracked: set[str] = set()
    result: dict[str, _CallInfo] = {}

    for node in ast.walk(mo):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            var = node.targets[0].id
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == target
            ):
                tracked.add(var)
            elif var in tracked:
                tracked.discard(var)

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            is_tracked = isinstance(value, ast.Name) and value.id in tracked
            is_direct = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == target
            )
            if is_tracked or is_direct:
                method = node.func.attr
                if method not in result:
                    result[method] = _CallInfo(
                        positional_types=[_infer_arg_type(arg) for arg in node.args],
                        keyword_names=frozenset(
                            kw.arg for kw in node.keywords if kw.arg is not None
                        ),
                    )

    return result


def analyze_usage(file: Path, target: str) -> tuple[list[str], dict[str, _CallInfo]]:
    """Parse the file once, returning both attribute uses and method call signatures."""
    try:
        mo = ast.parse(file.read_text(), file.name)
    except SyntaxError:
        return [], {}

    tracked: set[str] = set()
    uses: list[str] = []
    method_calls: dict[str, _CallInfo] = {}

    for node in ast.walk(mo):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            var = node.targets[0].id
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == target
            ):
                tracked.add(var)
            elif var in tracked:
                tracked.discard(var)

        if tracked and isinstance(node, ast.Expr):
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id in tracked
            ):
                uses.append(node.value.func.attr)
            elif (
                isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id in tracked
            ):
                uses.append(node.value.attr)

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            is_tracked = isinstance(value, ast.Name) and value.id in tracked
            is_direct = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == target
            )
            if is_tracked or is_direct:
                method = node.func.attr
                if method not in method_calls:
                    method_calls[method] = _CallInfo(
                        positional_types=[_infer_arg_type(arg) for arg in node.args],
                        keyword_names=frozenset(
                            kw.arg for kw in node.keywords if kw.arg is not None
                        ),
                    )

    return uses, method_calls
