from rancho_core.domain import Schema


class GeneratedDraft(Schema):
    title: str
    text: str
    model: str = "deterministic-fixture-v1"
    prompt_version: str = "fixture-v1"


class FixtureGenerator:
    def generate(self, topic: str, objective: str) -> GeneratedDraft:
        return GeneratedDraft(
            title=f"SIMULATION: {topic}",
            text=f"Fixture draft about {topic}. Objective: {objective}. "
            "Define the question, record the method, inspect evidence, then review. "
            "This synthetic text validates the media pipeline and needs human review.",
        )
