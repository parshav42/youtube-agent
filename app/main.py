
from ollama_searv import run_ollama
from openai import OpenAI
from tool import tool

# Start Ollama
run_ollama()

# Connect to Ollama
client = OpenAI(
    base_url="http://127.0.0.1:11434/v1/",
    api_key="ollama"
)

# Conversation history
messages = [
    {
        "role": "system",
        "content": "You are a helpful assistant. Answer in 10 words or less."
    }
]

try: 
    while True:
        user_input = input("Me: ")

        if user_input.lower() == "exit":
            break

        # Store user message
        messages.append({
            "role": "user",
            "content": user_input
        })

        # Generate response
        result = client.chat.completions.create(
            model="qwen3:1.7b",
            messages=messages,
            Tool=tool,
            stream=True
        )

        # Collect streamed response
        assistant_response = ""

        print("AI: ", end="", flush=True)

        for chunk in result:
            content = chunk.choices[0].delta.content

            if content:
                print(content, end="", flush=True)
                assistant_response += content

        print()

        # Store assistant response
        messages.append({
            "role": "assistant",
            "content": assistant_response
        })
except KeyboardInterrupt:
    print("\nExiting...")