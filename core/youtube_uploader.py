import os
import json
import google.oauth2.credentials
import google_auth_oauthlib.flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CLIENT_SECRETS_FILE = os.path.join(os.path.dirname(__file__), "youtube_client_secrets.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "youtube_token.json")


def get_youtube_service():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r") as f:
        token_data = json.load(f)
    credentials = google.oauth2.credentials.Credentials.from_authorized_user_info(token_data)
    return build("youtube", "v3", credentials=credentials)


def authenticate_youtube():
    if not os.path.exists(CLIENT_SECRETS_FILE):
        raise RuntimeError("youtube_client_secrets.json not found. Create OAuth credentials in Google Cloud Console.")
    flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    credentials = flow.run_local_server(port=8080)
    with open(TOKEN_FILE, "w") as f:
        f.write(credentials.to_json())
    return get_youtube_service()


def upload_to_youtube(video_path: str, title: str, description: str, tags: list = None, privacy: str = "private"):
    youtube = get_youtube_service()
    if not youtube:
        raise RuntimeError("YouTube not authenticated. Run authenticate_youtube() first.")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or [],
            "categoryId": "22"  # People & Blogs
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False
        }
    }

    insert_request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True)
    )

    response = None
    while response is None:
        status, response = insert_request.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%")

    return response
