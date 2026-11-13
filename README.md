# Ollama Chat Assistant

A simple command-line chat assistant powered by Ollama. It can search Google
when it needs current information.

## Requirements

- Python
- [Ollama](https://ollama.com/)
- A Google Custom Search API key and Search Engine ID for web search

## Setup

Install the model and project dependencies:

```bash
ollama pull qwen3:1.7b
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

For Google search, create a `.env` file in the project folder:

```env
GOOGLE_API_KEY=your-google-api-key
GOOGLE_CSE_ID=your-search-engine-id
```

Keep `.env` private; do not commit your keys.

## Run

```bash
python app/main.py
```

Type a message to chat. Type `exit` to quit. Ollama must be running; on Linux,
the app attempts to start its `ollama` system service if it is stopped.
