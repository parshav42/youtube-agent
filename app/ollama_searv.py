
import subprocess
import time


def run_ollama():
    try:
        # Check whether Ollama is already running
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "ollama"]
        )

        if result.returncode == 0:
            print("Ollama service is already running.")
            return

        # Start Ollama service
        subprocess.run(
            ["sudo", "systemctl", "start", "ollama"],
            check=True
        )

        time.sleep(2)

        print("Ollama service started successfully.")

    except subprocess.CalledProcessError as e:
        print(f"Error starting Ollama: {e}")

    except FileNotFoundError:
        print("Ollama or systemctl command not found.")
