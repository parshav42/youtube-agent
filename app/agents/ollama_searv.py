
import subprocess
import time
import requests

def ollama():
    print("Starting Ollama server...")

    # Check whether Ollama is already running
    try:
        requests.get("http://localhost:11434/api/tags", timeout=2)
        print("Ollama is already running.")
        return

    except requests.RequestException:
        pass

    # Start Ollama without blocking Python
    process = subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Wait until server becomes ready
    for _ in range(15):
        if process.poll() is not None:
            print("Ollama process stopped unexpectedly.")
            return

        try:
            response = requests.get(
                "http://localhost:11434/api/tags",
                timeout=2
            )

            if response.status_code == 200:
                print("Ollama server started successfully!")
                return

        except requests.RequestException:
            pass

        time.sleep(1)

    print("Ollama server did not become ready in time.")
    process.terminate()


ollama()
