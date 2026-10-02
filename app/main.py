"""CLI for configured-LLM scripts, Day 1 research, and video production."""

import argparse
import json
import sys
from typing import Any

from app.agents.research_agent import HuggingFaceResearchProvider
from app.config import (
    get_database_path,
    get_licensed_asset_path,
    get_niche,
    get_quality_threshold,
    get_topic,
)
from app.database.db import (
    ScriptNotFoundError,
    ScriptReviewError,
    get_latest_research,
    get_script_by_id,
    initialize_database,
    review_script,
)
from app.graph.workflow import build_workflow
from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.providers.llm_factory import build_llm_provider
from app.schemas.script_schema import MotivationalScript, ScriptRequest
from app.video.image_generator import (
    ImageGenerationError,
    build_image_provider,
    generate_scene_images,
)
from app.youtube.manual_upload import export_upload_package


def main() -> int:
    """Dispatch script generation, research, HF connectivity, and video production."""
    parser = argparse.ArgumentParser(description="Produce original motivational YouTube Shorts.")
    parser.add_argument("--topic", help="Script topic (defaults to MOTIVATIONOS_TOPIC).")
    parser.add_argument("--duration", type=int, default=35, help="Script target in seconds (30-45).")
    parser.add_argument(
        "--draft-for-review",
        action="store_true",
        help="Deprecated compatibility flag; all generated scripts require explicit human review.",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--research", action="store_true", help="Run the bundled Day 1 sample-topic workflow.")
    modes.add_argument(
        "--research-with-hf", "--research-live", dest="research_with_hf", action="store_true",
        help="Generate research ideas using the configured live LLM provider."
    )
    modes.add_argument("--check-hf", action="store_true", help="Make and print a real HF model inference request.")
    modes.add_argument(
        "--produce-script", type=int, metavar="SCRIPT_ID", help="Produce video only from a script explicitly approved by a human."
    )
    modes.add_argument(
        "--resume-production", metavar="PRODUCTION_ID", help="Resume a failed production whose source script remains approved."
    )
    modes.add_argument(
        "--export-upload-package",
        metavar="PRODUCTION_ID",
        help="Export a finished production and its YouTube metadata for manual upload.",
    )
    modes.add_argument("--run-daily-pipeline", action="store_true", help="Run script generation through upload-package export; YouTube upload stays manual.")
    modes.add_argument(
        "--approve-script", type=int, metavar="SCRIPT_ID", help="Record explicit human approval of a passing script."
    )
    modes.add_argument(
        "--generate-images", type=int, metavar="SCRIPT_ID", help="Generate or reuse images for every scene in a script."
    )
    modes.add_argument(
        "--regenerate-image",
        nargs=2,
        type=int,
        metavar=("SCRIPT_ID", "SCENE_NUMBER"),
        help="Force regeneration of one scene image.",
    )
    parser.add_argument("--reviewer", help="Human reviewer name required with --approve-script.")
    parser.add_argument(
        "--debug", action="store_true", help="Re-raise exceptions caught by the video production workflow."
    )
    parser.add_argument("--niche", help="Research niche for either research mode.")
    args = parser.parse_args()

    if args.check_hf:
        return check_huggingface()
    database_path = get_database_path()
    initialize_database(database_path)

    if args.run_daily_pipeline:
        from app.daily_pipeline import DailyPipelineError, run_daily_pipeline
        try:
            result = run_daily_pipeline(database_path)
        except DailyPipelineError as error:
            print("=" * 60, file=sys.stderr)
            print(f"DAILY PIPELINE FAILED: {error.step}", file=sys.stderr)
            print(f"Reason: {error}", file=sys.stderr)
            print("Resume guidance:", file=sys.stderr)
            if error.production_id:
                print(f"  Production ID: {error.production_id}", file=sys.stderr)
                print(f"  Resume: python -m app.main --resume-production {error.production_id}", file=sys.stderr)
                print(f"  Export after approval: python -m app.main --export-upload-package {error.production_id}", file=sys.stderr)
            elif error.script_id:
                print(f"  Script ID: {error.script_id}; review it, then continue with --generate-images and --produce-script.", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            return 1
        except Exception as error:
            print(f"DAILY PIPELINE FAILED: configuration or unexpected error: {error}", file=sys.stderr)
            return 1
        print("=" * 60)
        print("DAILY PIPELINE COMPLETE")
        print("=" * 60)
        print(f"Script ID:     {result['script_id']}")
        print(f"Title:         {result['title']}")
        print(f"Duration:      {result['duration']}")
        print(f"Production ID: {result['production_id']}")
        print(f"Package path:  {result['package_path']}")
        print(f"Video file:    {result['video_path']}")
        print("\nNEXT STEP (manual):")
        print(f"  1. Open {result['package_path']}")
        print("  2. Upload final.mp4 to YouTube Studio")
        print('  3. Set visibility to Public, "Not made for kids"')
        print("  4. Copy title/description/tags from the .txt files")
        print("  5. Paste the YouTube URL back into the dashboard:")
        print("     http://127.0.0.1:8000/production-review")
        print("=" * 60)
        return 0

    if args.produce_script is not None:
        return produce_script(args.produce_script, database_path, debug=args.debug)
    if args.resume_production:
        return resume_production(args.resume_production, database_path, debug=args.debug)
    if args.export_upload_package:
        try:
            package_path = export_upload_package(args.export_upload_package, database_path)
        except (OSError, ValueError) as error:
            print(f"Unable to export upload package: {error}", file=sys.stderr)
            return 1
        print(f"Upload package: {package_path}")
        return 0
    if args.approve_script is not None:
        if not args.reviewer or not args.reviewer.strip():
            print("--reviewer is required with --approve-script.", file=sys.stderr)
            return 2
        return approve_script(args.approve_script, args.reviewer, database_path)
    if args.generate_images is not None:
        return generate_images(args.generate_images, database_path)
    if args.regenerate_image is not None:
        script_id, scene_number = args.regenerate_image
        return generate_images(
            script_id,
            database_path,
            scene_number=scene_number,
            force=True,
        )
    if args.research or args.research_with_hf:
        research_provider = None
        if args.research_with_hf:
            try:
                research_provider = HuggingFaceResearchProvider(
                    build_llm_provider()
                )
            except Exception as error:
                print(f"Live LLM research unavailable: {error}", file=sys.stderr)
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
    print("Quality evaluation (heuristic and LLM review; not an engagement prediction):")
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


def produce_script(script_id: int, database_path: str, *, debug: bool = False) -> int:
    """Run a real production attempt and report its persisted status."""
    workflow = build_workflow(database_path=database_path)
    state = workflow.invoke({"production_script_id": script_id, "debug": debug})
    result = state.get("production_result")
    if result is None:
        errors = state.get("errors", [])
        if errors:
            for error in errors:
                print(_format_production_error(str(error)), file=sys.stderr)
        else:
            print(
                f"Production blocked: workflow returned no result for script {script_id} and no diagnostic.",
                file=sys.stderr,
            )
        return 1
    return print_production(result)


def resume_production(production_id: str, database_path: str, *, debug: bool = False) -> int:
    """Resume a previously failed production using its saved intermediate files."""
    try:
        workflow = build_workflow(database_path=database_path)
        state = workflow.invoke({"resume_production_id": production_id, "debug": debug})
        result = state.get("production_result")
        if result is None:
            errors = state.get("errors", [])
            if errors:
                for error in errors:
                    print(_format_production_error(str(error)), file=sys.stderr)
                return 1
            raise RuntimeError("The production workflow returned no result and no diagnostic.")
    except Exception as error:
        if debug:
            raise
        print(f"Unable to resume production: {error}", file=sys.stderr)
        return 1
    return print_production(result)


def approve_script(script_id: int, reviewer: str, database_path: str) -> int:
    """Use the same transactional, passing-evaluation review path as the API."""
    try:
        record = review_script(
            script_id, "approved", reviewer=reviewer, database_path=database_path
        )
    except (ScriptNotFoundError, ScriptReviewError, ValueError) as error:
        print(f"Unable to approve script {script_id}: {error}", file=sys.stderr)
        return 1
    print(f"Script ID: {record['id']}")
    print(f"Status: {record['status']}")
    print(f"Reviewed by: {record['reviewed_by']}")
    print(f"Reviewed at: {record['reviewed_at']}")
    return 0


def generate_images(
    script_id: int,
    database_path: str,
    *,
    scene_number: int | None = None,
    force: bool = False,
) -> int:
    """Generate or list scene assets without starting video production."""
    record = get_script_by_id(script_id, database_path)
    if record is None:
        print(f"Script record {script_id} was not found.", file=sys.stderr)
        return 1
    library = get_licensed_asset_path()
    try:
        script = MotivationalScript.model_validate(record["script"])
        provider = build_image_provider()
    except (ImageGenerationError, ValueError) as error:
        print(f"LICENSES.json: {library / 'LICENSES.json'}")
        print(f"Image generation failed: {error}", file=sys.stderr)
        return 1
    selected_scenes = [
        scene for scene in script.scenes
        if scene_number is None or scene.scene_number == scene_number
    ]
    if not selected_scenes:
        print(f"Image generation failed: Script {script_id} has no scene {scene_number}.", file=sys.stderr)
        print(f"LICENSES.json: {library / 'LICENSES.json'}")
        return 1
    for scene in selected_scenes:
        existing = {
            path.resolve()
            for path in library.glob(f"scene_{scene.scene_number:02d}.*")
            if path.is_file()
        }
        try:
            paths = generate_scene_images(
                [scene],
                library_path=library,
                provider=provider,
                force=force,
            )
        except ImageGenerationError as error:
            print(f"LICENSES.json: {library / 'LICENSES.json'}")
            print(f"Image generation failed: {error}", file=sys.stderr)
            return 1
        for path in paths:
            if force or path.resolve() not in existing:
                print(f"Wrote image: {path}")
            else:
                print(f"Reused image: {path}")
    print(f"LICENSES.json: {library / 'LICENSES.json'}")
    return 0


def _format_production_error(error: str) -> str:
    """Add a practical next step to known local production prerequisites."""
    if "MOTIVATIONOS_TTS_PROVIDER must explicitly select" in error:
        return (
            "Production blocked: select MOTIVATIONOS_TTS_PROVIDER=edge-tts, or configure "
            "MOTIVATIONOS_TTS_PROVIDER=elevenlabs with ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID."
        )
    if "Licensed visual library unavailable" in error:
        return f"Production blocked: {error}"
    if "No supported licensed image found for scene" in error:
        return f"Production blocked: {error} Add a licensed image for every scene and update LICENSES.json."
    if "needs source, license, and rights_holder metadata" in error:
        return f"Production blocked: {error} Update LICENSES.json with complete provenance."
    if "Required media tool(s) unavailable" in error:
        return f"Production blocked: {error}"
    return f"Production blocked: {error}"


def print_production(record: dict[str, Any]) -> int:
    print(f"Production ID: {record['id']}")
    print(f"Script ID: {record['script_id']}")
    print(f"Status: {record['status']}")
    print(f"Output directory: {record['output_dir']}")
    if record["status"] in {"completed", "ready_for_manual_upload"}:
        print(f"Output MP4: {record['metadata']['video']['path']}")
        print("Validated media:")
        print(json.dumps(record["metadata"]["video"], ensure_ascii=False, indent=2))
        return 0
    if record.get("error"):
        print(_format_production_error(str(record["error"])), file=sys.stderr)
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
