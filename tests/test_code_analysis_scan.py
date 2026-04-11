"""Tests for backend/App/workspace/infrastructure/code_analysis/scan.py."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


from backend.App.workspace.infrastructure.code_analysis.scan import (
    _entities_go,
    _entities_js_like,
    _entities_php,
    _entities_python,
    _extract_file,
    _rel_tree,
    analysis_to_json,
    analyze_workspace,
)


# ---------------------------------------------------------------------------
# _entities_python
# ---------------------------------------------------------------------------

def test_entities_python_class_and_function():
    source = """
class MyClass:
    pass

def my_function():
    pass
"""
    entities = _entities_python(source, "test.py")
    kinds = {e["kind"] for e in entities}
    names = {e["name"] for e in entities}
    assert "class" in kinds
    assert "function" in kinds
    assert "MyClass" in names
    assert "my_function" in names


def test_entities_python_async_function():
    source = "async def my_async(): pass"
    entities = _entities_python(source, "test.py")
    fn_names = [e["name"] for e in entities if e["kind"] == "function"]
    assert "my_async" in fn_names


def test_entities_python_syntax_error():
    source = "def broken("
    entities = _entities_python(source, "bad.py")
    assert len(entities) == 1
    assert entities[0]["kind"] == "parse_error"


def test_entities_python_empty():
    entities = _entities_python("", "empty.py")
    assert entities == []


def test_entities_python_nested():
    source = """
class Outer:
    class Inner:
        def method(self):
            pass
