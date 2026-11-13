
import json

from dotenv import load_dotenv
from ollama_searv import run_ollama
from openai import OpenAI
from tool import google_search, tool

load_dotenv()

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

        # Ask the model whether it needs to search before answering.
        result = client.chat.completions.create(
            model="qwen3:1.7b",
            messages=messages,
            tools=tool,
        )

        tool_rounds = 0
        while result.choices[0].message.tool_calls:
            if tool_rounds >= 5:
                raise RuntimeError("The model exceeded the maximum tool-call rounds.")

            assistant_message = result.choices[0].message
            messages.append(assistant_message.model_dump(exclude_none=True))

            for tool_call in assistant_message.tool_calls:
                if tool_call.function.name != "google_search":
                    raise RuntimeError(
                        f"Unsupported tool requested: {tool_call.function.name}"
                    )

                arguments = json.loads(tool_call.function.arguments)
                tool_result = google_search(**arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

            tool_rounds += 1
            result = client.chat.completions.create(
                model="qwen3:1.7b",
                messages=messages,
                tools=tool,
            )

        assistant_response = result.choices[0].message.content or ""
        print(f"AI: {assistant_response}")
        messages.append({
            "role": "assistant",
            "content": assistant_response
        })
except KeyboardInterrupt:
    print("\nExiting...")