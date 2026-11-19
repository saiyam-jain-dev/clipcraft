from .config import load_settings


def generate_social_post_package(clip: dict, brand_name: str = "") -> dict:
    title = clip.get("title", "Highlight")
    tags = " ".join(clip.get("hashtags", ["#Shorts"]))
    caption = clip.get("caption") or clip.get("hook") or title
    brand = f"\n\nFrom {brand_name}" if brand_name else ""
    return {
        "youtube": {"title": f"{title[:90]} #Shorts"[:100],
                    "description": f"{caption}{brand}\n\n{tags}"},
        "instagram": {"caption": f"{caption}{brand}\n\n{tags} #reels"},
    }


def get_publisher_settings() -> dict:
    return load_settings()
