import os
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
import uvicorn

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, HTMLResponse


YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
DEFAULT_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

PORT = int(os.getenv("PORT", "10000"))

OAUTH_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"

TOKEN_FILE = "youtube_oauth_token.json"

oauth_state = secrets.token_urlsafe(32)


security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False
)

mcp = FastMCP(
    "YouTube Live Data Analyzer",
    transport_security=security
)


def get_redirect_uri():
    if not PUBLIC_BASE_URL:
        raise RuntimeError("PUBLIC_BASE_URL is not configured.")

    return f"{PUBLIC_BASE_URL}/oauth/callback"


def save_token(token):
    with open(TOKEN_FILE, "w", encoding="utf-8") as file:
        json.dump(token, file)


def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None

    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return None


def refresh_access_token(token):
    refresh_token = token.get("refresh_token")

    if not refresh_token:
        return None

    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        },
        timeout=20
    )

    response.raise_for_status()

    refreshed = response.json()

    token["access_token"] = refreshed["access_token"]

    if "expires_in" in refreshed:
        token["expires_at"] = (
            datetime.now(timezone.utc).timestamp()
            + refreshed["expires_in"]
        )

    save_token(token)

    return token


def get_valid_token():
    token = load_token()

    if not token:
        return None

    expires_at = token.get("expires_at", 0)

    if expires_at <= datetime.now(timezone.utc).timestamp() + 60:
        return refresh_access_token(token)

    return token


def get_access_token():
    token = get_valid_token()

    if not token:
        raise RuntimeError(
            "YouTube OAuth authorization is required. "
            "Open /oauth/start first."
        )

    return token["access_token"]


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    return JSONResponse({
        "status": "ok",
        "service": "YouTube Live Data Analyzer"
    })


@mcp.custom_route("/oauth/start", methods=["GET"])
async def oauth_start(request: Request):
    global oauth_state

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return JSONResponse({
            "success": False,
            "error": "Google OAuth credentials are not configured."
        }, status_code=500)

    oauth_state = secrets.token_urlsafe(32)

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": get_redirect_uri(),
        "response_type": "code",
        "scope": OAUTH_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": oauth_state
    }

    authorization_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urlencode(params)
    )

    return RedirectResponse(authorization_url)


@mcp.custom_route("/oauth/callback", methods=["GET"])
async def oauth_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code:
        return JSONResponse({
            "success": False,
            "error": "Authorization code was not provided."
        }, status_code=400)

    if state != oauth_state:
        return JSONResponse({
            "success": False,
            "error": "Invalid OAuth state."
        }, status_code=400)

    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": get_redirect_uri(),
            "grant_type": "authorization_code"
        },
        timeout=20
    )

    if not response.ok:
        return JSONResponse({
            "success": False,
            "error": response.text
        }, status_code=400)

    token = response.json()

    if "expires_in" in token:
        token["expires_at"] = (
            datetime.now(timezone.utc).timestamp()
            + token["expires_in"]
        )

    save_token(token)

    return HTMLResponse("""
    <html>
    <body style="font-family:Arial;padding:40px">
        <h2>YouTube authorization successful</h2>
        <p>You can now return to Gemini.</p>
    </body>
    </html>
    """)


@mcp.custom_route("/oauth/status", methods=["GET"])
async def oauth_status(request: Request):
    token = get_valid_token()

    return JSONResponse({
        "authorized": bool(token),
        "scope": OAUTH_SCOPE
    })


def youtube_get(endpoint, params):
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is not configured.")

    params = dict(params)
    params["key"] = YOUTUBE_API_KEY

    url = f"https://www.googleapis.com/youtube/v3/{endpoint}"

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    return response.json()


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
    statistics = channel.get("statistics", {})

    return {
        "channel_id": channel["id"],
        "channel_title": channel["snippet"]["title"],
        "subscribers": int(
            statistics.get("subscriberCount", 0)
        ),
        "total_views": int(
            statistics.get("viewCount", 0)
        ),
        "total_videos": int(
            statistics.get("videoCount", 0)
        )
    }


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
            live_details.get("concurrentViewers", 0)
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


@mcp.tool()
def get_youtube_live_data(
    channel_id: str = "",
    video_id: str = ""
) -> dict:
    try:
        channel_id = (
            channel_id.strip()
            or DEFAULT_CHANNEL_ID
            or ""
        )

        if not channel_id:
            return {
                "success": False,
                "error": "No YouTube channel ID provided."
            }

        channel = get_channel_info(channel_id)

        if video_id.strip():
            current_video_id = video_id.strip()
        else:
            current_video_id = find_live_video(
                channel_id
            )

        if not current_video_id:
            return {
                "success": True,
                "live": False,
                "message": "Channel is not currently live.",
                "channel": channel
            }

        live_video = get_live_video_info(
            current_video_id
        )

        return {
            "success": True,
            "live": True,
            "channel": channel,
            "live_stream": live_video
        }

    except requests.exceptions.RequestException as error:
        return {
            "success": False,
            "error": f"YouTube API request failed: {str(error)}"
        }

    except Exception as error:
        return {
            "success": False,
            "error": str(error)
        }


