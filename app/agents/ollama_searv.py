import subprocess


def  ollama():
    g = 'ollama serve'
    f = 'sudo systemctl stop ollama'

    try:
        subprocess.Popen(g, shell=True)
        
    except Exception as e:
        print(f"Error starting ollama: {e}")
        subprocess.Popen(f, shell=True)
        subprocess.Popen(g, shell=True)
        print("ollama restarted successfully.")




d = ollama()

print(d)