"""
    entities = _entities_python(source, "nested.py")
    names = {e["name"] for e in entities}
    assert "Outer" in names
    assert "Inner" in names
    assert "method" in names


# ---------------------------------------------------------------------------
# _entities_js_like
# ---------------------------------------------------------------------------

def test_entities_js_export_function():
    source = "export function myFunc() { return 1; }"
    entities = _entities_js_like(source, "file.js", "javascript")
    fn_names = [e["name"] for e in entities if e["kind"] == "function"]
    assert "myFunc" in fn_names


def test_entities_js_export_const_arrow():
    source = "export const myArrow = async (x) => x + 1;"
    entities = _entities_js_like(source, "file.js", "javascript")
    fn_names = [e["name"] for e in entities if e["kind"] == "function"]
    assert "myArrow" in fn_names


def test_entities_js_export_class():
    source = "export class MyComponent extends React.Component {}"
    entities = _entities_js_like(source, "file.tsx", "typescript")
    class_names = [e["name"] for e in entities if e["kind"] == "class"]
    assert "MyComponent" in class_names


def test_entities_js_route():
    source = "app.get('/api/users', handler);"
    entities = _entities_js_like(source, "routes.js", "javascript")
    routes = [e for e in entities if e["kind"] == "route"]
    assert len(routes) >= 1
    assert routes[0]["path"] == "/api/users"


def test_entities_js_flask_route():
    source = "@app.route('/home')\ndef home(): pass"
    entities = _entities_js_like(source, "app.py", "python")
    routes = [e for e in entities if e["kind"] == "route"]
    assert len(routes) >= 1


def test_entities_js_fastapi_route():
    source = "@router.get('/items/{id}')\nasync def get_item(id: int): pass"
    entities = _entities_js_like(source, "api.py", "python")
    routes = [e for e in entities if e["kind"] == "route"]
    assert len(routes) >= 1
    assert routes[0]["path"] == "/items/{id}"


def test_entities_js_default_export():
    source = "export default function App() { return <div/>; }"
    entities = _entities_js_like(source, "App.jsx", "javascript")
    components = [e for e in entities if e["kind"] == "component"]
    assert len(components) >= 1


def test_entities_js_empty():
    entities = _entities_js_like("", "empty.js", "javascript")
    assert entities == []


# ---------------------------------------------------------------------------
# _entities_go
# ---------------------------------------------------------------------------

def test_entities_go_function():
    source = "func MyHandler(w http.ResponseWriter, r *http.Request) {}"
    entities = _entities_go(source, "handler.go")
    fn_names = [e["name"] for e in entities if e["kind"] == "function"]
    assert "MyHandler" in fn_names


def test_entities_go_method():
    source = "func (s *Server) Start() error { return nil }"
    entities = _entities_go(source, "server.go")
    fn_names = [e["name"] for e in entities if e["kind"] == "function"]
    assert "Start" in fn_names


def test_entities_go_type():
    source = "type MyStruct struct { Name string }"
    entities = _entities_go(source, "types.go")
    type_names = [e["name"] for e in entities if e["kind"] == "type"]
    assert "MyStruct" in type_names


def test_entities_go_empty():
    entities = _entities_go("", "empty.go")
    assert entities == []


# ---------------------------------------------------------------------------
# _entities_php
# ---------------------------------------------------------------------------

def test_entities_php_class():
    source = "class UserController extends Controller {}"
    entities = _entities_php(source, "controller.php")
    class_names = [e["name"] for e in entities if e["kind"] == "class"]
    assert "UserController" in class_names


def test_entities_php_function():
    source = "function get_user($id) { return $id; }"
    entities = _entities_php(source, "helpers.php")
    fn_names = [e["name"] for e in entities if e["kind"] == "function"]
    assert "get_user" in fn_names


def test_entities_php_empty():
    entities = _entities_php("", "empty.php")
    assert entities == []


# ---------------------------------------------------------------------------
# _rel_tree
# ---------------------------------------------------------------------------

def test_rel_tree_file(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("print('hi')")
    result = _rel_tree(tmp_path, Path("hello.py"))
    assert result["type"] == "file"
    assert result["name"] == "hello.py"


def test_rel_tree_directory(tmp_path):
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "a.py").write_text("x = 1")
    result = _rel_tree(tmp_path, Path("."))
    assert result["type"] == "dir"
    assert any(c["name"] == "subdir" for c in result["children"])


def test_rel_tree_ignores_node_modules(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src").mkdir()
    result = _rel_tree(tmp_path, Path("."))
    child_names = {c["name"] for c in result["children"]}
    assert "node_modules" not in child_names
    assert "src" in child_names


# ---------------------------------------------------------------------------
# _extract_file
# ---------------------------------------------------------------------------

def test_extract_file_python(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("def hello(): pass")
    with patch(
        "backend.App.workspace.infrastructure.code_analysis.scan.extract_with_tree_sitter",
        return_value=None,
    ):
        result = _extract_file(f, "app.py", "python", tree_sitter_disabled=True)
    assert result["path"] == "app.py"
    assert result["language"] == "python"
    assert any(e["name"] == "hello" for e in result["entities"])


def test_extract_file_too_large(tmp_path):
    f = tmp_path / "big.py"
    # Write more than the _MAX_FILE_BYTES default (256000) — or mock it
    f.write_bytes(b"x" * 1000)
    with patch(
        "backend.App.workspace.infrastructure.code_analysis.scan._MAX_FILE_BYTES",
        10,
    ):
        result = _extract_file(f, "big.py", "python")
    assert result.get("skipped") == "too_large"


def test_extract_file_binary_skipped(tmp_path):
    f = tmp_path / "image.py"
    f.write_bytes(b"\x00\x01\x02binary content")
    result = _extract_file(f, "image.py", "python")
    assert result.get("skipped") == "binary"


def test_extract_file_go(tmp_path):
    f = tmp_path / "main.go"
    f.write_text("func main() {}")
    with patch(
        "backend.App.workspace.infrastructure.code_analysis.scan.extract_with_tree_sitter",
        return_value=None,
    ):
        result = _extract_file(f, "main.go", "go", tree_sitter_disabled=True)
    assert result["language"] == "go"


def test_extract_file_unknown_language(tmp_path):
    f = tmp_path / "file.vue"
    f.write_text("<template></template>")
    with patch(
        "backend.App.workspace.infrastructure.code_analysis.scan.extract_with_tree_sitter",
        return_value=None,
    ):
        result = _extract_file(f, "file.vue", "vue", tree_sitter_disabled=True)
    assert result["language"] == "vue"
    # Unknown lang falls back to file entity
    assert any(e["kind"] == "file" for e in result.get("entities", []))


# ---------------------------------------------------------------------------
# analyze_workspace
# ---------------------------------------------------------------------------

def test_analyze_workspace_not_a_dir(tmp_path):
    result = analyze_workspace(tmp_path / "nonexistent")
    assert result["error"] == "not_a_directory"


def test_analyze_workspace_empty_dir(tmp_path):
    with patch(
        "backend.App.workspace.infrastructure.code_analysis.scan.build_architecture_map",
        return_value={"schema": "swarm_relation_graph/v1", "edges": [], "nodes": []},
    ):
        result = analyze_workspace(tmp_path)
    assert result["schema"] == "swarm_code_analysis/v1"
    assert result["stats"]["scanned_files"] == 0


def test_analyze_workspace_with_python_files(tmp_path):
    (tmp_path / "app.py").write_text("def hello(): pass")
    with patch(
        "backend.App.workspace.infrastructure.code_analysis.scan.build_architecture_map",
        return_value={"schema": "swarm_relation_graph/v1", "edges": [], "nodes": []},
    ), patch(
        "backend.App.workspace.infrastructure.code_analysis.scan.extract_with_tree_sitter",
        return_value=None,
    ):
        result = analyze_workspace(tmp_path)
    assert result["stats"]["scanned_files"] == 1
    assert result["stats"]["by_language"].get("python") == 1


def test_analyze_workspace_language_filter(tmp_path):
    (tmp_path / "app.py").write_text("x = 1")
    (tmp_path / "main.go").write_text("func main() {}")
    with patch(
        "backend.App.workspace.infrastructure.code_analysis.scan.build_architecture_map",
        return_value={"schema": "swarm_relation_graph/v1", "edges": [], "nodes": []},
    ), patch(
        "backend.App.workspace.infrastructure.code_analysis.scan.extract_with_tree_sitter",
        return_value=None,
    ):
        result = analyze_workspace(tmp_path, languages_filter=["python"])
    langs = result["stats"]["by_language"]
    assert "python" in langs
    assert "go" not in langs


def test_analyze_workspace_ignores_venv(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "some.py").write_text("x = 1")
    with patch(
        "backend.App.workspace.infrastructure.code_analysis.scan.build_architecture_map",
        return_value={"schema": "swarm_relation_graph/v1", "edges": [], "nodes": []},
    ):
        result = analyze_workspace(tmp_path)
    assert result["stats"]["scanned_files"] == 0


def test_analyze_workspace_has_required_keys(tmp_path):
    with patch(
        "backend.App.workspace.infrastructure.code_analysis.scan.build_architecture_map",
        return_value={"schema": "swarm_relation_graph/v1", "edges": [], "nodes": []},
    ):
        result = analyze_workspace(tmp_path)
    for key in ("schema", "root", "generated_at", "file_tree", "files", "relation_graph", "stats"):
        assert key in result


# ---------------------------------------------------------------------------
# analysis_to_json
# ---------------------------------------------------------------------------

def test_analysis_to_json_valid():
    payload = {"key": "value", "list": [1, 2, 3]}
    result = analysis_to_json(payload)
    import json
    parsed = json.loads(result)
    assert parsed == payload


def test_analysis_to_json_non_ascii():
    payload = {"message": "Привет мир"}
    result = analysis_to_json(payload)
    assert "Привет мир" in result  # ensure_ascii=False
