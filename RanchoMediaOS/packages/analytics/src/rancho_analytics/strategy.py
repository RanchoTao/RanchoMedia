from rancho_core.schemas import PerformanceFact, StrategyNote


def exploratory_bonuses(facts: list[PerformanceFact]) -> dict[str, float]:
    # Observed average affects an exploratory score, never statistical significance/promotion.
    return {
        fact.value: min(0.25, max(0.0, fact.mean_engagement_rate or 0.0))
        for fact in facts
        if fact.dimension == "topic"
    }


def strategy_notes(sample_size: int) -> list[StrategyNote]:
    return [
        StrategyNote(
            kind="HYPOTHESIS",
            text="Observed mock engagement may identify useful topics; "
            "confounding and small cohorts prevent causal/significance claims.",
        ),
        StrategyNote(
            kind="RECOMMENDATION",
            text=f"Use bounded exploratory topic bonuses and "
            f"collect matched-age observations. {sample_size} observed publications; "
            "human promotion required; real platform quality unverified.",
        ),
    ]
