"""parse_dev_qa_task_plan и шаг dev_lead (legacy: pm_tasks) в пайплайне."""

from langgraph_pipeline import (
    DEFAULT_PIPELINE_STEP_IDS,
    normalize_dev_qa_tasks_to_count,
    parse_dev_qa_task_plan,
    read_dev_qa_task_count_target,
    validate_pipeline_steps,
)


def test_parse_dev_qa_task_plan_fenced_json():
    raw = """Вот план:
```json
[
  {"id": "a", "title": "Auth", "development_scope": "login", "testing_scope": "e2e"}
]
```
"""
    tasks = parse_dev_qa_task_plan(raw)
    assert len(tasks) == 1
    assert tasks[0]["id"] == "a"
    assert tasks[0]["development_scope"] == "login"
    assert tasks[0]["testing_scope"] == "e2e"


def test_parse_dev_qa_task_plan_aliases():
    raw = '[{"id": 1, "name": "X", "scope": "devpart", "acceptance": "qapart"}]'
    tasks = parse_dev_qa_task_plan(raw)
    assert len(tasks) == 1
    assert tasks[0]["title"] == "X"
    assert tasks[0]["development_scope"] == "devpart"
    assert tasks[0]["testing_scope"] == "qapart"


def test_parse_dev_qa_task_plan_empty():
    assert parse_dev_qa_task_plan("") == []
    assert parse_dev_qa_task_plan("no json here") == []


def test_default_pipeline_includes_dev_lead_and_devops():
    assert "dev_lead" in DEFAULT_PIPELINE_STEP_IDS
    assert "devops" in DEFAULT_PIPELINE_STEP_IDS
    assert "analyze_code" in DEFAULT_PIPELINE_STEP_IDS
    assert "human_code_review" in DEFAULT_PIPELINE_STEP_IDS
    idx_spec = DEFAULT_PIPELINE_STEP_IDS.index("human_spec")
    idx_analyze = DEFAULT_PIPELINE_STEP_IDS.index("analyze_code")
    idx_doc = DEFAULT_PIPELINE_STEP_IDS.index("generate_documentation")
    idx_prob = DEFAULT_PIPELINE_STEP_IDS.index("problem_spotter")
    idx_ref = DEFAULT_PIPELINE_STEP_IDS.index("refactor_plan")
    idx_hcr = DEFAULT_PIPELINE_STEP_IDS.index("human_code_review")
    idx_devops = DEFAULT_PIPELINE_STEP_IDS.index("devops")
    idx_dev_lead = DEFAULT_PIPELINE_STEP_IDS.index("dev_lead")
    idx_dev = DEFAULT_PIPELINE_STEP_IDS.index("dev")
    assert (
        idx_spec
        < idx_analyze
        < idx_doc
        < idx_prob
        < idx_ref
        < idx_hcr
        < idx_devops
        < idx_dev_lead
        < idx_dev
    )
    validate_pipeline_steps(DEFAULT_PIPELINE_STEP_IDS)
    # Алиасы старых id из API/UI
    validate_pipeline_steps(["pm_tasks", "review_pm_tasks", "human_pm_tasks"])


def test_normalize_dev_qa_tasks_to_count_pad_and_trim():
    one = [{"id": "a", "title": "A", "development_scope": "d", "testing_scope": "q"}]
    padded = normalize_dev_qa_tasks_to_count(one, 3)
    assert len(padded) == 3
    assert padded[0]["id"] == "a"
    assert padded[2]["id"] == "3"

    many = [
        {"id": str(i), "title": str(i), "development_scope": "", "testing_scope": ""}
        for i in range(5)
    ]
    trimmed = normalize_dev_qa_tasks_to_count(many, 2)
    assert len(trimmed) == 2
    assert trimmed[0]["id"] == "0"


def test_read_dev_qa_task_count_target():
    assert read_dev_qa_task_count_target(None) is None
    assert read_dev_qa_task_count_target({}) is None
    assert read_dev_qa_task_count_target({"swarm": {"dev_qa_task_count": 4}}) == 4
    assert read_dev_qa_task_count_target({"swarm": {"dev_qa_task_count": 99}}) == 20


def test_read_dev_qa_task_count_target_dev_qa_split():
    assert read_dev_qa_task_count_target({"swarm": {"dev_task_count": 3}}) == 3
    assert read_dev_qa_task_count_target({"swarm": {"qa_task_count": 5}}) == 5
    assert read_dev_qa_task_count_target({"swarm": {"dev_task_count": 3, "qa_task_count": 7}}) == 7
    assert read_dev_qa_task_count_target({"swarm": {"dev_task_count": 9, "qa_task_count": 2}}) == 9
    # legacy single key побеждает, если задан
    assert read_dev_qa_task_count_target(
        {"swarm": {"dev_qa_task_count": 2, "dev_task_count": 9, "qa_task_count": 9}}
    ) == 2
