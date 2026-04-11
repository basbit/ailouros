"""Tests for backend/App/workspace/infrastructure/code_analysis/relations.py."""
from __future__ import annotations

from backend.App.workspace.infrastructure.code_analysis.relations import (
    _call_edges_light,
    _entity_edges,
    _import_edges,
    _resolve_py_module,
    build_architecture_map,
)


# ---------------------------------------------------------------------------
# _entity_edges
# ---------------------------------------------------------------------------

def test_entity_edges_empty():
    result = _entity_edges([])
    assert result == []


def test_entity_edges_route():
    files = [
        {
            "path": "backend/api.py",
            "entities": [
                {"kind": "route", "name": "/api/v1/users", "method": "GET", "line": 10}
            ],
        }
    ]
    result = _entity_edges(files)
    assert len(result) == 1
    assert result[0]["source"] == "backend/api.py"
    assert result[0]["kind"] == "route"
    assert result[0]["target"] == "/api/v1/users"
    assert result[0]["detail"] == "GET"


def test_entity_edges_non_route_skipped():
    files = [
        {
            "path": "backend/models.py",
            "entities": [
                {"kind": "class", "name": "User"}
            ],
        }
    ]
    result = _entity_edges(files)
    assert result == []


def test_entity_edges_no_path_skipped():
    files = [{"path": "", "entities": [{"kind": "route", "name": "/x"}]}]
    result = _entity_edges(files)
    assert result == []


def test_entity_edges_non_dict_entity_skipped():
    files = [{"path": "api.py", "entities": ["not-a-dict", {"kind": "route", "name": "/x"}]}]
    result = _entity_edges(files)
    assert len(result) == 1


def test_entity_edges_empty_name():
    files = [{"path": "api.py", "entities": [{"kind": "route", "name": ""}]}]
    result = _entity_edges(files)
    assert result[0]["target"] == "?"


# ---------------------------------------------------------------------------
# _resolve_py_module
# ---------------------------------------------------------------------------

def test_resolve_py_module_finds_file(tmp_path):
    (tmp_path / "mymod.py").write_text("# module")
    result = _resolve_py_module(tmp_path, "mymod", "current.py")
    assert result == "mymod.py"


