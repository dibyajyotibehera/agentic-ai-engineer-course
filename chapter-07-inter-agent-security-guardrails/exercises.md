# Exercises — Chapter 7: Inter-Agent Security & Guardrails

All exercises run in `code/` with only the Python standard library and a connected model: connect a model first — see "Connect a model" in the root README. The demo runs against a real model, so an attack either works or it does not on this run, and the demo reports what the model actually did rather than asserting a fixed script. The guardrails live outside the model — the harness, proxy, judge, scopes, gate and log — and that is the point: the deterministic tests cover the guardrails without a model, and the live tests cover the model-facing behaviour, skipping when no model backend is configured.

Run the starting point first:

```bash
cd code
python demo_agent.py
python -m unittest test_guardrails -v
```

On the author's machine the demo shows the vulnerable agent exfiltrating the customer record to the attacker's address, the guarded agent blocking it, and the guarded agent still summarising a clean ticket normally; the test suite has 28 tests (23 deterministic, 5 live ones that skip without a backend). Those three outcomes are your baseline — if your changes break the clean-ticket case, you have built an off switch, not a guardrail.

---

## Exercise 1 — Reproduce the indirect prompt injection, then stop it

**Difficulty:** Foundation

**Task:** Run `demo_agent.py` and read the vulnerable agent's output. Then, in your own words and in a short note (half a page), trace the attack end to end: where the attacker's text entered the system, which trust boundary it crossed, and which of the three legs of the [lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) — private data, untrusted content, external communication — each tool call supplied. Finish by deleting the sanitisation proxy from the guarded agent (comment out the `proxy.sanitise` call and pass the raw ticket text to the decision step instead) and re-running the demo, to see which of the remaining layers still stops the attack and which does not.

**Acceptance criteria:**

- Your note names the entry point (the ticket body), the boundary crossed (tool output treated as instruction), and all three trifecta legs with the tool that supplied each one.
- With the proxy removed, the demo still blocks the exfiltration — through the approval gate and the destination allow-list — and you can say which layer did it, citing the audit-log event that proves it.
- The clean-ticket case still summarises normally.

---

## Exercise 2 — Attack your own sanitisation proxy

**Difficulty:** Intermediate

**Task:** The proxy in `code/guardrails.py` is a heuristic, and heuristics can be beaten. Write five new poisoned tickets, each using a different evasion, and run each through `SanitisationProxy.sanitise` directly: a paraphrase with no "ignore previous instructions" wording (for example, a polite request phrased as a customer asking the agent to "helpfully include" account details in a reply to a third party); an instruction split across two lines so that no single line looks imperative; an instruction hidden in Unicode tag characters (U+E0000 to U+E007F) or zero-width characters; an instruction embedded in a fake system message or a fake assistant turn inside the ticket; and an instruction that only becomes dangerous when combined with a later, innocent-looking ticket (a two-turn attack). For each one, record whether the proxy removed it, whether the judge in `code/injection_judge.py` would still flag it, and which downstream layer — tool scopes, approval gate, destination allow-list — would have to catch it if the proxy and the judge both missed it.

**Acceptance criteria:**

- At least two of your five evasions slip past the proxy (the judge still has to catch them, or the downstream layers do). If none do, your evasions are too close to the patterns the proxy already knows — paraphrase harder.
- For each evasion that slips past, you name the downstream layer that catches it in the guarded agent, with the audit-log event that proves it, or you state honestly that no layer catches it and say what layer you would add.
- You write down one false positive you found or constructed — legitimate ticket text the proxy strips — and say how you would distinguish it from an attack in the audit log.

---

## Exercise 3 — Tool poisoning: a poisoned description tries to exfiltrate data

**Difficulty:** Intermediate

**Task:** Build a small Model Context Protocol-style server in Python (no MCP library needed — a plain function that returns tool definitions is enough) with two tools: a harmless `add_numbers` tool and a `read_file` tool. Give `add_numbers` a poisoned description, modelled on the [Invariant Labs disclosure](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks): the visible part says it adds two numbers, and a hidden block tells the model to read a sensitive file and include its contents as an extra argument. Register both tools in a `ToolRegistry` from `code/guardrails.py`, pin their descriptions, and then demonstrate two things: first, that a naive client that shows the user only the tool name and passes the full description to the model would follow the hidden instruction; second, that your registry's pin check, plus a review step that prints the full description to a human before pinning, catches the poison. Then simulate a rug pull: change the `read_file` description after pinning, and show the registry refusing the next call.

**Acceptance criteria:**

