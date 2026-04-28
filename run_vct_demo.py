import os
import sys
import argparse
import asyncio
import config
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from vct_researcher import VCTResearcher

async def start_demo():
    parser = argparse.ArgumentParser(description="VCT Research Agent Demo")
    parser.add_argument("--simulate-timeout", action="store_true", help="Simulate a vlr.gg connection timeout")
    parser.add_argument("--simulate-error", action="store_true", help="Simulate a 503 vlr.gg server error")
    args = parser.parse_args()

    print("Starting VLR.gg MCP Server subprocess...")
    env = os.environ.copy()
    if args.simulate_timeout:
        env["VLR_SIMULATE_TIMEOUT"] = "1"
        print(" -> Simulation enabled: TIMEOUT")
    if args.simulate_error:
        env["VLR_SIMULATE_ERROR"] = "1"
        print(" -> Simulation enabled: 503 ERROR")

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["vlr_server.py"],
        env=env
    )
    
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            print("Connected to VLR MCP Server successfully.")
            
            researcher = VCTResearcher(session)
            
            print("\n" + "="*50)
            print(" VCT Research Agent Demo")
            print(f" Using {config.MODEL} + VLR.gg MCP integration")
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
