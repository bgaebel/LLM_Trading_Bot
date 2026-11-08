"""Which depots run, each GPT depot's model, prices and budget share (ADR-0012, ADR-0013)."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from llm_trading_bot.llm.budget import Pricing, amount
from llm_trading_bot.llm.due import Cadence
from llm_trading_bot.llm.openai_client import EFFORTS
from llm_trading_bot.paper.execution import BUYHOLD
from llm_trading_bot.paper.usdc import BUYHOLD_USDC, USDC_SUFFIX

# List prices in USD per 1M tokens taken 1:1 as EUR - rather too high, which is the safe side.
MODEL_PRICES: Mapping[str, Pricing] = {
    "gpt-6-luna": Pricing(Decimal("0.10"), Decimal("0.50")),
    "gpt-6.1-sol": Pricing(Decimal("2.00"), Decimal("10.00")),
}
# ADR-0012: luna trades, sol is the cross-check; asked in this order with the same snapshot.
LLM_DEFAULTS: Mapping[str, tuple[str, Decimal]] = {
    "gpt-luna": ("gpt-6-luna", Decimal("0.50")),
    "gpt-sol": ("gpt-6.1-sol", Decimal("2.50")),
}
DEFAULT_AGENTS = "gpt-luna,gpt-sol"
CLASSIC_AGENTS = ("trend", "breakout", "meanrev", "ensemble")
USDC_AGENTS = tuple(f"{name}{USDC_SUFFIX}" for name in CLASSIC_AGENTS)
"""Spec 020 (ADR-0030): the classic rules with BTC executed over BTC-USDC, cash in euro."""
USDC_FIELD = "BOT_USDC_AGENTS"
"""Spec 020: the USDC depots are chosen here, never in ``BOT_AGENTS``, so that a release from
before Spec 020 (rollback) still reads ``BOT_AGENTS`` and simply ignores this field. Missing
means all of them (AK4: on by default, also on a server with an explicit ``BOT_AGENTS``);
empty means none."""
BUYHOLD_FIELD = "BOT_BUYHOLD"
"""Spec 021 (ADR-0031): the euro buy & hold depot runs only with exactly ``1`` here; missing
means off. Set in the service's environment (drop-in), not in ``secrets.env``, which keeps only
the fields the web UI knows. A release from before Spec 021 always runs it (rollback)."""
MAX_OUTPUT_TOKENS = 8000


@dataclass(frozen=True)
class LlmAgent:
    name: str
    model: str
    pricing: Pricing | None
    """None: unknown model without configured prices - never called (ADR-0013)."""
    daily_eur: Decimal


@dataclass(frozen=True)
class LlmSettings:
    agents: tuple[LlmAgent, ...]
    total_daily_eur: Decimal
    effort: str
    cadence: Cadence
    api_key: str
    max_output_tokens: int = MAX_OUTPUT_TOKENS

    def __repr__(self) -> str:
        # Keep the key out of every log line and traceback that formats this object.
        return f"LlmSettings(agents={[a.name for a in self.agents]}, effort={self.effort!r})"


@dataclass(frozen=True)
class BotConfig:
    classic: tuple[str, ...]
    llm: LlmSettings | None
    usdc: tuple[str, ...] = ()
    """Selected USDC depots (Spec 020); empty: the bot runs exactly as before."""
    buyhold: bool = False
    """Spec 021 (ADR-0031): run the euro buy & hold depot. Off unless asked for, so nothing
    that builds a config by hand starts it by accident: also not the strategy lab (Spec 016),
    which must ask for it with ``buyhold=True`` where it compares with buy & hold."""

    @property
    def agents(self) -> tuple[str, ...]:
        return (
            self.classic + self.usdc + (tuple(a.name for a in self.llm.agents) if self.llm else ())
        )

    @property
    def benchmarks(self) -> tuple[str, ...]:
        """Buy & hold only when switched on (Spec 021); its USDC twin whenever a USDC depot runs
        (Spec 020). Opening, valuing and creating depots all go through ``depots``, so a depot
        left out here is not touched by a round at all."""
        euro = (BUYHOLD,) if self.buyhold else ()
        return euro + ((BUYHOLD_USDC,) if self.usdc else ())

    @property
    def depots(self) -> tuple[str, ...]:
        """Every depot of a round: the selected ones, then the benchmarks."""
        return self.agents + self.benchmarks


def _suffix(name: str) -> str:
    return name.upper().replace("-", "_")


def _llm_agent(env: Mapping[str, str], name: str) -> LlmAgent:
    default_model, default_share = LLM_DEFAULTS[name]
    suffix = _suffix(name)
    model = env.get(f"OPENAI_MODEL_{suffix}", default_model).strip() or default_model
    price_names = (f"LLM_PRICE_IN_EUR_{suffix}", f"LLM_PRICE_OUT_EUR_{suffix}")
    raw = [env.get(n) for n in price_names]
    if any(r is not None for r in raw) and not all(r is not None for r in raw):
        raise ValueError(f"{price_names[0]} and {price_names[1]} must be set together")
    pricing: Pricing | None
    if all(r is not None for r in raw):
        pricing = Pricing(amount(raw[0], price_names[0]), amount(raw[1], price_names[1]))
    else:
        pricing = MODEL_PRICES.get(model)
    share_name = f"LLM_DAILY_BUDGET_EUR_{suffix}"
    share = amount(env.get(share_name, str(default_share)), share_name)
    return LlmAgent(name, model, pricing, share)


def _names(env: Mapping[str, str], field: str, default: str, known: tuple[str, ...]) -> list[str]:
    names = [n.strip() for n in env.get(field, default).split(",") if n.strip()]
    if len(set(names)) != len(names):
        raise ValueError(f"{field} lists a depot twice: {names}")
    unknown = [n for n in names if n not in known]
    if unknown:
        hint = f" (USDC depots belong in {USDC_FIELD})" if set(unknown) & set(USDC_AGENTS) else ""
        raise ValueError(f"{field}: unknown depots {unknown}; known: {list(known)}{hint}")
    return names


def _switch(env: Mapping[str, str], field: str) -> bool:
    """Exactly ``1`` or ``0``; missing is off. Anything else is refused rather than guessed."""
    raw = env.get(field, "0")
    if raw not in ("0", "1"):
        raise ValueError(f"{field} must be 1 or 0: {raw[:20]!r}")
    return raw == "1"


def load_config(env: Mapping[str, str]) -> BotConfig:
    """Raises ValueError for anything unclear; nothing is opened or written before this passes."""
    buyhold = _switch(env, BUYHOLD_FIELD)
    names = _names(env, "BOT_AGENTS", DEFAULT_AGENTS, (*CLASSIC_AGENTS, *LLM_DEFAULTS))
    if not names:
        # Also with USDC depots chosen: a release from before Spec 020 needs one here.
        raise ValueError("BOT_AGENTS must name at least one depot besides the USDC depots")
    classic = tuple(n for n in names if n in CLASSIC_AGENTS)
    usdc = tuple(_names(env, USDC_FIELD, ",".join(USDC_AGENTS), USDC_AGENTS))
    llm_names = [n for n in LLM_DEFAULTS if n in names]
    if not llm_names:
        return BotConfig(classic, None, usdc, buyhold)
    api_key = env.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError(f"OPENAI_API_KEY is required for the GPT depots {llm_names}")
    effort = env.get("LLM_EFFORT", "low").strip()
    if effort not in EFFORTS:
        raise ValueError(f"LLM_EFFORT must be one of {list(EFFORTS)}: {effort!r}")
    total = amount(env.get("LLM_DAILY_BUDGET_EUR", "3"), "LLM_DAILY_BUDGET_EUR")
    settings = LlmSettings(
        tuple(_llm_agent(env, n) for n in llm_names),
        total,
        effort,
        Cadence.from_env(env),
        api_key,
    )
    return BotConfig(classic, settings, usdc, buyhold)
