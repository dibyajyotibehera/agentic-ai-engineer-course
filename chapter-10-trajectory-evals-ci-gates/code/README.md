# Chapter 10 code — Trajectory Evals & CI Gates

Standard-library-only Python. The core eval pipeline — trajectory
grading, golden DAGs, distance metrics, the CI gate and the
faithfulness arithmetic — runs offline, with no model, no network
connection, and no API key. The live path is the exception: the
real judge (`LLMJudge`), the real claim checker
(`LLMClaimChecker`), and `demo_live.py` need a connected model —
see "Connect a model" in the root README, then run the demo
key-based or with AWS Bedrock as shown below. The live tests in
`test_trajectory.py` skip when no backend is configured.

## Run the eval suite and the gate

```bash
python ci_gate.py
```

This grades the four trajectories in `data/trajectories.json`
against the golden DAG in `data/golden_refund_dag.json`, compares the
aggregates with `data/baseline.json`, and exits 0 on pass, 1 on
regression. **As shipped, it exits 1**: the dataset deliberately
contains the refund-before-eligibility trajectory, and that case is
marked critical in `ci_gate.py`, so the gate fails until the agent
is fixed. That is the gate working, not the gate broken — see
Exercise 5 in `../exercises.md` for the fix.

## Run the live demo (one real judge grade, one real claim check)

```bash
# Key-based (any OpenAI-compatible API):
LLM_API_KEY=<redacted> LLM_BASE_URL=<your provider's API base URL> LLM_MODEL=<your model name> python demo_live.py

# ... or with AWS Bedrock:
LLM_BACKEND=bedrock LLM_BEDROCK_MODEL_ID=<your inference profile ID> python demo_live.py
```

This grades the golden refund trajectory and the ordering-error
trajectory with the real judge, and scores the Ragas worked example
with the real claim checker, then prints the tokens and the cost.
As run on 22 November 2026: the judge passed the golden run and
failed the ordering-error run for refunding before the eligibility
check, the claim checker scored the worked example at 0.5, and the
whole demo cost $0.0032 (1,426 prompt + 301 completion tokens).

## Run the tests

```bash
python -m unittest test_trajectory -v
```

The deterministic tests need nothing. The three live tests at the
end need a configured backend and skip without one.

## Files

| File | What it is |
| --- | --- |
| `trajectory.py` | `Trajectory`, `Step`, `GoldenDAG`, and the distance metrics (edit distance, longest common subsequence, Jaccard) implemented from scratch |
| `grader.py` | `StepLevelGrader`: ordering, required steps, branching, refund-argument reconciliation, outcome |
| `judge.py` | `MockJudge` (deterministic, offline — the fixed instrument the chapter measures judges against; see below), `LLMJudge` (real judge through `llm_client.py`), `measure_agreement`, `trajectory_to_text` |
| `faithfulness.py` | Ragas-style faithfulness and context recall; the deterministic keyword check is the default, `LLMClaimChecker` is the same check with a real model; `HHEMFaithfulness` is a stub for Vectara's HHEM-2.1-Open classifier |
| `llm_client.py` | The shared standard-library client for an OpenAI-compatible chat API (copied from Chapter 7, so this chapter stays self-contained): environment configuration, retries, structured JSON replies, token and cost totals |
| `demo_live.py` | The small live path: one real trajectory graded by the real judge, one real faithfulness check, with the cost printed |
| `ci_gate.py` | The eval runner, the baseline gate, and the canary verdict |
| `eval-gate.yml` | GitHub Actions workflow — copy to `.github/workflows/eval-gate.yml` |
| `data/` | The golden DAG, the four trajectories, and the committed baseline |
| `test_trajectory.py` | Unit tests, standard-library `unittest` only; the three live tests skip without a model backend |

## The deliberate stand-in: MockJudge

`MockJudge` is the one mock in this chapter that stands in for a
model, and it stays on purpose. This chapter measures judges —
`measure_agreement` scores a judge against human labels — and that
measurement needs a judge whose verdicts are fixed and repeatable,
so the agreement arithmetic can be read and tested without a model
call whose verdict might change between runs. A real model judge is
the thing being measured here, not the thing doing the measuring;
the fixed judge is the instrument, and an instrument that wobbles
cannot calibrate anything. The real judge lives next to it in
`LLMJudge`, and `demo_live.py` shows it grading a real trajectory.

## Stubs that need a key or a download

- `HHEMFaithfulness` in `faithfulness.py` needs `transformers`,
  `torch`, and the `vectara/hallucination_evaluation_model` weights
  from Hugging Face. It is a stub for an optional local classifier,
  not a stand-in for a model call: the real claim check is
  `LLMClaimChecker` in the same file, and the deterministic keyword
  check covers the same arithmetic offline.
