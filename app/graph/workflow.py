"""Typed LangGraph workflows for topic research and motivational scripts."""

import json
from pathlib import Path
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.quality_agent import evaluate_script
from app.agents.research_agent import score_topics, select_top_topics
from app.agents.scene_agent import plan_scenes
from app.agents.script_agent import (
    HuggingFaceScriptGenerator,
    ScriptGenerator,
    build_script_generator,
)
from app.agents.story_agent import structure_story
from app.config import get_quality_threshold, get_speaking_rate_wps
from app.database.db import save_research, save_script
from app.providers.huggingface_llm import HuggingFaceConfigurationError, HuggingFaceLLMProvider
from app.providers.llm_factory import build_llm_provider
from app.schemas import SelectedTopic, TopicCandidate
from app.schemas.script_schema import (
    MotivationalScript,
    ScriptEvaluation,
    ScriptRequest,
)
from app.tools.topic_research import SampleTopicResearchProvider

MAX_REVISION_ATTEMPTS = 2


class ResearchProvider(Protocol):
    """Interface for sample or future live topic-research providers."""

    def research(self, niche: str) -> list[TopicCandidate]:
        """Return candidate topics for a niche."""


class ResearchState(TypedDict, total=False):
    """Shared typed state for both the preserved research and script workflows."""

    niche: str
    candidates: list[TopicCandidate]
    scored_topics: list[SelectedTopic]
    selected_topics: list[SelectedTopic]
    errors: list[str]
    script_request: ScriptRequest | dict[str, Any]
    selected_topic: str
    script: MotivationalScript
    evaluation: ScriptEvaluation
    revision_attempts: int
    provider_name: str
    provider_is_mock: bool
    provider_limitation: str
    script_record: dict[str, Any]
    script_json_path: str
    provider_available: bool
    production_script_id: int
    resume_production_id: str
    production_result: dict[str, Any]
    require_human_review: bool
    debug: bool


