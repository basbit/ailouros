from __future__ import annotations

from backend.App.spec.domain.dsl_block import extract_dsl_blocks
from backend.App.spec.domain.dsl_registry import make_default_registry
from backend.App.spec.domain.dsl_ts_sig import TypeScriptSignatureParser


def _block(body: str):
    markdown = "```typescript {dsl=ts-sig}\n" + body + "\n```\n"
    return extract_dsl_blocks(markdown)[0]


def test_ts_sig_parses_exported_function():
    block = _block("export function hashPassword(plain: string): string;")
    result = TypeScriptSignatureParser().parse(block)
    assert result.findings == ()
    functions = result.payload["functions"]
    assert functions[0]["name"] == "hashPassword"
    assert functions[0]["returns"] == "string"
    assert functions[0]["params"] == [{"name": "plain", "type": "string"}]


def test_ts_sig_parses_bare_function_without_export():
    block = _block("function add(a: number, b: number): number;")
    result = TypeScriptSignatureParser().parse(block)
    assert result.findings == ()
    functions = result.payload["functions"]
    assert functions[0]["name"] == "add"
    assert len(functions[0]["params"]) == 2


def test_ts_sig_parses_async_function():
    block = _block("export async function fetchUser(id: string): Promise<User>;")
    result = TypeScriptSignatureParser().parse(block)
    assert result.findings == ()
    functions = result.payload["functions"]
    assert functions[0]["name"] == "fetchUser"
    assert functions[0]["returns"] == "Promise<User>"


def test_ts_sig_parses_interface_with_multiple_methods():
    block = _block(
        "export interface Hasher {\n"
        "    hash(plain: string): string;\n"
        "    verify(plain: string, expected: string): boolean;\n"
        "}"
    )
    result = TypeScriptSignatureParser().parse(block)
    assert result.findings == ()
    interfaces = result.payload["interfaces"]
    assert interfaces[0]["name"] == "Hasher"
    member_names = {member["name"] for member in interfaces[0]["members"]}
    assert member_names == {"hash", "verify"}


def test_ts_sig_parses_type_alias():
    block = _block('export type Algorithm = "bcrypt" | "argon2";')
    result = TypeScriptSignatureParser().parse(block)
    assert result.findings == ()
    types = result.payload["types"]
    assert types[0]["name"] == "Algorithm"
    assert '"bcrypt"' in types[0]["expression"]


def test_ts_sig_parses_const_declaration():
    block = _block("export const DEFAULT_ROUNDS: number;")
    result = TypeScriptSignatureParser().parse(block)
    assert result.findings == ()
    constants = result.payload["constants"]
    assert constants[0]["name"] == "DEFAULT_ROUNDS"
    assert constants[0]["type"] == "number"


def test_ts_sig_flags_malformed_syntax():
    block = _block("export totally not a signature line")
    result = TypeScriptSignatureParser().parse(block)
    severities = {finding.severity for finding in result.findings}
    assert "error" in severities


def test_ts_sig_flags_empty_block():
    block = _block("")
    result = TypeScriptSignatureParser().parse(block)
    severities = {finding.severity for finding in result.findings}
    assert "error" in severities


def test_ts_sig_unclosed_interface_is_error():
    block = _block(
        "export interface Broken {\n"
        "    hash(plain: string): string;"
    )
    result = TypeScriptSignatureParser().parse(block)
    severities = {finding.severity for finding in result.findings}
    assert "error" in severities


def test_default_registry_knows_ts_sig():
    registry = make_default_registry()
    assert "ts-sig" in registry.known_kinds()
