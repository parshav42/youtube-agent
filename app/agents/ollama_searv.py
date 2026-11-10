import os 
import schedule
import time

g ='ollama serve'
p='sudo systemctl stop ollama'

import subprocess

# 1. Run a simple command (e.g., 'ls' on Mac/Linux or 'dir' on Windows)
# 'shell=True' allows you to pass the command as a plain string\

def ollama():
    try:
        print("Okk! ollama serve is running")
        subprocess.run(g, shell=True)
        
    except:
        subprocess.run(p, shell=True)
        print("ollama serve is not running, stopping the service.")


def my_task():
    print("Task is running...")

# Schedule intervals
schedule.every(10).seconds.do(ollama)
schedule.every(10).seconds.do(my_task)
# schedule.every().hour.do(my_task)
# schedule.every().day.at("10:30").do(my_task)