import os
import requests
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("YouTube MCP Server")

@mcp.tool()
def get_youtube_channel_stats() -> str:
    """Fetches live subscribers, views, and video counts for the TASIN PRO MAX YouTube channel."""
    webhook_url = "https://hook.us2.make.com/rdgf9cvuenfo95ht3dccc9fd80whevwd"
    try:
        response = requests.get(webhook_url)
        return response.text
    except Exception as e:
        return f"Error fetching data: {str(e)}"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mcp._app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
