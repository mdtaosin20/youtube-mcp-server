import os
import requests

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse


# Configuration
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
DEFAULT_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")
PORT = int(os.getenv("PORT", "8000"))


# MCP Server
mcp = FastMCP("YouTube Live Data Analyzer")


# Health check
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    return JSONResponse({
        "status": "ok",
        "service": "YouTube Live Data Analyzer"
    })


# YouTube API request helper
def youtube_get(endpoint, params):
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is not configured.")

    params["key"] = YOUTUBE_API_KEY

    url = f"https://www.googleapis.com/youtube/v3/{endpoint}"

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    return response.json()


# Get channel information
def get_channel_info(channel_id):
    data = youtube_get(
        "channels",
        {
            "part": "snippet,statistics",
            "id": channel_id
        }
    )

    items = data.get("items", [])

    if not items:
        raise ValueError(
            f"YouTube channel not found: {channel_id}"
        )

    channel = items[0]

    return {
        "channel_id": channel["id"],
        "channel_title": channel["snippet"]["title"],
        "subscribers": int(
            channel["statistics"].get("subscriberCount", 0)
        ),
        "total_views": int(
            channel["statistics"].get("viewCount", 0)
        ),
        "total_videos": int(
            channel["statistics"].get("videoCount", 0)
        )
    }


# Find current live stream
def find_live_video(channel_id):

    data = youtube_get(
        "search",
        {
            "part": "id,snippet",
            "channelId": channel_id,
            "eventType": "live",
            "type": "video",
            "maxResults": 1
        }
    )

    items = data.get("items", [])

    if not items:
        return None

    return items[0]["id"]["videoId"]


# Get live video information
def get_live_video_info(video_id):

    data = youtube_get(
        "videos",
        {
            "part": "snippet,statistics,liveStreamingDetails",
            "id": video_id
        }
    )

    items = data.get("items", [])

    if not items:
        raise ValueError(
            f"YouTube video not found: {video_id}"
        )

    video = items[0]

    statistics = video.get("statistics", {})
    live_details = video.get(
        "liveStreamingDetails",
        {}
    )

    return {
        "video_id": video["id"],
        "title": video["snippet"].get("title"),
        "channel_id": video["snippet"].get("channelId"),
        "channel_title": video["snippet"].get("channelTitle"),

        "view_count": int(
            statistics.get("viewCount", 0)
        ),

        "like_count": int(
            statistics.get("likeCount", 0)
        ),

        "comment_count": int(
            statistics.get("commentCount", 0)
        ),

        "concurrent_viewers": int(
            live_details.get(
                "concurrentViewers",
                0
            )
        ),

        "scheduled_start_time": live_details.get(
            "scheduledStartTime"
        ),

        "actual_start_time": live_details.get(
            "actualStartTime"
        ),

        "actual_end_time": live_details.get(
            "actualEndTime"
        )
    }


# Main MCP tool
@mcp.tool()
def get_youtube_live_data(
    channel_id: str = "",
    video_id: str = ""
) -> dict:
    """
    Get YouTube channel and current live stream data.
    """

    try:

        channel_id = (
            channel_id.strip()
            or DEFAULT_CHANNEL_ID
        )

        if not channel_id:
            return {
                "success": False,
                "error": "No YouTube channel ID provided."
            }

        # Get channel data
        channel = get_channel_info(channel_id)

        # Find live stream
        if video_id.strip():
            current_video_id = video_id.strip()
        else:
            current_video_id = find_live_video(
                channel_id
            )

        # No live stream
        if not current_video_id:
            return {
                "success": True,
                "live": False,
                "message": "Channel is not currently live.",
                "channel": channel
            }

        # Get live stream data
        live_video = get_live_video_info(
            current_video_id
        )

        return {
            "success": True,
            "live": True,
            "channel": channel,
            "live_stream": live_video
        }

    except requests.exceptions.RequestException as e:

        return {
            "success": False,
            "error": f"YouTube API request failed: {str(e)}"
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }


# Start server
if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=PORT
    )
