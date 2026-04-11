"""coerce_mcp_config_dict: Cursor-style mcpServers → servers."""

from backend.App.integrations.infrastructure.mcp.stdio.session import coerce_mcp_config_dict, load_mcp_server_defs


def test_coerce_mcp_servers_cursor_shape():
    raw = {
        "mcpServers": {
            "mobile-mcp": {
                "command": "npx",
                "args": ["-y", "@mobilenext/mobile-mcp@latest"],
            },
            "rn": {"command": "npm", "args": ["install", "-g", "pkg"]},
        }
    }
    cfg = coerce_mcp_config_dict(raw)
    assert "servers" in cfg
    assert len(cfg["servers"]) == 2
    assert cfg["servers"][0]["name"] == "mobile-mcp"
    assert cfg["servers"][0]["command"] == "npx"


def test_coerce_preserves_existing_servers():
    inner = {"servers": [{"name": "x", "command": "true", "args": []}]}
    assert coerce_mcp_config_dict(inner) is inner


def test_load_mcp_defs_from_mcp_servers():
    defs = load_mcp_server_defs(
        {
            "mcpServers": {
                "a": {"command": "sh", "args": ["-c", "exit 0"]},
            }
        }
    )
    assert len(defs) == 1
    assert defs[0]["name"] == "a"
    assert defs[0]["command"] == ["sh", "-c", "exit 0"]
