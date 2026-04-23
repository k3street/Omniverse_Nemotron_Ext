import asyncio
import json
import os
from service.isaac_assist_service.config import config
from service.isaac_assist_service.chat.provider_factory import get_llm_provider

async def main():
    print(f"LLM Mode: {config.llm_mode}")
    provider = get_llm_provider()
    print(f"Provider: {type(provider).__name__}")
    
    messages = [
        {"role": "user", "content": "Hello! What is 2+2? Please use a tool to calculate it if you can, otherwise just answer."}
    ]
    
    # Mock tool
    context = {
        "tools": [{
            "type": "function",
            "function": {
                "name": "calculate",
                "description": "Calculates math",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string"}
                    }
                }
            }
        }]
    }
    
    print("Testing completion...")
    try:
        resp = await provider.complete(messages, context)
        print("Response:", resp.text)
        print("Tool Calls:", json.dumps(resp.tool_calls, indent=2))
        
        if resp.tool_calls:
            print("\nSimulating tool return...")
            messages.append({
                "role": "assistant",
                "content": resp.text,
                "tool_calls": resp.tool_calls
            })
            messages.append({
                "role": "tool",
                "tool_call_id": resp.tool_calls[0]["id"],
                "content": '{"result": 4}'
            })
            
            resp2 = await provider.complete(messages, context)
            print("Response 2:", resp2.text)
            print("Tool Calls 2:", json.dumps(resp2.tool_calls, indent=2))

    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
