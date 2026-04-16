# Manual regression: UI pipeline summary & step taxonomy

The UI modules `shared/lib/step-taxonomy.ts` and `widgets/pipeline-graph/PipelineSummary.vue`
do not have a vitest harness in this repo (only Playwright E2E). Until a
proper unit-test framework lands, the invariants below are enforced via
`vue-tsc` + manual inspection:

1. `classifyStep("review_pm")` → `"reviewer"`
2. `classifyStep("human_pm")` → `"human_gate"`
3. `classifyStep("pm")` → `"agent"`
4. `classifyStep("analyze_code")` → `"tool_preflight"`
5. `classifyStep("spec_merge")` → `"join_branch"`
6. `classifyStep("dev_retry_gate")` → `"verification"`
7. `classifyStep("review_unknown_future")` → `"reviewer"` (prefix fallback)
8. `classifyStep("human_unknown_future")` → `"human_gate"` (prefix fallback)
9. `classifyStep("some_new_agent")` → `"agent"` (default)

`summarizeSteps(["pm", "review_pm", "human_pm", "ba"])` must produce:
```json
{"total": 4, "agent": 2, "reviewer": 1, "human_gate": 1,
 "verification": 0, "tool_preflight": 0, "join_branch": 0}
```

`PipelineSummary.vue` must:
- show `{n} steps` always;
- show `{agent} agents · {reviewer} reviewers · {human_gate} gates`
  chips only for non-zero counts;
- render `title="Pipeline breakdown: …"` tooltip with full line.

TODO: when vitest lands, port this checklist to a real `*.test.ts` file.