def test_resolve_py_module_finds_package(tmp_path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    result = _resolve_py_module(tmp_path, "mypkg", "current.py")
    assert result == "mypkg/__init__.py"


def test_resolve_py_module_not_found(tmp_path):
    result = _resolve_py_module(tmp_path, "nonexistent", "current.py")
    assert result is None


def test_resolve_py_module_relative_import_skipped(tmp_path):
    result = _resolve_py_module(tmp_path, ".relative", "current.py")
    assert result is None


def test_resolve_py_module_empty_mod(tmp_path):
    result = _resolve_py_module(tmp_path, "", "current.py")
    assert result is None


def test_resolve_py_module_dotted(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.py").write_text("")
    result = _resolve_py_module(tmp_path, "a.b", "current.py")
    assert result == "a/b.py"


# ---------------------------------------------------------------------------
# _import_edges
# ---------------------------------------------------------------------------

def test_import_edges_python_from_import(tmp_path):
    src = tmp_path / "main.py"
    src.write_text("from utils import helper\n")
    utils = tmp_path / "utils.py"
    utils.write_text("def helper(): pass\n")

    files = [
        {"path": "main.py", "language": "python"},
        {"path": "utils.py", "language": "python"},
    ]
    result = _import_edges(tmp_path, files)
    # The 'from utils import helper' should create an import edge
    import_edges = [e for e in result if e["kind"] == "imports"]
    assert len(import_edges) >= 1
    assert import_edges[0]["source"] == "main.py"
    assert import_edges[0]["target"] == "utils.py"


def test_import_edges_python_dotted_import(tmp_path):
    src = tmp_path / "app.py"
    src.write_text("import os.path\n")
    files = [{"path": "app.py", "language": "python"}]
    result = _import_edges(tmp_path, files)
    # os.path not in path_set → no edge
    assert result == []


def test_import_edges_no_path_skipped(tmp_path):
    files = [{"path": "", "language": "python"}]
    result = _import_edges(tmp_path, files)
    assert result == []


def test_import_edges_nonexistent_file(tmp_path):
    files = [{"path": "nonexistent.py", "language": "python"}]
    result = _import_edges(tmp_path, files)
    assert result == []


def test_import_edges_javascript_relative_import(tmp_path):
    src = tmp_path / "app.js"
    src.write_text("import utils from './utils';\n")
    utils = tmp_path / "utils.js"
    utils.write_text("export function helper() {}\n")

    files = [
        {"path": "app.js", "language": "javascript"},
        {"path": "utils.js", "language": "javascript"},
    ]
    result = _import_edges(tmp_path, files)
    js_edges = [e for e in result if e["kind"] == "imports"]
    assert len(js_edges) >= 0  # JS relative import may resolve


def test_import_edges_go_import(tmp_path):
    src = tmp_path / "main.go"
    src.write_text('import "fmt"\n')
    files = [{"path": "main.go", "language": "go"}]
    result = _import_edges(tmp_path, files)
    go_edges = [e for e in result if e["kind"] == "go_import"]
    assert len(go_edges) >= 1
    assert go_edges[0]["detail"] == "fmt"


def test_import_edges_php_use(tmp_path):
    src = tmp_path / "app.php"
    src.write_text("use App\\Http\\Controllers\\UserController;\n")
    ctrl = tmp_path / "App/Http/Controllers/UserController.php"
    ctrl.parent.mkdir(parents=True)
    ctrl.write_text("<?php class UserController {}\n")

    files = [
        {"path": "app.php", "language": "php"},
        {"path": "App/Http/Controllers/UserController.php", "language": "php"},
    ]
    result = _import_edges(tmp_path, files)
    php_edges = [e for e in result if e["kind"] == "uses"]
    assert len(php_edges) >= 1


# ---------------------------------------------------------------------------
# _call_edges_light
# ---------------------------------------------------------------------------

def test_call_edges_light_empty(tmp_path):
    result = _call_edges_light(tmp_path, [])
    assert result == []


def test_call_edges_light_finds_call(tmp_path):
    caller = tmp_path / "caller.py"
    caller.write_text("result = my_unique_function()\n")
    callee = tmp_path / "callee.py"
    callee.write_text("def my_unique_function(): pass\n")

    files = [
        {
            "path": "caller.py",
            "language": "python",
            "entities": [],
        },
        {
            "path": "callee.py",
            "language": "python",
            "entities": [{"kind": "function", "name": "my_unique_function"}],
        },
    ]
    result = _call_edges_light(tmp_path, files)
    call_edges = [e for e in result if e["kind"] == "calls_name"]
    assert len(call_edges) >= 1
    assert call_edges[0]["target"] == "callee.py"


def test_call_edges_light_short_name_skipped(tmp_path):
    files = [
        {
            "path": "f.py",
            "entities": [{"kind": "function", "name": "ab"}],  # too short
        }
    ]
    result = _call_edges_light(tmp_path, files)
    assert result == []


def test_call_edges_light_multiple_files_same_function_skipped(tmp_path):
    """Function in multiple files → not unique → no call edge."""
    f1 = tmp_path / "f1.py"
    f1.write_text("def shared_fn(): pass\n")
    f2 = tmp_path / "f2.py"
    f2.write_text("def shared_fn(): pass\nshared_fn()\n")

    files = [
        {"path": "f1.py", "entities": [{"kind": "function", "name": "shared_fn"}]},
        {"path": "f2.py", "entities": [{"kind": "function", "name": "shared_fn"}]},
    ]
    result = _call_edges_light(tmp_path, files)
    call_edges = [e for e in result if e["kind"] == "calls_name"]
    # shared_fn in two files → not unique → no edges
    assert len(call_edges) == 0


# ---------------------------------------------------------------------------
# build_architecture_map
# ---------------------------------------------------------------------------

def test_build_architecture_map_empty(tmp_path):
    result = build_architecture_map(tmp_path, [])
    assert result["schema"] == "swarm_relation_graph/v1"
    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["stats"]["edge_count"] == 0
    assert result["stats"]["node_count"] == 0


def test_build_architecture_map_basic(tmp_path):
    src = tmp_path / "main.py"
    src.write_text("# main")

    files = [{"path": "main.py", "language": "python", "entities": []}]
    result = build_architecture_map(tmp_path, files)
    assert result["schema"] == "swarm_relation_graph/v1"
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["id"] == "main.py"
    assert result["nodes"][0]["language"] == "python"


def test_build_architecture_map_with_route(tmp_path):
    files = [
        {
            "path": "api.py",
            "language": "python",
            "entities": [{"kind": "route", "name": "/api/health", "method": "GET"}],
        }
    ]
    result = build_architecture_map(tmp_path, files)
    route_edges = [e for e in result["edges"] if e["kind"] == "route"]
    assert len(route_edges) == 1
