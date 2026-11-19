# Chapter 10 code — Trajectory Evals & CI Gates

Standard-library-only Python. Nothing here needs a model, a network
connection, or an API key, except the two stubs named below.

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

## Run the tests

```bash
python -m unittest test_trajectory -v
```

## Files

| File | What it is |
| --- | --- |
| `trajectory.py` | `Trajectory`, `Step`, `GoldenDAG`, and the distance metrics (edit distance, longest common subsequence, Jaccard) implemented from scratch |
| `grader.py` | `StepLevelGrader`: ordering, required steps, branching, refund-argument reconciliation, outcome |
| `judge.py` | `MockJudge` (deterministic, offline), `LLMJudge` (real implementation over an OpenAI-compatible API; needs `JUDGE_API_KEY`), `measure_agreement` |
| `faithfulness.py` | Ragas-style faithfulness and context recall with a toy entailment check; `HHEMFaithfulness` is a stub for Vectara's HHEM-2.1-Open classifier |
| `ci_gate.py` | The eval runner, the baseline gate, and the canary verdict |
| `eval-gate.yml` | GitHub Actions workflow — copy to `.github/workflows/eval-gate.yml` |
| `data/` | The golden DAG, the four trajectories, and the committed baseline |
| `test_trajectory.py` | Unit tests, standard-library `unittest` only |
```

## Stubs that need a key or a download

- `LLMJudge` in `judge.py` needs `JUDGE_API_KEY` (and optionally
  `JUDGE_BASE_URL` for a non-OpenAI provider). Without it, it raises
  a clear error; use `MockJudge` offline.
- `HHEMFaithfulness` in `faithfulness.py` needs `transformers`,
  `torch`, and the `vectara/hallucination_evaluation_model` weights
  from Hugging Face. The mock scorer covers the same arithmetic
  offline.
