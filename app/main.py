from ollama_searv import run_ollama
from openai import OpenAI
import os

run_ollama()

client = OpenAI(
    base_url="http://127.0.0.1:11434/v1/" ,
    api_key="an"
)
me = []
while True:
    user_input = input('Me: ')
    if user_input.lower() == 'exit':
        break
    me.append({"role": "user", "content": user_input})
    me.append(user_input)
    result = client.chat.completions.create(
        model="qwen3:1.7b",
        messages=[{"role": "user", "content": user_input}],
        stream=True
    )
    
    

    for chunk in result:
        print(chunk.choices[0].delta.content,end='', flush=True)
        me.append(chunk.choices[0].delta.content)