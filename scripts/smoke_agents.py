from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    # Ensure repo root is on PYTHONPATH when running as `python scripts/...`
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from backend.App.orchestration.infrastructure.agents.arch_agent import ArchitectAgent
    from backend.App.orchestration.infrastructure.agents.ba_agent import BAAgent
    from backend.App.orchestration.infrastructure.agents.dev_agent import DevAgent
    from backend.App.orchestration.infrastructure.agents.pm_agent import PMAgent
    from backend.App.orchestration.infrastructure.agents.qa_agent import QAAgent

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="test: сделать минимальный артефакт для проверки агентов",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="не вызывает Ollama, мокает ask_model",
    )
    args = parser.parse_args()

    agents = [
        ("pm", PMAgent()),
        ("ba", BAAgent()),
        ("arch", ArchitectAgent()),
        ("dev", DevAgent()),
        ("qa", QAAgent()),
    ]

    if args.dry_run:
        import agents.base_agent as base_agent

        def fake_ask_model(messages, model, temperature=0.2, **kwargs):
            # Show which model would have been used for the role
            role_marker = messages[0]["content"][:40].replace("\n", " ")
            text = f"dry-run ok. model={model}. sys_prefix={role_marker}..."
            return (text, {"input_tokens": 0, "output_tokens": 0, "model": model, "cached": False})

        base_agent.ask_model = fake_ask_model

    for name, agent in agents:
        print(f"=== {name} ===")
        print(f"role={agent.role} model={agent.model}")
        print(f"system_prompt_prefix={agent.system_prompt[:80].replace(chr(10), ' ')}...")
        out = agent.run(args.input)
        print(f"output_prefix={out[:120].replace(chr(10), ' ')}...")
        print()


if __name__ == "__main__":
    main()
