import os
import json
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
            base_url="https://api.deepseek.com"
        )
        self.model = "deepseek-chat"

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
            
        system_prompt = (
            "You are a specialized VCT (Valorant Champions Tour) Research Agent. "
            "Your task is to analyze esports sentiment, roster changes, and tactical trends using vlr.gg content. "
            "Follow a 'Discovery -> Investigation' workflow strictly: "
            "Step 1: Use list tools (e.g., list_vlr_matches, list_vlr_threads, list_vlr_results) to discover relevant resources and their numeric IDs. "
            "Step 2: Use get_vlr_resource with the discovered resource_ids to deep-dive into content. "
            "Step 3: Pivot to Team View using get_vlr_team when analyzing roster changes or team performance. Team IDs are found inside match resources. "
            "Always output a short rationale immediately before calling a tool. "
            "Optimize for token efficiency by reading only relevant IDs."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query}
        ]

        while True:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=openai_tools,
                temperature=0.3
            )
            
            message = response.choices[0].message
            
            if message.content:
                print(f"\n[Agent]: {message.content}")
            
            assistant_msg = {"role": "assistant", "content": message.content or ""}
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
                
                result = await self.session.call_tool(func_name, arguments=func_args)
                result_text = "\n".join(c.text for c in result.content if c.type == "text")
                
                print(f"[Result]: Fetched {len(result_text)} text characters.")
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": func_name,
                    "content": result_text[:16000] # Safe token limit
                })