- The poisoned description is written out in full in your code or notes, with the hidden block clearly marked, so a reviewer can see the visibility gap between what the user sees (the name) and what the model sees (the whole description).
- The registry refuses the rug-pulled tool with a `PermissionError`, and the audit log contains a `tool_refused_rug_pull` event.
- You state, in one paragraph, why code review of the server alone does not catch this attack — the poison is in the description string, not in the code — and what review step would catch it.

---

## Exercise 4 — Build a sanitisation proxy that strips instructions from untrusted content

**Difficulty:** Advanced

**Task:** Write your own sanitisation proxy from scratch in a new file, `code/my_proxy.py`, without copying the pattern list from `code/guardrails.py`. Your proxy must handle, at minimum: instruction-like lines, invisible Unicode characters, a secret-shaped value (an API key or a private-key block), and content wrapped in fake system or assistant markers. It must return both the cleaned text and a structured report of what it removed, in the same spirit as `SanitisedContent`, so that the audit log can show the attack. Then wire your proxy into the guarded agent in `demo_agent.py` in place of the provided one, and run the full test suite plus the demo against it. Finally, write a short "known bypasses" note listing two attacks your proxy does not catch and the layer you would rely on instead for each.

**Acceptance criteria:**

- Your proxy passes the four sanitisation tests in `test_guardrails.py` (adapt them to import your proxy if you need to — and say that you did), and the demo's guarded agent still blocks the poisoned ticket while summarising the clean ticket.
- The report it returns distinguishes removed instructions, redacted secrets and removed invisible characters as separate counts or lists — a single boolean "was it bad?" is not enough, because the audit log needs to show what the attack was.
- Your "known bypasses" note names two real bypasses (a paraphrase and one other) and the downstream layer that must catch each one. A note that claims no bypasses exist fails this exercise.

---

## Exercise 5 — Least-privilege scoping for a real task

**Difficulty:** Intermediate

**Task:** Design the tool set for a billing-support agent that must do three things: read a ticket, read the customer's billing record, and issue a refund of up to a fixed limit. Give each tool the smallest set of scopes you can defend, assign each a risk tier, and decide which calls need human approval and which do not — and write down the reasoning for each decision, not just the answer. Then implement the set with `ToolRegistry` and `ApprovalGate` from `code/guardrails.py`, and demonstrate three cases in a short script: a legitimate refund within the limit that goes through (with approval, if you decided it needs one); a refund above the limit that is refused; and an injected instruction, smuggled in through a ticket, that tries to issue a refund to a different customer, which must be refused by the scopes or the gate rather than by the model noticing the injection.

**Acceptance criteria:**

- No tool has a scope it does not need for its own job — in particular, the ticket-reading tool cannot read billing records and the refund tool cannot read anything.
- The above-limit refund and the cross-customer refund are both refused, and for each you can point to the audit-log event and the layer that refused it.
- Your written reasoning says what the cost of a false positive is for each approval decision (a legitimate refund delayed) and what the cost of a false negative is (money moved), and the approval policy follows from that trade-off rather than from a blanket rule.

---

## Exercise 6 — Red-team the guardrail itself, and report utility alongside attack success

**Difficulty:** Advanced

**Task:** This is the exercise the design-review angle in the lesson is built on. Assemble a small evaluation of your own, in the spirit of [AgentDojo](https://arxiv.org/abs/2406.13352): ten benign tasks (for example, summarise ten clean tickets, each with a checkable outcome) and ten injection tasks (the same ten tickets, each with a different injected instruction hidden in it, each with a checkable attacker goal, such as a send to an outside address or a refund to the wrong customer). Run your guarded agent over all twenty, and report three numbers: benign utility (benign tasks completed), utility under attack (injected tasks where the benign task was still completed and the attacker goal was not met), and targeted attack success rate (injected tasks where the attacker goal was met). Then, as the adaptive attacker, write three new injections aimed specifically at the defences you built — one against the proxy's patterns, one against the approval flow, one against the audit log's blind spots — and run those too, reporting the same three numbers for the adaptive set. Write up the results in half a page, including the utility cost of your defences and the attack that broke them, if any did.

**Acceptance criteria:**

- All three numbers are reported for both the fixed set and the adaptive set, with the task counts stated (for example, "7 of 10"). A report that gives only the attack success rate, without utility, fails.
- At least one adaptive attack is attempted against each of the three targets (proxy, approval flow, audit log), and the write-up says honestly whether it succeeded.
- The write-up names the single weakest layer in your system as measured, not as asserted, and says what you would change first — and the change must be a harness change (a scope, a gate, a log), not "tell the model more firmly".
