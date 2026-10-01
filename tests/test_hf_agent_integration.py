"""LangGraph agent integration with a fake response provider, not a live HF call."""

from app.agents.research_agent import HuggingFaceResearchProvider, ResearchTopicBatch
from app.agents.scene_agent import ScenePlan
from app.agents.script_agent import (
    HuggingFaceScriptGenerator,
    MockScriptGenerator,
    split_narration_for_scenes,
)
from app.agents.story_agent import StoryBeatPlan
from app.graph.workflow import build_workflow
from app.schemas import TopicCandidate
from app.schemas.script_schema import (
    LLMQualityReview,
    MotivationalScript,
    ScriptDraft,
    ScriptRequest,
    ScriptSceneDraft,
)


def make_script_draft(script: MotivationalScript) -> ScriptDraft:
    segments = split_narration_for_scenes(script.narration, len(script.scenes))
    return ScriptDraft(
        **script.model_dump(exclude={"scenes"}),
        scenes=[
            ScriptSceneDraft(
                **scene.model_dump(exclude={"narration", "caption"}),
                caption=" ".join(segment.split()[:5]),
            )
            for scene, segment in zip(script.scenes, segments, strict=True)
        ],
    )


def make_scene_plan(script: MotivationalScript) -> ScenePlan:
    segments = split_narration_for_scenes(script.narration, len(script.scenes))
    scenes = [
        scene.model_copy(update={"caption": " ".join(segment.split()[:5])})
        for scene, segment in zip(script.scenes, segments, strict=True)
    ]
    return ScenePlan(scenes=scenes)


class HuggingFaceFixture:
    """Explicit test double for typed HF responses; it makes no network requests."""

    provider_name = "test fixture for Hugging Face"
    model = "test/hf-model"

    def __init__(self):
        self.script = MockScriptGenerator().generate(ScriptRequest(topic="HF topic"))
        self.response_types = []

    def generate_structured(self, response_model, system_prompt, user_prompt, **kwargs):
        self.response_types.append(response_model)
        if response_model is ScriptDraft:
            return make_script_draft(self.script)
        if response_model is StoryBeatPlan:
            return StoryBeatPlan(story_beats=self.script.story_beats)
        if response_model is ScenePlan:
            return make_scene_plan(self.script)
        if response_model is LLMQualityReview:
            return LLMQualityReview(
                language_consistency=True,
                story_coherence=True,
                originality_safeguards=True,
                motivational_message=True,
                unsupported_factual_assertions=True,
                feedback=[],
            )
        if response_model is ResearchTopicBatch:
            return ResearchTopicBatch(
                candidates=[
                    TopicCandidate(
                        topic=f"Original topic idea {index}",
                        audience="Students and young professionals",
                        content_format="Cinematic short story",
                        relevance_score=8,
                        freshness_score=7,
                        story_potential=9,
                        source_type="LLM idea, not verified trend",
                    )
                    for index in range(8)
                ]
            )
        raise AssertionError(f"Unexpected structured schema requested: {response_model}")


class RevisingHuggingFaceFixture(HuggingFaceFixture):
    """Return one English-only draft, then a valid Hinglish draft for the revision."""

    def __init__(self):
        super().__init__()
        self.script_drafts = 0
        segments = [
            "Each morning, a student silenced the alarm and postponed starting. ",
            "The exam was close while the untouched book waited on the desk. ",
            "That evening the phone lit up, offering the familiar escape again. ",
            "Instead, the student opened one page and studied for ten minutes. ",
            "Next morning was ordinary; keeping one promise made the difference.",
        ]
        english_scenes = [
            scene.model_copy(update={"narration": segment})
            for scene, segment in zip(self.script.scenes, segments, strict=True)
        ]
        self.english_script = MotivationalScript.model_validate(
            self.script.model_copy(
                update={"narration": "".join(segments), "scenes": english_scenes}
            )
        )
        self.active_script = self.english_script

    def generate_structured(self, response_model, system_prompt, user_prompt, **kwargs):
        self.response_types.append(response_model)
        if response_model is ScriptDraft:
            self.script_drafts += 1
            self.active_script = self.english_script if self.script_drafts == 1 else self.script
            return make_script_draft(self.active_script)
        if response_model is StoryBeatPlan:
            return StoryBeatPlan(story_beats=self.active_script.story_beats)
        if response_model is ScenePlan:
            return make_scene_plan(self.active_script)
        if response_model is LLMQualityReview:
            return LLMQualityReview(
                language_consistency=True,
                story_coherence=True,
                originality_safeguards=True,
                motivational_message=True,
                unsupported_factual_assertions=True,
                feedback=[],
            )
        raise AssertionError(f"Unexpected structured schema requested: {response_model}")


def test_hf_provider_is_shared_across_day2_graph_agents(tmp_path) -> None:
    provider = HuggingFaceFixture()
    workflow = build_workflow(
        database_path=str(tmp_path / "hf-script.sqlite3"),
        json_output_directory=tmp_path / "json",
        script_generator=HuggingFaceScriptGenerator(provider),
        llm_provider=provider,
    )

    result = workflow.invoke({"script_request": {"topic": "focus"}})

    assert result["evaluation"].passed
    assert result["provider_name"] == provider.provider_name
    assert {
        ScriptDraft,
        StoryBeatPlan,
        ScenePlan,
        LLMQualityReview,
    }.issubset(set(provider.response_types))


def test_hf_research_provider_returns_validated_candidates(tmp_path) -> None:
    provider = HuggingFaceFixture()
    workflow = build_workflow(
        research_provider=HuggingFaceResearchProvider(provider),
        database_path=str(tmp_path / "hf-research.sqlite3"),
    )

    result = workflow.invoke({"niche": "student discipline"})

    assert len(result["candidates"]) == 8
    assert all(candidate.source_type == "LLM idea, not verified trend" for candidate in result["candidates"])
    assert ResearchTopicBatch in provider.response_types


def test_hf_revision_reuses_canonical_draft_parser_and_exact_scene_segments(tmp_path) -> None:
    provider = RevisingHuggingFaceFixture()
    workflow = build_workflow(
        database_path=str(tmp_path / "hf-revision.sqlite3"),
        json_output_directory=tmp_path / "revision-json",
        script_generator=HuggingFaceScriptGenerator(provider),
        llm_provider=provider,
    )

    result = workflow.invoke({"script_request": {"topic": "discipline"}})

    assert result["revision_attempts"] == 1
    assert provider.script_drafts == 2
    assert provider.response_types.count(ScriptDraft) == 2
    assert result["evaluation"].passed
    assert "".join(scene.narration for scene in result["script"].scenes) == result[
        "script"
    ].narration