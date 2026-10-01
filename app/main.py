"""CLI for Hugging Face-backed scripts, Day 1 research, and video production."""

import argparse
import json
import sys
from typing import Any

from app.agents.research_agent import HuggingFaceResearchProvider
from app.config import get_database_path, get_niche, get_quality_threshold, get_topic
from app.database.db import get_latest_research, initialize_database
from app.graph.workflow import build_workflow
from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.schemas.script_schema import ScriptRequest


def main() -> int:
    """Dispatch script generation, research, HF connectivity, and video production."""
    parser = argparse.ArgumentParser(description="Produce original motivational YouTube Shorts.")
    parser.add_argument("--topic", help="Script topic (defaults to MOTIVATIONOS_TOPIC).")
    parser.add_argument("--duration", type=int, default=35, help="Script target in seconds (30-45).")
    parser.add_argument(
        "--draft-for-review",
        action="store_true",
        help="Save the generated script as needs_review even if automated checks pass.",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--research", action="store_true", help="Run the bundled Day 1 sample-topic workflow.")
    modes.add_argument(
        "--research-with-hf", action="store_true", help="Generate research ideas using configured Hugging Face inference."
    )
    modes.add_argument("--check-hf", action="store_true", help="Make and print a real HF model inference request.")
    modes.add_argument(
        "--produce-script", type=int, metavar="SCRIPT_ID", help="Produce video for a completed approved script record."
    )
    modes.add_argument(
        "--resume-production", metavar="PRODUCTION_ID", help="Resume a failed production record."
    )
    parser.add_argument("--niche", help="Research niche for either research mode.")
    args = parser.parse_args()

    if args.check_hf:
        return check_huggingface()
    database_path = get_database_path()
    initialize_database(database_path)

    if args.produce_script is not None:
        return produce_script(args.produce_script, database_path)
    if args.resume_production:
        return resume_production(args.resume_production, database_path)
    if args.research or args.research_with_hf:
        research_provider = None
        if args.research_with_hf:
            try:
                research_provider = HuggingFaceResearchProvider(
                    HuggingFaceLLMProvider.from_environment()
                )
            except Exception as error:
                print(f"Hugging Face research unavailable: {error}", file=sys.stderr)
                return 1
        workflow = build_workflow(
            research_provider=research_provider,
            database_path=str(database_path),
            quality_threshold=get_quality_threshold(),
        )
        return run_research(workflow, get_niche(args.niche), database_path, args.research_with_hf)

    try:
        request = ScriptRequest(topic=get_topic(args.topic), target_duration_seconds=args.duration)
        workflow = build_workflow(
            database_path=str(database_path), quality_threshold=get_quality_threshold()
        )
        result = workflow.invoke(
            {
                "script_request": request,
                "errors": [],
                "revision_attempts": 0,
                "require_human_review": args.draft_for_review,
            }
        )
    except Exception as error:
        print(f"Unable to generate a script: {error}", file=sys.stderr)
        return 1
    script = result.get("script")
    if script is None:
        print("No script was generated.", file=sys.stderr)
        if result.get("provider_limitation"):
            print(f"Provider limitation: {result['provider_limitation']}", file=sys.stderr)
        for error in result.get("errors", []):
            print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Topic: {script.topic} ({script.target_duration_seconds}s, {script.language})")
    print(f"Provider: {result.get('provider_name', 'unknown')}")
    print("Title candidates:")
    for index, title in enumerate(script.title_candidates, start=1):
        print(f"  {index}. {title}")
    print(f"Opening hook: {script.opening_hook}")
    print("Full narration:")
    print(script.narration)
    print("Scene breakdown:")
    for scene in script.scenes:
        print(
            f"  {scene.scene_number}. {scene.duration_seconds}s | {scene.narration}\n"
            f"     Visual: {scene.visual_description}\n"
            f"     Caption: {scene.caption}\n"
            f"     Transition: {scene.transition} | Audio: {scene.audio_direction}"
        )
    print("Quality evaluation (heuristic and HF review; not an engagement prediction):")
    print(json.dumps(result["evaluation"].model_dump(mode="json"), ensure_ascii=False, indent=2))
    record = result.get("script_record")
    print(f"Database record ID: {record['id'] if record else 'not saved'}")
    if result.get("script_json_path"):
        print(f"JSON file: {result['script_json_path']}")
    return 0


def check_huggingface() -> int:
    """Make one real connectivity inference without revealing the HF token."""
    try:
        provider = HuggingFaceLLMProvider.from_environment()
        response = provider.generate_text(
            "Reply with one short connectivity confirmation.",
            "Say: Hugging Face Inference Providers connectivity verified.",
            max_tokens=64,
        )
    except Exception as error:
        print(f"Hugging Face inference failed: {error}", file=sys.stderr)
        return 1
    print(f"Model: {provider.model}")
    print(f"Provider: {provider.provider_name}")
    print("Inference succeeded: yes")
    print(f"Response: {response}")
    print("Billing: usage and availability depend on the selected model/provider and account; check HF pricing.")
    return 0


def produce_script(script_id: int, database_path: str) -> int:
    """Run a real production attempt and report its persisted status."""
    workflow = build_workflow(database_path=database_path)
    result = workflow.invoke({"production_script_id": script_id}).get("production_result")
    if result is None:
        print(f"Unable to start production for script {script_id}.", file=sys.stderr)
        return 1
    return print_production(result)


def resume_production(production_id: str, database_path: str) -> int:
    """Resume a previously failed production using its saved intermediate files."""
    try:
        workflow = build_workflow(database_path=database_path)
        result = workflow.invoke({"resume_production_id": production_id}).get("production_result")
        if result is None:
            raise RuntimeError("The production workflow returned no result.")
    except Exception as error:
        print(f"Unable to resume production: {error}", file=sys.stderr)
        return 1
    return print_production(result)


def print_production(record: dict[str, Any]) -> int:
    print(f"Production ID: {record['id']}")
    print(f"Script ID: {record['script_id']}")
    print(f"Status: {record['status']}")
    print(f"Output directory: {record['output_dir']}")
    if record["status"] == "completed":
        print(f"Output MP4: {record['metadata']['video']['path']}")
        print("Validated media:")
        print(json.dumps(record["metadata"]["video"], ensure_ascii=False, indent=2))
        return 0
    if record.get("error"):
        print(f"Error: {record['error']}", file=sys.stderr)
    print(f"Intermediate metadata: {record['output_dir']}/metadata.json")
    print(f"Resume with: python -m app.main --resume-production {record['id']}")
    return 1


def run_research(workflow, niche: str, database_path, use_hf: bool) -> int:
    """Run Day 1 topic research with either bundled candidates or an explicit HF provider."""
    result = workflow.invoke({"niche": niche, "errors": []})
    print(f"Research niche: {niche}")
    print(
        "Research source: Hugging Face Inference Providers"
        if use_hf
        else "Research source: bundled sample ideas (not live trends)"
    )
    print("Selected topics:")
    selected = result.get("selected_topics", [])
    for index, topic in enumerate(selected, start=1):
        print(f"  {index}. {topic.topic} (score: {topic.final_score:.2f}/10)")
    for error in result.get("errors", []):
        print(f"Warning: {error}", file=sys.stderr)
    if not selected:
        print("  No topics selected.")
    print("Latest saved research record:")
    print(json.dumps(get_latest_research(database_path), ensure_ascii=False, indent=2))
    return 0 if selected and not result.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())