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
            "You are an expert VCT (Valorant Champions Tour) Research Intelligence Agent. "
            "Your core objective is analyzing esports sentiment, roster dynamics, and tactical metas using vlr.gg data. "
            "Mandatory Strict Workflow ('Discovery -> Investigation -> Synthesis'): "
            "1. DISCOVERY: Always begin using list tools (list_vlr_events, list_matches, list_vlr_threads) to scan the current landscape and establish valid numeric IDs. Never hallucinate IDs. If the first page doesn't have what you need or you need broader context, you may use the `page` parameter to fetch subsequent pages (page 2, 3, etc.). "
            "2. INVESTIGATION: Deep-dive into specific items using get_vlr_resource using exactly one unique ID category (e.g. resource_id for matches/threads, event_id for events). Read forum threads for community sentiment and match pages for technical results. "
            "3. PIVOT TO ENTITIES: When your analysis requires deep historical context, extract the entity ID and use get_vlr_resource(with player_id or team_id) to view that entity's profile. "
            "4. SYNTHESIS: Aggregate your findings into clear, actionable, and data-backed intelligence. "
            "Constraints & Operations: "
            "- Think step-by-step. Always output a precise rationale before calling any tool. "
            "- Use Pagination: Do NOT stop at page 1 if your task requires more extensive data. Actively explore multiple pages! "
            "- Maximize token efficiency. Only read resources strictly necessary to answer the prompt. "
            "- Ground all claims in retrieved data. Cite match results or community consensus where applicable. "
            "- FINAL ANSWER FORMAT: When you have finished your investigation and are providing the final synthesis to the user, clearly separate it from your logical thoughts using a Markdown divider (e.g. '---' followed by '### Final Analysis'). "
            "- SYSTEM FAILURES: If a tool returns a connection or access error (e.g. [SYSTEM ERROR], [HTTP Error], [Timeout Error]), DO NOT hallucinate data. Explain the network/system issue, apologize to the user, and stop further operations."
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
                    "content": result_text[:16000] # Safe token limit
                })
