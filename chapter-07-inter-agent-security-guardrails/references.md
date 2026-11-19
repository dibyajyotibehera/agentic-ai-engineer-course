# References — Chapter 7: Inter-Agent Security & Guardrails

Every link below was verified live on 18 November 2026 by opening the page or by a web search that returned the page. No link was invented. Where a number in the lesson comes from a paper, the paper is the source named; where a number comes from the authors' own evaluation, the lesson says so.

## Adapted material (with attribution)

- **Microsoft, AI Agents for Beginners, Lesson 6: Building Trustworthy AI Agents** — https://microsoft.github.io/ai-agents-for-beginners/06-building-trustworthy-agents/ — The adapted backbone of this chapter: the system-message framework, the threat classes and mitigations (task and instruction manipulation, access to critical systems, knowledge-base poisoning, cascading errors), human-in-the-loop approval gates and audit logging. Used under the MIT Licence; the source repository is https://github.com/microsoft/ai-agents-for-beginners.

## Syllabus inspiration only (proprietary — no content copied)

- **DeepLearning.AI, Governing AI Agents (with Databricks)** — https://www.deeplearning.ai/courses/governing-ai-agents — Shape only: the four pillars of agent governance (lifecycle management, risk management, security, observability) and least-privilege access through views, column-level masking and agent identities. No text, code or diagrams were copied.

## Further reading only (custom non-commercial licence — no content reused)

- **agents-towards-production, Agent Security with LlamaFirewall tutorial** — https://github.com/NirDiamant/agents-towards-production/tree/main/tutorials/agent-security-with-llamafirewall — Cited as a pointer for further reading only. None of its content was reused in this chapter.

## Primary sources on indirect prompt injection

- **"Not what you've signed up for: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection", arXiv:2302.12173 (February 2023)** — https://arxiv.org/abs/2302.12173 — The paper that named indirect prompt injection and demonstrated it against real systems, including Bing's GPT-4 powered chat, with a taxonomy covering data theft, worming and information-ecosystem contamination.
- **"The lethal trifecta for AI agents: private data, untrusted content, and external communication" (16 June 2025)** — https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/ — The necessary-conditions threat model this chapter is organised around: remove any one of the three legs and the exfiltration chain breaks.

## Standards and rankings

- **OWASP GenAI Security Project, Top 10 for LLM Applications** — https://genai.owasp.org/llm-top-10/ — Prompt injection is LLM01, the top risk; excessive agency (LLM06) and improper output handling (LLM05) are the neighbouring entries this chapter draws on.
- **OWASP Foundation, SQL Injection** — https://owasp.org/www-community/attacks/SQL_Injection — The classical injection analogy: trusted instructions and untrusted input glued into one string, with no reliable distinction between the control plane and the data plane.

## Tool poisoning

- **Invariant Labs, "MCP Security Notification: Tool Poisoning Attacks" (1 April 2025)** — https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks — The disclosure that named tool poisoning: malicious instructions embedded in a Model Context Protocol tool description that the model reads in full while the user interface never shows it, demonstrated against Cursor with a poisoned calculator tool exfiltrating a private SSH key.
- **"MCPTox: A Benchmark for Tool Poisoning Attack on Real-World MCP Servers", arXiv:2508.14925 (August 2025)** — https://arxiv.org/abs/2508.14925 — Built on 45 live Model Context Protocol servers and 353 authentic tools, with 1,348 malicious test cases across 10 risk categories; the authors report the highest refusal rate, for Claude 3.7 Sonnet, under 3%, and the most vulnerable model, o1-mini, succeeding in 72.8% of cases.

## Guardrail systems and structural defences

- **"LlamaFirewall: An open source guardrail system for building secure AI agents", arXiv:2505.03574 (May 2025)** — https://arxiv.org/abs/2505.03574 — PromptGuard 2 (a jailbreak classifier), AlignmentCheck (a chain-of-thought auditor for goal drift and indirect injection, labelled experimental by the authors) and CodeShield (static analysis of generated code). The AgentDojo figures quoted in the lesson — attack success 17.63% with no guardrail, 1.75% with PromptGuard 2 plus AlignmentCheck, utility 47.73% to 42.68% — are the authors' own results on the models and attack set they tested. The code lives in Meta's PurpleLlama repository: https://github.com/meta-llama/PurpleLlama/tree/main/LlamaFirewall
- **"Defeating Prompt Injections by Design" (CaMeL), arXiv:2503.18813 (March 2025)** — https://arxiv.org/abs/2503.18813 — The structural defence: control flow and data flow are extracted from the trusted query and executed in a custom interpreter, so untrusted data can never change program flow; capabilities on every value block exfiltration. The authors report solving 77% of AgentDojo tasks with provable security, against 84% for an undefended system.

## Evaluation

- **"AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents", arXiv:2406.13352 (June 2024)** — https://arxiv.org/abs/2406.13352 — The evaluation environment this chapter uses as its standard: 97 realistic tasks and 629 security test cases across workspace, Slack, travel and e-banking environments, with utility and attack success reported together.

## Production guardrails

- **Amazon Web Services, "Create your guardrail" — Guardrails for Amazon Bedrock** — https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-components.html — The managed guardrail this chapter maps its harness onto: content filters (including a prompt-attack category), denied topics, word filters and sensitive-information filters, applied to prompts and responses. Note the gap the lesson flags: the inline guardrail does not evaluate tool results, tool definitions or tool-call arguments, so it complements the harness in this chapter rather than replacing it.

## Style model for the course

- **LLM course** — https://github.com/mlabonne/llm-course — The style model for the whole repository: chapter shape, hands-on notebooks and references. Cited as such, not copied from.
