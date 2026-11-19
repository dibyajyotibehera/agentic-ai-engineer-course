# References — Chapter 3: Computer Use and GUI Automation

Every link below was verified live on 18 November 2026. Numbers in the lesson trace to these sources or to the verified facts listed in the lesson; nothing here is cited from memory.

## Primary sources — the launch and the documentation

- Anthropic, "Computer use, Claude 3.5 Sonnet, and Claude 3.5 Haiku", 22 October 2024 — the launch announcement: computer use in public beta, Claude 3.5 Sonnet the first frontier model to offer it, 14.9% screenshot-only on OSWorld and 22.0% with more steps.
  https://www.anthropic.com/news/3-5-models-and-computer-use
- Anthropic, "Computer use tool" (documentation) — the reference implementation of the screenshot loop: the computer toolset, its 17 member tools including `screenshot`, `left_click`, `type` and `zoom`, batch actions, prompt-injection classifiers over returned screenshots, and the prompting guidance to place instruction text before the screenshot image and to use `zoom` for small text.
  https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/computer-use-tool
- OpenAI, "Introducing Operator", 23 January 2025 (updated 17 July 2025) — the Operator launch announcement: the Computer-Using Agent model, 38.1% on OSWorld, 58.1% on WebArena and 87% on WebVoyager, and the July 2025 update folding Operator into ChatGPT agent.
  https://openai.com/index/introducing-operator/

## The benchmark

- "OSWorld: Benchmarking Multimodal Agents for Open-Ended Tasks in Real Computer Environments", NeurIPS 2024 — the benchmark paper: 369 real desktop tasks on real virtual machines, execution-based binary grading, human baseline 72.36%.
  https://arxiv.org/abs/2404.07972
- OSWorld official site and leaderboard (maintained by the XLANG Lab) — the live leaderboard, including the OSWorld-Verified results released 28 July 2025.
  https://os-world.github.io/
- Agentropic, "Computer-Use Agents: What OSWorld Scores Really Tell You About Production Readiness" — the honest-reading essay the lesson draws on for the OSWorld-Verified ruler change, the self-reported nature of vendor scores, the 1.4–2.7x step-count gap against humans, and the capability cliff (UI-CUBE 67–85% atomic vs 9–19% complex workflows; WorkArena++ and OfficeBench corroboration).
  https://www.agentropic.ai/blog/computer-use-agents-osworld-reality/

## Grounding — Set-of-Mark prompting

- "Set-of-Mark Prompting Unleashes Extraordinary Visual Grounding in GPT-4V", arXiv:2310.11441, October 2023 — the paper: segment, overlay numbered marks, let the model select instead of guessing coordinates. GPT-4V on RefCOCOg from 25.7% to 86.4% (caveat: sub-sampled 177-instance set), referring segmentation 75.6% mean Intersection over Union, Flickr30K phrase grounding 89.2% Recall@1.
  https://arxiv.org/abs/2310.11441
- The same paper as PDF, for the figures and the experimental detail:
  https://arxiv.org/pdf/2310.11441

## The adapted hands-on source and the syllabus inspiration

- Microsoft, "Building Computer Use Agents (CUA)", Lesson 15 of AI Agents for Beginners (MIT licence) — the adapted skeleton of this chapter: Browser-Use with Playwright and the Chrome DevTools Protocol sharing one Chrome session, Azure OpenAI for vision reasoning, Pydantic for structured extraction, applied to finding the cheapest Airbnb stay in Stockholm. Adapted with attribution; re-platformed onto framework-neutral Python in this chapter.
  https://microsoft.github.io/ai-agents-for-beginners/15-browser-use/
- Microsoft, AI Agents for Beginners (repository, MIT licence) — the source course.
  https://github.com/microsoft/ai-agents-for-beginners
- DeepLearning.AI, "Building toward Computer Use with Anthropic" — syllabus inspiration only; proprietary, no content copied. Covers the Anthropic API, multimodal prompting, prompt caching and tool use, ending in a computer-use demo.
  https://www.coursera.org/projects/building-toward-computer-use-with-anthropic
- DeepLearning.AI, "Building AI Browser Agents" (Div Garg and Naman Garg) — syllabus inspiration only; proprietary, no content copied. Autonomous web agents with Playwright, including the AgentQ self-correction framework.
  https://learn.deeplearning.ai/courses/building-ai-browser-agents/lesson/rf3uq/intro-to-web-agents

## The tools

- Playwright, Python documentation — installation, the sync and async APIs, locators (`get_by_role`, `get_by_label`, `get_by_text`) and the accessibility-tree support this chapter's code uses.
  https://playwright.dev/python/docs/intro
- Puppeteer documentation — Google's Node.js library for driving Chrome (and Firefox) over the DevTools Protocol; the JavaScript counterpart to Playwright.
  https://pptr.dev
- Chrome DevTools Protocol, protocol viewer — the wire protocol underneath Playwright, Puppeteer and the shared-session hybrid in this chapter.
  https://chromedevtools.github.io/devtools-protocol/

## Style model for the course

- "LLM course" (Apache 2.0) — the style model for the whole repository: chapter shape, hands-on notebooks, references.
  https://github.com/mlabonne/llm-course

## A note on the cost figures

The token figures in the lesson's cost arithmetic — about 4,500 input tokens per request to declare the computer toolset, roughly 1,000–1,800 input tokens per screenshot, about 783,000 input tokens and about $1.61 for a 30-step task on Claude Sonnet 5.5 at list prices — come from Anthropic's documentation as summarised in an explainer checked on 28 October 2026. They are list-price figures for one model at one point in time; re-derive them from current pricing before quoting them in a production business case, and Exercise 4 shows how.