def build_workflow(
    research_provider: ResearchProvider | None = None,
    database_path: str | None = None,
    top_n: int = 3,
    script_generator: ScriptGenerator | None = None,
    quality_threshold: float | None = None,
    json_output_directory: str | Path | None = None,
    llm_provider: HuggingFaceLLMProvider | None = None,
    production_service: Any | None = None,
    speaking_rate_wps: float | None = None,
) -> CompiledStateGraph:
    """Build one graph with the existing research path and a script-generation path."""
    if top_n < 1:
        raise ValueError("top_n must be at least 1.")
    provider = research_provider or SampleTopicResearchProvider()
    generator = script_generator
    active_llm_provider = llm_provider
    threshold = get_quality_threshold() if quality_threshold is None else quality_threshold
    if not 0 <= threshold <= 10:
        raise ValueError("Quality threshold must be between 0 and 10.")
    speaking_rate = get_speaking_rate_wps() if speaking_rate_wps is None else speaking_rate_wps
    if not 1 <= speaking_rate <= 5:
        raise ValueError("Speaking rate must be between 1 and 5 words per second.")
    output_directory = (
        Path(json_output_directory)
        if json_output_directory is not None
        else Path(__file__).resolve().parents[2] / "data" / "scripts"
    )

    def resolve_llm_provider() -> HuggingFaceLLMProvider | None:
        nonlocal active_llm_provider
        if active_llm_provider is not None:
            return active_llm_provider
        if isinstance(generator, HuggingFaceScriptGenerator):
            active_llm_provider = generator.llm
            return active_llm_provider
        if generator is not None and generator.is_mock:
            return None
        active_llm_provider = build_llm_provider()
        return active_llm_provider

    def resolve_script_generator() -> ScriptGenerator:
        nonlocal generator
        if generator is None:
            generator = build_script_generator(resolve_llm_provider())
        return generator

    def research_node(state: ResearchState) -> dict[str, object]:
        """Collect candidates; provider errors are recorded and allow the graph to continue."""
        errors = list(state.get("errors", []))
        try:
            niche = state.get("niche", "").strip()
            if not niche:
                raise ValueError("The research niche must not be empty.")
            candidates = provider.research(niche)
        except Exception as error:
            candidates = []
            errors.append(f"research_node: {error}")
        return {"candidates": candidates, "errors": errors}

    def scoring_node(state: ResearchState) -> dict[str, object]:
        """Score all candidates, retaining a recoverable error if scoring fails."""
        errors = list(state.get("errors", []))
        try:
            scored = score_topics(state.get("candidates", []))
        except Exception as error:
            scored = []
            errors.append(f"scoring_node: {error}")
        return {"scored_topics": scored, "errors": errors}

    def selection_node(state: ResearchState) -> dict[str, object]:
        """Choose the highest-ranked topics."""
        errors = list(state.get("errors", []))
        try:
            selected = select_top_topics(state.get("scored_topics", []), top_n)
        except Exception as error:
            selected = []
            errors.append(f"selection_node: {error}")
        return {"selected_topics": selected, "errors": errors}

    def persistence_node(state: ResearchState) -> dict[str, object]:
        """Save the selected output; persistence failures are returned in shared state."""
        errors = list(state.get("errors", []))
        try:
            save_research(
                state.get("niche", ""),
                state.get("selected_topics", []),
                database_path=database_path,
            )
        except Exception as error:
            errors.append(f"persistence_node: {error}")
        return {"errors": errors}

    def script_generation_node(state: ResearchState) -> dict[str, object]:
        """Generate the first structured draft using the selected provider."""
        errors = list(state.get("errors", []))
        raw_request: ScriptRequest | dict[str, Any] = state.get("script_request", {})
        if not raw_request and state.get("selected_topic"):
            raw_request = {"topic": state["selected_topic"]}
        generator_used: ScriptGenerator | None = None
        provider_available = True
        try:
            generator_used = resolve_script_generator()
            request = ScriptRequest.model_validate(raw_request)
            script = MotivationalScript.model_validate(generator_used.generate(request))
        except HuggingFaceConfigurationError as error:
            script = None
            provider_available = False
            errors.append(f"script_generation_node: {error}")
        except Exception as error:
            script = None
            errors.append(f"script_generation_node: {error}")
        limitation = ""
        if generator_used is not None and generator_used.is_mock:
            limitation = (
                "An explicit test provider was injected; output is not a live model response."
            )
        elif script is None and errors:
            limitation = f"Configured live provider failed: {errors[-1]}"
        result: dict[str, object] = {
            "script": script,
            "revision_attempts": 0,
            "provider_name": generator_used.provider_name if generator_used else "Hugging Face Inference Providers",
            "provider_is_mock": generator_used.is_mock if generator_used else False,
            "provider_limitation": limitation,
            "provider_available": provider_available,
            "errors": errors,
        }
        return result

    def story_structure_node(state: ResearchState) -> dict[str, object]:
        """Make the six story beats reflect the generated scene progression."""
        script = state.get("script")
        if script is None:
            return {}
        try:
            return {"script": structure_story(script, resolve_llm_provider())}
        except Exception as error:
            errors = list(state.get("errors", []))
            errors.append(f"story_structure_node: {error}")
            return {"script": None, "errors": errors}

    def scene_planning_node(state: ResearchState) -> dict[str, object]:
        """Attach balanced 9:16 scene descriptions, captions, and audio directions."""
        script = state.get("script")
        if script is None:
            return {}
        try:
            return {"script": plan_scenes(script, resolve_llm_provider())}
        except Exception as error:
            errors = list(state.get("errors", []))
            errors.append(f"scene_planning_node: {error}")
            return {"script": None, "errors": errors}

    def quality_evaluation_node(state: ResearchState) -> dict[str, object]:
        """Evaluate heuristic quality or return explicit failed checks if no draft exists."""
        script = state.get("script")
        if script is None:
            failure = "No valid script is available to evaluate."
            return {
                "evaluation": ScriptEvaluation(
                    quality_score=0,
                    threshold=threshold,
                    passed=False,
                    language_consistency=False,
                    story_coherence=False,
                    originality_safeguards=False,
                    duration_feasibility=False,
                    repetition=False,
                    motivational_message=False,
                    scene_completeness=False,
                    caption_scene_alignment=False,
                    structural_labels_absent=False,
                    unsupported_factual_assertions=False,
                    unsupported_guarantees=False,
                    feedback=[failure],
                )
            }
        evaluation = evaluate_script(
            script,
            threshold,
            resolve_llm_provider(),
            speaking_rate_wps=speaking_rate,
        )
        scored_script = script.model_copy(
            update={"quality_score": evaluation.quality_score}
        )
        return {"script": MotivationalScript.model_validate(scored_script), "evaluation": evaluation}

    def revision_node(state: ResearchState) -> dict[str, object]:
        """Regenerate once with evaluator feedback, never exceeding the retry bound."""
        errors = list(state.get("errors", []))
        attempts = state.get("revision_attempts", 0) + 1
        request_data = state.get("script_request", {})
        if not request_data and state.get("selected_topic"):
            request_data = {"topic": state["selected_topic"]}
        try:
            request = ScriptRequest.model_validate(request_data)
            evaluation = state.get("evaluation")
            feedback = "; ".join(evaluation.feedback) if evaluation else "Improve quality checks."
            script = MotivationalScript.model_validate(
                resolve_script_generator().generate(request, feedback)
            )
            script = script.model_copy(
                update={"revision_notes": [*script.revision_notes, f"Revision {attempts}: {feedback}"]}
            )
            script = MotivationalScript.model_validate(script)
            limitation = state.get("provider_limitation", "")
        except Exception as error:
            errors.append(f"revision_node (attempt {attempts}): {error}")
            script = state.get("script")
            limitation = f"Revision attempt {attempts} failed: {error}"
        return {
            "script": script,
            "revision_attempts": attempts,
            "provider_limitation": limitation,
            "errors": errors,
        }

    def script_persistence_node(state: ResearchState) -> dict[str, object]:
        """Save the final script in SQLite and as a JSON artifact under data/."""
        script = state.get("script")
        evaluation = state.get("evaluation")
        if script is None or evaluation is None:
            return {}
        record = save_script(
            script,
            status="needs_review",
            evaluation=evaluation,
            database_path=database_path,
        )
        errors = list(state.get("errors", []))
        json_path = output_directory / f"script_{record['id']}.json"
        try:
            output_directory.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(script.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            errors.append(f"script_json_persistence_node: {error}")
        return {
            "script_record": record,
            "script_json_path": str(json_path),
            "errors": errors,
        }

    def video_production_node(state: ResearchState) -> dict[str, object]:
        """Delegate a requested persisted script/failed production to Day 3 service."""
        from app.video.production_service import ProductionService

        service = production_service or ProductionService(database_path=database_path)
        try:
            if state.get("resume_production_id"):
                record = service.resume(state["resume_production_id"])
            else:
                record = service.produce_script_record(state["production_script_id"])
            return {"production_result": record}
        except Exception as error:
            if state.get("debug"):
                raise
            errors = list(state.get("errors", []))
            errors.append(f"video_production_node: {error}")
            return {"errors": errors}

    def route_at_start(state: ResearchState) -> str:
        """Route script requests separately; no-request calls retain Day 1 behavior."""
        if state.get("production_script_id") is not None or state.get("resume_production_id"):
            return "video_production_node"
        return "script_generation_node" if (
            state.get("script_request") or state.get("selected_topic")
        ) else "research_node"

    def route_after_evaluation(state: ResearchState) -> str:
        """Continue to revision, persistence, or end at the bounded retry limit."""
        if state.get("script") is None:
            if not state.get("provider_available", True):
                return END
            return "revision_node" if state.get("revision_attempts", 0) < MAX_REVISION_ATTEMPTS else END
        evaluation = state.get("evaluation")
        if evaluation and evaluation.passed:
            return "script_persistence_node"
        if state.get("revision_attempts", 0) < MAX_REVISION_ATTEMPTS:
            return "revision_node"
        return "script_persistence_node"

    graph = StateGraph(ResearchState)
    graph.add_node("research_node", research_node)
    graph.add_node("scoring_node", scoring_node)
    graph.add_node("selection_node", selection_node)
    graph.add_node("persistence_node", persistence_node)
    graph.add_node("script_generation_node", script_generation_node)
    graph.add_node("story_structure_node", story_structure_node)
    graph.add_node("scene_planning_node", scene_planning_node)
    graph.add_node("quality_evaluation_node", quality_evaluation_node)
    graph.add_node("revision_node", revision_node)
    graph.add_node("script_persistence_node", script_persistence_node)
    graph.add_node("video_production_node", video_production_node)
    graph.add_conditional_edges(
        START,
        route_at_start,
        {
            "research_node": "research_node",
            "script_generation_node": "script_generation_node",
            "video_production_node": "video_production_node",
        },
    )
    graph.add_edge("research_node", "scoring_node")
    graph.add_edge("scoring_node", "selection_node")
    graph.add_edge("selection_node", "persistence_node")
    graph.add_edge("persistence_node", END)
    graph.add_edge("script_generation_node", "story_structure_node")
    graph.add_edge("story_structure_node", "scene_planning_node")
    graph.add_edge("scene_planning_node", "quality_evaluation_node")
    graph.add_conditional_edges(
        "quality_evaluation_node",
        route_after_evaluation,
        {
            "revision_node": "revision_node",
            "script_persistence_node": "script_persistence_node",
            END: END,
        },
    )
    graph.add_edge("revision_node", "story_structure_node")
    graph.add_edge("script_persistence_node", END)
    graph.add_edge("video_production_node", END)
    return graph.compile()