import sys
import asyncio
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from vct_researcher import VCTResearcher

async def start_demo():
    print("Starting VLR.gg MCP Server subprocess...")
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["vlr_server.py"]
    )
    
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            print("Connected to VLR MCP Server successfully.")
            
            researcher = VCTResearcher(session)
            
            print("\n" + "="*50)
            print(" VCT Research Agent Demo")
            print(" Using DeepSeek + VLR.gg MCP integration")
            print("="*50)
            
            while True:
                try:
                    query = input("\nWhat VCT topic should I investigate today? (or 'exit' to quit): ")
                    if query.strip().lower() in ('exit', 'quit'):
                        print("Shutting down the system...")
                        break
                    if not query.strip():
                        continue
                    
                    await researcher.run(query)
                except KeyboardInterrupt:
                    print("\nShutting down the system...")
                    break
                except Exception as e:
                    print(f"Error during execution: {e}")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(start_demo())
