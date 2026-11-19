"""Exercise 4 — Cost arithmetic: price a screenshot task against a DOM task
(Foundation).

A cost model for computer-use tasks, anchored on the verified figures from
the lesson (Anthropic's documentation, via an explainer checked 28 Oct 2026):

  * declaring the computer toolset adds about 4,500 input tokens per request;
  * each screenshot costs roughly 1,000-1,800 input tokens;
  * a 30-step task works out to about 783,000 input tokens, about $1.61 on
    Claude Sonnet 5.5 at list prices.

The model behind the anchor: the agent loop re-sends the conversation on
every step, so the history — including every prior screenshot — is re-read
each time. With S input tokens per screenshot and T toolset tokens per
request, an N-step task costs N*T + S * N*(N+1)/2 input tokens. At the
midpoint S = 1,400 that gives 786,000 tokens for 30 steps, matching the
anchor's "about 783,000" within rounding; the script prints its own
derivation so you can see exactly where the number comes from.

The DOM path's per-step observation cost is deliberately a parameter, not a
hard-coded number: measure it yourself by capturing an accessibility-tree
snapshot of a real page with Playwright (Exercise 1's --a11y flag shows how)
and counting its characters at the rough heuristic of 4 characters per
token, then pass it with --dom-tokens. The default (300) is a clearly
labelled placeholder — replace it with your measurement.

Run (standard library only):
    python 04_cost_arithmetic.py [--dom-tokens 300]
                                 [--cache-fraction 0.5 --cache-discount 0.9]

--cache-fraction / --cache-discount model prompt caching (Exercise 4
stretch): that fraction of the repeated input is served from cache at that
discount (0.9 = 90% cheaper). Caching closes part of the gap; the per-step
screenshot is the part it cannot remove.
"""

from __future__ import annotations

import argparse

# Verified anchors (see module docstring).
TOOLSET_TOKENS_PER_REQUEST = 4_500
SCREENSHOT_TOKENS_LOW = 1_000
SCREENSHOT_TOKENS_HIGH = 1_800
ANCHOR_STEPS = 30
ANCHOR_TOKENS = 783_000
ANCHOR_COST_USD = 1.61

# Price per million input tokens, derived from the anchor — not a quoted
# price. $1.61 / 783,000 tokens = $2.06 per million, to two decimals.
PRICE_PER_MILLION_INPUT_TOKENS = ANCHOR_COST_USD / ANCHOR_TOKENS * 1_000_000


def screenshot_path_tokens(steps: int, screenshot_tokens: int) -> int:
    """Total input tokens for an N-step screenshot task, history included."""
    toolset = steps * TOOLSET_TOKENS_PER_REQUEST
    screenshots = screenshot_tokens * steps * (steps + 1) // 2
    return toolset + screenshots


def dom_path_tokens(steps: int, dom_tokens_per_step: int) -> int:
    """Total input tokens for an N-step DOM/accessibility-tree task.

    No computer toolset is declared and no images are sent; each step costs
    one structured text observation. A deterministic Playwright click costs
    no model tokens at all.
    """
    return steps * dom_tokens_per_step


def cost_usd(tokens: int) -> float:
    return tokens / 1_000_000 * PRICE_PER_MILLION_INPUT_TOKENS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dom-tokens", type=int, default=300,
                        help="input tokens per step for the DOM path "
                             "(PLACEHOLDER default — measure a real "
                             "accessibility snapshot and pass it here)")
    parser.add_argument("--screenshot-tokens", type=int, default=1_400,
                        help="input tokens per screenshot "
                             "(midpoint of the verified 1,000-1,800 range)")
    parser.add_argument("--cache-fraction", type=float, default=0.0,
                        help="fraction of repeated input served from cache")
    parser.add_argument("--cache-discount", type=float, default=0.9,
                        help="discount on cached input (0.9 = 90% cheaper)")
    args = parser.parse_args()

    print("Anchors (verified, see module docstring):")
    print(f"  toolset declaration: {TOOLSET_TOKENS_PER_REQUEST:,} input "
          "tokens per request")
    print(f"  screenshot: {SCREENSHOT_TOKENS_LOW:,}-"
          f"{SCREENSHOT_TOKENS_HIGH:,} input tokens each")
    print(f"  derived price: ${PRICE_PER_MILLION_INPUT_TOKENS:.2f} per "
          "million input tokens (from $1.61 / 783,000 tokens)")
    print(f"  DOM observation: {args.dom_tokens} tokens/step "
          f"({'PLACEHOLDER — measure a real snapshot' if args.dom_tokens == 300 else 'supplied by you'})")
    if args.cache_fraction:
        print(f"  prompt caching: {args.cache_fraction:.0%} of repeated "
              f"input at {args.cache_discount:.0%} discount")
    print()

    # Check the model against the anchor first, so the table is grounded.
    anchor_model = screenshot_path_tokens(ANCHOR_STEPS, args.screenshot_tokens)
    print(f"Anchor check — 30-step screenshot task: model says "
          f"{anchor_model:,} tokens / ${cost_usd(anchor_model):.2f}; "
          f"verified anchor is {ANCHOR_TOKENS:,} tokens / "
          f"${ANCHOR_COST_USD:.2f} (match within rounding).")
    print()

    print(f"  {'steps':>5s} | {'screenshot task':>16s} | "
          f"{'DOM task':>10s} | {'ratio':>6s} | {'screenshot / 1k tasks':>21s}")
    print("  " + "-" * 78)
    for steps in (5, 10, 30, 50, 100):
        shot = screenshot_path_tokens(steps, args.screenshot_tokens)
        dom = dom_path_tokens(steps, args.dom_tokens)
        if args.cache_fraction:
            # Only the repeated history (everything except the latest step's
            # fresh screenshot) can be cached; the newest screenshot is
            # always paid in full.
            repeated = shot - args.screenshot_tokens
            shot = int(args.screenshot_tokens
                       + repeated * (1 - args.cache_fraction)
                       + repeated * args.cache_fraction * (1 - args.cache_discount))
        ratio = shot / dom if dom else float("inf")
        print(f"  {steps:>5d} | ${cost_usd(shot):>7.2f} "
              f"({shot:>8,} tok) | ${cost_usd(dom):>6.2f} | "
              f"{ratio:>5.1f}x | ${cost_usd(shot) * 1000:>12,.2f}")

    # Break-even: the step count at which the screenshot path costs 10x the
    # DOM path. Solve N*T + S*N*(N+1)/2 = 10 * N * D for N.
    s, d = args.screenshot_tokens, args.dom_tokens
    # 4500 + S*(N+1)/2 = 10*D  =>  N = 2*(10*D - 4500)/S - 1
    breakeven = 2 * (10 * d - TOOLSET_TOKENS_PER_REQUEST) / s - 1
    print()
    if breakeven > 0:
        print(f"Break-even: at about {breakeven:.0f} steps the screenshot "
              f"path costs 10x the DOM path (at {args.dom_tokens} "
              "DOM tokens/step). Below that, the toolset declaration alone "
              "already makes screenshots the expensive option for short, "
              "repeatable work.")
    else:
        print("Break-even: the screenshot path already costs more than 10x "
              "the DOM path at a single step — the 4,500-token toolset "
              "declaration alone exceeds ten DOM observations.")
    print("\nNow answer the question the model exists for: for a workflow "
          "your team actually runs, how many tasks a month would the "
          "screenshot path have to handle before its extra cost exceeds one "
          "engineer-day of building the DOM path instead? State your "
          "assumptions explicitly; the point is the reasoning, not the number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
