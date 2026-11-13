import json
import os

import requests


def google_search(query: str) -> str:
    """Search Google Custom Search and return concise source information."""
    query = query.strip()
    if not query:
        raise ValueError("Google search query must not be empty.")

    api_key = os.getenv("GOOGLE_API_KEY")
    search_engine_id = os.getenv("GOOGLE_CSE_ID")
    if not api_key or not search_engine_id:
        raise RuntimeError("Google search requires GOOGLE_API_KEY and GOOGLE_CSE_ID.")

    response = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": api_key, "cx": search_engine_id, "q": query, "num": 5},
        timeout=15,
    )
    response.raise_for_status()

    return json.dumps(
        [
            {
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "url": item.get("link", ""),
            }
            for item in response.json().get("items", [])
        ],
        ensure_ascii=False,
    )


tool = [
    {
        "type": "function",
        "function": {
            "name": "google_search",
            "description": "Search Google for current information and return titles, snippets, and URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The topic or question to search for.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
]
