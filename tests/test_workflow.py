"""Integration tests for the LangGraph topic research workflow."""

import sqlite3

from app.database.db import get_latest_research
from app.graph.workflow import build_workflow


def test_langgraph_runs_all_stages_and_persists_results(tmp_path) -> None:
    database_path = tmp_path / "workflow.sqlite3"
    workflow = build_workflow(database_path=str(database_path))

    result = workflow.invoke(
        {
            "niche": "Hindi English discipline and mindset",
            "candidates": [],
            "scored_topics": [],
            "selected_topics": [],
            "errors": [],
        }
    )

    assert len(result["candidates"]) >= 10
    assert len(result["scored_topics"]) == len(result["candidates"])
    assert len(result["selected_topics"]) == 3
    assert result["errors"] == []
    assert get_latest_research(database_path)["results"][0]["final_score"] == result[
        "selected_topics"
    ][0].final_score

    with sqlite3.connect(database_path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name = ?",
            ("table", "research_runs"),
        ).fetchone()
    assert table == ("research_runs",)


def test_workflow_records_invalid_niche_as_recoverable_error(tmp_path) -> None:
    database_path = tmp_path / "invalid.sqlite3"
    workflow = build_workflow(database_path=str(database_path))

    result = workflow.invoke({"niche": "", "errors": []})

    assert result["candidates"] == []
    assert result["selected_topics"] == []
    assert any("research_node" in error for error in result["errors"])
    assert any("persistence_node" in error for error in result["errors"])
    assert get_latest_research(database_path) is None