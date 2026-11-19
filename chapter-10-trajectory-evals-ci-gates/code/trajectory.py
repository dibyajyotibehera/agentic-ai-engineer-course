"""Core types and distance metrics for trajectory evaluation.

A *trajectory* is the ordered record of everything an agent did on one
task: each reasoning step, each tool call and its result, and the final
outcome. A *golden directed acyclic graph* (golden DAG) is the fixture
that says what a good trajectory is allowed to look like: which steps
are required, which steps must come before which others, and which
branches are mutually exclusive alternatives.

This module is deliberately free of any model or network dependency so
that the whole evaluation pipeline runs offline in continuous
integration (CI). The distance metrics here are implemented from
scratch on plain Python lists, which is the point: a trajectory
distance is a small, auditable function, not a service you have to
trust.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Trajectories
# ---------------------------------------------------------------------------


@dataclass
class Step:
    """One step of an agent run.

    ``kind`` is ``"tool_call"``, ``"reasoning"`` or ``"message"``.
    ``tool`` is the tool name for a tool call and ``None`` otherwise.
    ``args`` holds the arguments the agent passed; ``result`` holds a
    short summary of what came back. Both are kept as plain data so a
    trajectory can be serialised to JSON and stored as a fixture.
    """

    kind: str
    tool: str | None = None
    args: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    text: str = ""

    def label(self) -> str:
        """The short name this step contributes to a tool sequence."""
        if self.kind == "tool_call" and self.tool:
            return self.tool
        return self.kind


@dataclass
class Trajectory:
    """One complete agent run, from the input to the final outcome."""

    case_id: str
    input: str
    steps: list[Step] = field(default_factory=list)
    final_outcome: str = ""

    def tool_sequence(self) -> list[str]:
        """The ordered list of tool names the agent called."""
        return [s.tool for s in self.steps if s.kind == "tool_call" and s.tool]

    def step_labels(self) -> list[str]:
        """The ordered list of labels for every step, tools included."""
        return [s.label() for s in self.steps]

    @classmethod
    def from_dict(cls, data: dict) -> "Trajectory":
        steps = [Step(**s) for s in data.get("steps", [])]
        return cls(
            case_id=data["case_id"],
            input=data.get("input", ""),
            steps=steps,
            final_outcome=data.get("final_outcome", ""),
        )

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "input": self.input,
            "steps": [
                {
                    "kind": s.kind,
                    "tool": s.tool,
                    "args": s.args,
                    "result": s.result,
                    "text": s.text,
                }
                for s in self.steps
            ],
            "final_outcome": self.final_outcome,
        }


# ---------------------------------------------------------------------------
# Golden DAGs
# ---------------------------------------------------------------------------


@dataclass
class GoldenDAG:
    """A golden trajectory written as a directed acyclic graph (DAG).

    Each node is one step the agent may take, named by its tool. An
    edge from node A to node B means A must happen before B. A node
    marked ``required`` must appear in every run that takes its branch.
    ``branches`` groups mutually exclusive paths: a run may follow one
    path of a branch, never two, so an approved refund and a denial
    cannot both appear in the same run.

    The DAG is deliberately more forgiving than a single golden
    sequence: any topological ordering of the graph is acceptable,
    which is how you allow an agent to reorder independent steps
    without failing the eval.
    """

    name: str
    nodes: dict[str, dict]  # node id -> {"tool", "required", "predecessors"}
    branches: list[dict] = field(default_factory=list)  # {"name", "paths": [[ids], ...]}

    @classmethod
    def from_dict(cls, data: dict) -> "GoldenDAG":
        nodes = {n["id"]: n for n in data["nodes"]}
        return cls(
            name=data["name"],
            nodes=nodes,
            branches=data.get("branches", []),
        )

    @classmethod
    def from_json_file(cls, path: str) -> "GoldenDAG":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def tool_to_node(self) -> dict[str, str]:
        """Map each tool name to the node id that performs it."""
        return {node["tool"]: node_id for node_id, node in self.nodes.items()}

    def validate(self, trajectory: Trajectory) -> dict:
        """Check a trajectory against this DAG.

        Returns a report with four lists:

        - ``precedence_violations``: a step ran before one of its
          predecessors, each as ``(tool, missing_predecessor_tool)``.
        - ``unknown_steps``: tool calls that appear nowhere in the DAG.
        - ``missing_required``: required steps that never ran, given
          the branch the run actually took.
        - ``branch_violations``: two mutually exclusive paths of one
          branch both appear in the run.
        """
        tool_to_node = self.tool_to_node()
        seen: list[str] = []  # node ids, in the order they ran
        precedence_violations: list[tuple[str, str]] = []
        unknown_steps: list[str] = []

        for tool in trajectory.tool_sequence():
            node_id = tool_to_node.get(tool)
            if node_id is None:
                unknown_steps.append(tool)
                continue
            for predecessor in self.nodes[node_id].get("predecessors", []):
                if predecessor not in seen:
                    precedence_violations.append(
                        (tool, self.nodes[predecessor]["tool"])
                    )
            seen.append(node_id)

        seen_set = set(seen)

        # Work out which branch path the run took, if any.
        excused: set[str] = set()  # nodes on a branch path not taken
        branch_violations: list[str] = []
        for branch in self.branches:
            touched = [
                path
                for path in branch["paths"]
                if any(node in seen_set for node in path)
            ]
            if len(touched) > 1:
                branch_violations.append(
                    f"branch {branch['name']!r}: run took more than one path "
                    f"({[self.nodes[n]['tool'] for p in touched for n in p]})"
                )
            elif len(touched) == 1:
                taken = set(touched[0])
                for path in branch["paths"]:
                    if set(path) != taken:
                        excused.update(path)
            # No path touched: nothing is excused yet; the required
            # check below will report the missing branch nodes.

        missing_required = [
            node["tool"]
            for node_id, node in self.nodes.items()
            if node.get("required", False)
            and node_id not in seen_set
            and node_id not in excused
        ]

        return {
            "precedence_violations": precedence_violations,
            "unknown_steps": unknown_steps,
            "missing_required": missing_required,
            "branch_violations": branch_violations,
            "valid": not (
                precedence_violations
                or unknown_steps
                or missing_required
                or branch_violations
            ),
        }


# ---------------------------------------------------------------------------
# Trajectory distance metrics (implemented from scratch)
# ---------------------------------------------------------------------------


def edit_distance(a: list[str], b: list[str]) -> int:
    """Levenshtein distance between two sequences of step labels.

    The minimum number of insertions, deletions and substitutions
    needed to turn sequence ``a`` into sequence ``b``. A distance of
    zero means the two runs took exactly the same steps in exactly the
    same order. This is the same dynamic-programming recurrence as
    string edit distance, applied to tool names instead of characters.
    """
    rows, cols = len(a) + 1, len(b) + 1
    table = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        table[i][0] = i
    for j in range(cols):
        table[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            table[i][j] = min(
                table[i - 1][j] + 1,  # delete a step from a
                table[i][j - 1] + 1,  # insert a step into a
                table[i - 1][j - 1] + cost,  # substitute, or keep
            )
    return table[-1][-1]


def normalized_edit_distance(a: list[str], b: list[str]) -> float:
    """Edit distance scaled to the range 0 (identical) to 1 (unrelated).

    Dividing by the length of the longer sequence makes the number
    comparable across runs of different lengths: swapping two steps in
    a four-step run scores 0.5, while the same swap in a twenty-step
    run scores 0.1, which matches the intuition that the longer run
    drifted less.
    """
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    return edit_distance(a, b) / longest


def lcs_length(a: list[str], b: list[str]) -> int:
    """Length of the longest common subsequence of two step sequences.

    Unlike edit distance, the longest common subsequence ignores
    ordering within the shared steps and only counts how much of the
    run survived intact, so it is a good companion metric: a run that
    reordered its steps but kept them all scores a perfect longest
    common subsequence while still paying an edit-distance penalty.
    """
    rows, cols = len(a) + 1, len(b) + 1
    table = [[0] * cols for _ in range(rows)]
    for i in range(1, rows):
        for j in range(1, cols):
            if a[i - 1] == b[j - 1]:
                table[i][j] = table[i - 1][j - 1] + 1
            else:
                table[i][j] = max(table[i - 1][j], table[i][j - 1])
    return table[-1][-1]


def lcs_similarity(a: list[str], b: list[str]) -> float:
    """Longest-common-subsequence similarity, 0 to 1, higher is closer."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    return lcs_length(a, b) / longest


def jaccard_similarity(a: list[str], b: list[str]) -> float:
    """Set overlap of the two step sets, ignoring order entirely.

    Useful as a coarse first signal: a run that called all the right
    tools in the wrong order still scores 1.0 here, which is exactly
    why order-aware metrics have to sit alongside it.
    """
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return 1.0
    return len(set_a & set_b) / len(set_a | set_b)


def trajectory_distance(run: list[str], golden: list[str]) -> dict:
    """All three distance metrics for one run against one golden path.

    ``run`` and ``golden`` are ordered tool-name sequences. The golden
    sequence here is one reference ordering through the DAG; a run that
    takes a different but still valid ordering will show a small
    non-zero edit distance, which is why the DAG validation in
    :meth:`GoldenDAG.validate` is the authority on correctness and
    these numbers are the authority on *how far* a run drifted.
    """
    return {
        "edit_distance": edit_distance(run, golden),
        "normalized_edit_distance": normalized_edit_distance(run, golden),
        "lcs_similarity": lcs_similarity(run, golden),
        "jaccard_similarity": jaccard_similarity(run, golden),
        "missing_steps": sorted(set(golden) - set(run)),
        "extra_steps": sorted(set(run) - set(golden)),
    }
