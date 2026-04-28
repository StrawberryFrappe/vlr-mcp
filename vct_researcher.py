import os
import json
import config
from mcp.client.session import ClientSession
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

class VCTResearcher:
    def __init__(self, mcp_session: ClientSession):
        self.session = mcp_session
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.client = AsyncOpenAI(
            api_key=api_key if api_key else "dummy_key",
            base_url=config.BASE_URL
        )
        self.model = config.MODEL

    async def run(self, query: str):
        tools_response = await self.session.list_tools()
        
        openai_tools = []
        for tool in tools_response.tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema
                }
            })
            
        messages = [
            {"role": "system", "content": config.SYSTEM_PROMPT},
            {"role": "user", "content": query}
        ]

        while True:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=openai_tools,
                temperature=config.TEMPERATURE
            )
            
            message = response.choices[0].message
            
            if message.content:
                print(f"\n[Agent]: {message.content}")
            
            assistant_msg = {"role": "assistant", "content": message.content or ""}
            
            reasoning_content = getattr(message, "reasoning_content", None)
            if not reasoning_content and hasattr(message, "model_extra") and message.model_extra:
                reasoning_content = message.model_extra.get("reasoning_content")
                
            if reasoning_content:
                assistant_msg["reasoning_content"] = reasoning_content
                
            if message.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": t.id,
                        "type": t.type,
                        "function": {
                            "name": t.function.name,
                            "arguments": t.function.arguments
                        }
                    }
                    for t in message.tool_calls
                ]
            messages.append(assistant_msg)

            if not message.tool_calls:
                break

            for tool_call in message.tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)
                
                print(f"\n[Action]: Calling {func_name} with {func_args}...")
                
                try:
                    result = await self.session.call_tool(func_name, arguments=func_args)
                    result_text = "\n".join(c.text for c in result.content if c.type == "text")
                except Exception as e:
                    result_text = f"[SYSTEM ERROR] MCP server is unreachable or failed to execute tool. Details: {e}"
                
                # Check for Source URL metadata
                url_log = ""
                if result_text.startswith("[Source: "):
                    url_line = result_text.split("\n", 1)[0]
                    url = url_line.replace("[Source: ", "").replace("]", "").strip()
                    url_log = f" -> Looking at {url}\n"
                    
                print(f"{url_log}")
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": func_name,
                    "content": result_text[:config.MAX_TOKENS_PER_MESSAGE]
                })

