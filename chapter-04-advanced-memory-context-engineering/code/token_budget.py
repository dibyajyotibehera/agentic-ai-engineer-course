"""Dynamic token budgeting and the cost-per-task arithmetic for Chapter 4.

Two pieces, kept separate because they answer different questions:

* :class:`TokenBudget` -- how many tokens each memory tier may spend on
  one task, how the allocation rebalances by task type, and how the system
  degrades in three stages when retrieved material does not fit.
* :class:`CostModel` -- what a task costs, using only verified prices.
  The one verified price used here is OpenAI's text-embedding-3-small at
  $0.02 per 1M tokens (OpenAI pricing, November 2026). The large language
  model price is a parameter you supply, so the arithmetic stays honest:
  no model price is invented. Pinecone read units are reported in read
  units, not dollars, because the chapter's verified Pinecone figure is
  the 1-read-unit-per-1-GB rate, not a dollar price.

No API key and no paid service are needed to run this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Verified price (OpenAI pricing, November 2026): text-embedding-3-small.
EMBEDDING_PRICE_PER_MILLION_TOKENS_USD = 0.02

# MemGPT's pressure constants (arXiv:2310.08560): warn at
# about 70% of the window, flush at 100%.
PRESSURE_WARNING_FRACTION = 0.70


@dataclass
class TierAllocation:
    working: int
    episodic: int
    semantic: int
    reserve: int

    @property
    def total(self) -> int:
        return self.working + self.episodic + self.semantic + self.reserve


# Default allocations per task type, for a 4,000-token memory budget. These
# are worked examples, not measured optima: Exercise 4 asks you to
# re-derive them from your own retrieval measurements.
DEFAULT_ALLOCATIONS = {
    "new_customer": TierAllocation(working=1600, episodic=1800, semantic=300, reserve=300),
    "returning_customer": TierAllocation(working=1600, episodic=1000, semantic=1000, reserve=400),
    "contested_policy": TierAllocation(working=1400, episodic=1200, semantic=600, reserve=800),
    "default": TierAllocation(working=1600, episodic=1400, semantic=600, reserve=400),
}


class TokenBudget:
    """A token budget for one task, with rebalancing and staged compression."""

    def __init__(self, total_tokens: int = 4000, task_type: str = "default") -> None:
        self.total_tokens = total_tokens
        self.task_type = task_type
        base = DEFAULT_ALLOCATIONS.get(task_type, DEFAULT_ALLOCATIONS["default"])
        scale = total_tokens / base.total
        self.allocation = TierAllocation(
            working=int(base.working * scale),
            episodic=int(base.episodic * scale),
            semantic=int(base.semantic * scale),
            reserve=total_tokens
            - int(base.working * scale)
            - int(base.episodic * scale)
            - int(base.semantic * scale),
        )
        self.usage: dict[str, int] = {"working": 0, "episodic": 0, "semantic": 0}
        self.warnings: list[str] = []

    def pressure(self) -> float:
        """Fraction of the total budget consumed so far."""
        return sum(self.usage.values()) / self.total_tokens

    def check_pressure(self) -> str | None:
        """MemGPT's rule in miniature: warn at 70%, flush at 100%."""
        fraction = self.pressure()
        if fraction >= 1.0:
            message = "flush: budget exhausted, oldest working turns must be summarised and evicted"
            self.warnings.append(message)
            return message
        if fraction >= PRESSURE_WARNING_FRACTION:
            message = f"warning: {fraction:.0%} of the token budget consumed"
            self.warnings.append(message)
            return message
        return None

    def compress(self, items: list[tuple[str, int]], budget: int) -> list[tuple[str, int]]:
        """Three-stage compression, in order of increasing loss.

        Stage 1 drops the lowest-ranked items (items are assumed ranked best
        first). Stage 2 summarises the survivors to roughly a tenth of their
        tokens. Stage 3 truncates what is left. Returns the items that fit,
        with their post-compression token counts.
        """
        total = sum(tokens for _, tokens in items)
        if total <= budget:
            return items
        # Stage 1: drop from the tail until it fits.
        kept = list(items)
        while kept and sum(tokens for _, tokens in kept) > budget:
            kept.pop()
        if kept and sum(tokens for _, tokens in kept) <= budget:
            return kept
        # Stage 2: summarise each survivor to ~10% of its tokens.
        summarised = [(f"SUMMARY: {text[:120]}", max(1, tokens // 10)) for text, tokens in kept]
        if sum(tokens for _, tokens in summarised) <= budget:
            return summarised
        # Stage 3: truncate the summaries to fit.
        result: list[tuple[str, int]] = []
        remaining = budget
        for text, tokens in summarised:
            if remaining <= 0:
                break
            take = min(tokens, remaining)
            result.append((text, take))
            remaining -= take
        return result


@dataclass
class TaskCost:
    """Cost of one task, broken down by tier, in US dollars."""

    working_usd: float
    episodic_usd: float
    semantic_usd: float
    embedding_usd: float
    pinecone_read_units: float = 0.0

    @property
    def llm_usd(self) -> float:
        return self.working_usd + self.episodic_usd + self.semantic_usd

    @property
    def total_usd(self) -> float:
        return self.llm_usd + self.embedding_usd


class CostModel:
    """Cost-per-task arithmetic.

    ``llm_price_per_million_input_tokens_usd`` is a parameter because the
    chapter invents no model price: pass in the price of the model you
    actually run. Everything else uses verified figures.
    """

    def __init__(self, llm_price_per_million_input_tokens_usd: float) -> None:
        self.llm_price = llm_price_per_million_input_tokens_usd

    def _llm_cost(self, tokens: int) -> float:
        return tokens * self.llm_price / 1_000_000

    def embedding_cost(self, tokens: int) -> float:
        """Embedding cost at the verified $0.02 per 1M tokens."""
        return tokens * EMBEDDING_PRICE_PER_MILLION_TOKENS_USD / 1_000_000

    def task_cost(
        self,
        *,
        working_tokens: int,
        episodic_tokens: int,
        semantic_tokens: int,
        embedded_tokens: int,
        pinecone_read_units: float = 0.0,
    ) -> TaskCost:
        return TaskCost(
            working_usd=self._llm_cost(working_tokens),
            episodic_usd=self._llm_cost(episodic_tokens),
            semantic_usd=self._llm_cost(semantic_tokens),
            embedding_usd=self.embedding_cost(embedded_tokens),
            pinecone_read_units=pinecone_read_units,
        )

    @staticmethod
    def pinecone_read_units(namespace_gb: float, queries: int) -> float:
        """Read units for tenant-scoped queries.

        Verified Pinecone rate: 1 read unit per 1 GB of namespace searched,
        minimum 0.25 read units per query.
        """
        return queries * max(0.25, namespace_gb)

    @staticmethod
    def semantic_cache_calls(total_calls: int, hit_rate: float) -> float:
        """Calls remaining after semantic caching at a given hit rate.

        The GPT Semantic Cache paper (arXiv:2411.05276) reports hit rates of
        61.6-68.8% and up to 68.8% fewer API calls. Pass a hit rate inside
        that verified range; do not extrapolate beyond it.
        """
        if not 0.0 <= hit_rate <= 1.0:
            raise ValueError("hit_rate must be between 0 and 1")
        return total_calls * (1.0 - hit_rate)


@dataclass
class BudgetReport:
    allocation: TierAllocation
    usage: dict[str, int] = field(default_factory=dict)
    decisive_memories: dict[str, int] = field(default_factory=dict)

    def tokens_per_decisive_memory(self, tier: str) -> float | None:
        """Tokens spent per memory that actually changed an answer.

        The ratio Exercise 4 uses to re-derive the allocation: the tier
        with the highest ratio is the tier whose budget should shrink.
        """
        decisive = self.decisive_memories.get(tier, 0)
        if decisive == 0:
            return None
        return self.usage.get(tier, 0) / decisive