def analytics_get(params):
    access_token = get_access_token()

    response = requests.get(
        "https://youtubeanalytics.googleapis.com/v2/reports",
        params=params,
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        timeout=30
    )

    response.raise_for_status()

    return response.json()


@mcp.tool()
def get_youtube_analytics(
    start_date: str = "",
    end_date: str = "",
    channel_id: str = ""
) -> dict:
    try:
        if not start_date or not end_date:
            today = datetime.now(timezone.utc).date()
            end = today - timedelta(days=1)
            start = end - timedelta(days=1)

            start_date = start.isoformat()
            end_date = end.isoformat()

        channel_id = (
            channel_id.strip()
            or DEFAULT_CHANNEL_ID
            or ""
        )

        params = {
            "ids": "channel==MINE",
            "startDate": start_date,
            "endDate": end_date,
            "metrics": (
                "views,"
                "estimatedMinutesWatched,"
                "averageViewDuration,"
                "likes,"
                "comments,"
                "subscribersGained,"
                "subscribersLost"
            ),
            "dimensions": "day",
            "sort": "day"
        }

        data = analytics_get(params)

        return {
            "success": True,
            "channel_id": channel_id,
            "start_date": start_date,
            "end_date": end_date,
            "data": data
        }

    except requests.exceptions.HTTPError as error:
        details = ""

        if error.response is not None:
            details = error.response.text

        return {
            "success": False,
            "error": "YouTube Analytics API error.",
            "details": details
        }

    except Exception as error:
        return {
            "success": False,
            "error": str(error)
        }


@mcp.tool()
def get_youtube_daily_analytics(
    days: int = 7
) -> dict:
    try:
        days = max(1, min(days, 90))

        today = datetime.now(timezone.utc).date()

        end = today - timedelta(days=1)

        start = end - timedelta(
            days=days - 1
        )

        params = {
            "ids": "channel==MINE",
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "metrics": (
                "views,"
                "estimatedMinutesWatched,"
                "averageViewDuration,"
                "likes,"
                "comments,"
                "subscribersGained,"
                "subscribersLost"
            ),
            "dimensions": "day",
            "sort": "day"
        }

        data = analytics_get(params)

        return {
            "success": True,
            "days_requested": days,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "data": data
        }

    except requests.exceptions.HTTPError as error:
        details = ""

        if error.response is not None:
            details = error.response.text

        return {
            "success": False,
            "error": "YouTube Analytics API error.",
            "details": details
        }

    except Exception as error:
        return {
            "success": False,
            "error": str(error)
        }


@mcp.tool()
def get_youtube_video_analytics(
    video_id: str,
    start_date: str = "",
    end_date: str = ""
) -> dict:
    try:
        video_id = video_id.strip()

        if not video_id:
            return {
                "success": False,
                "error": "Video ID is required."
            }

        if not start_date or not end_date:
            today = datetime.now(timezone.utc).date()
            end = today - timedelta(days=1)
            start = end - timedelta(days=6)

            start_date = start.isoformat()
            end_date = end.isoformat()

        params = {
            "ids": "channel==MINE",
            "startDate": start_date,
            "endDate": end_date,
            "filters": f"video=={video_id}",
            "metrics": (
                "views,"
                "estimatedMinutesWatched,"
                "averageViewDuration,"
                "likes,"
                "comments,"
                "subscribersGained,"
                "subscribersLost"
            )
        }

        data = analytics_get(params)

        return {
            "success": True,
            "video_id": video_id,
            "start_date": start_date,
            "end_date": end_date,
            "data": data
        }

    except requests.exceptions.HTTPError as error:
        details = ""

        if error.response is not None:
            details = error.response.text

        return {
            "success": False,
            "error": "YouTube Analytics API error.",
            "details": details
        }

    except Exception as error:
        return {
            "success": False,
            "error": str(error)
        }


@mcp.tool()
def get_youtube_channel_overview(
    channel_id: str = ""
) -> dict:
    try:
        channel_id = (
            channel_id.strip()
            or DEFAULT_CHANNEL_ID
            or ""
        )

        if not channel_id:
            return {
                "success": False,
                "error": "No YouTube channel ID provided."
            }

        channel = get_channel_info(
            channel_id
        )

        live_video_id = find_live_video(
            channel_id
        )

        live_data = None

        if live_video_id:
            live_data = get_live_video_info(
                live_video_id
            )

        return {
            "success": True,
            "channel": channel,
            "live": bool(live_video_id),
            "live_stream": live_data,
            "oauth_authorized": bool(
                get_valid_token()
            )
        }

    except Exception as error:
        return {
            "success": False,
            "error": str(error)
        }


app = mcp.streamable_http_app()


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT
    )
