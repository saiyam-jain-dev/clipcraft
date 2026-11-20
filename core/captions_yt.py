"""
YouTube word-timed captions parser.
Based on NeonClipping's captions_yt.py - extracts YouTube's native word-timed captions (json3 format).
This is much faster than running Whisper for YouTube videos.
"""

import json
import re
from typing import List, Dict, Tuple
import yt_dlp
from .downloader import _ydl_opts


def get_youtube_captions(video_url: str) -> List[Dict]:
    """
    Fetches word-timed captions from YouTube if available.
    Returns list of segments with start, end, and text.
    
    Each segment: {
        'start': float,
        'end': float,
        'text': str,
        'words': [{'word': str, 'start': float, 'end': float}, ...]
    }
    """
    try:
        opts = _ydl_opts(
            skip_download=True,
            writesubtitles=True,
            writeautomaticsub=True,
            subtitleslangs=['en', 'en-US', 'en-GB'],
            subtitlesformat='json3',
        )
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
        if not info or 'subtitles' not in info:
            return []
            
        # Try manual captions first, then automatic
        subtitles = info.get('subtitles', {})
        automatic = info.get('automatic_captions', {})
        
        all_captions = {**automatic, **subtitles}
        
        for lang in ['en', 'en-US', 'en-GB']:
            if lang in all_captions:
                for sub in all_captions[lang]:
                    if sub.get('ext') == 'json3':
                        # Download the caption file
                        caption_opts = _ydl_opts(
                            skip_download=True,
                            writesubtitles=True,
                            subtitleslangs=[lang],
                            subtitlesformat='json3',
                            outtmpl='%(id)s.%(ext)s',
                        )
                        with yt_dlp.YoutubeDL(caption_opts) as ydl:
                            ydl.download([video_url])
                        
                        # Read and parse the downloaded file
                        vid = info.get('id', 'video')
                        caption_file = f"{vid}.en.json3"
                        try:
                            with open(caption_file, 'r', encoding='utf-8') as f:
                                caption_data = json.load(f)
                            
                            # Parse json3 format
                            segments = []
                            for event in caption_data.get('events', []):
                                if event.get('segs'):
                                    text = ''.join(seg.get('utf8', '') for seg in event['segs'])
                                    if text.strip():
                                        # Extract word timings if available
                                        words = []
                                        for seg in event['segs']:
                                            if seg.get('utf8', '').strip():
                                                words.append({
                                                    'word': seg['utf8'],
                                                    'start': event.get('tStart', 0) / 1000,
                                                    'end': (event.get('tStart', 0) + seg.get('tOffset', 0)) / 1000,
                                                })
                                        
                                        segments.append({
                                            'start': event.get('tStart', 0) / 1000,
                                            'end': (event.get('tStart', 0) + event.get('dDuration', 0)) / 1000,
                                            'text': text,
                                            'words': words if words else None,
                                        })
                            
                            # Clean up
                            import os
                            if os.path.exists(caption_file):
                                os.remove(caption_file)
                            
                            return segments
                        except Exception as e:
                            print(f"Error parsing caption file: {e}")
                            continue
                            
    except Exception as e:
        print(f"Error fetching YouTube captions: {e}")
    
    return []


def sentences_from_captions(captions: List[Dict], min_duration: float = 2.0) -> List[Dict]:
    """
    Groups caption segments into sentences based on punctuation.
    Ensures each sentence meets minimum duration.
    """
    if not captions:
        return []
    
    sentences = []
    current_sentence = {
        'start': captions[0]['start'],
        'text': '',
        'words': [],
    }
    
    for caption in captions:
        text = caption['text'].strip()
        if not text:
            continue
        
        # Add to current sentence
        current_sentence['text'] += ' ' + text
        if caption.get('words'):
            current_sentence['words'].extend(caption['words'])
        current_sentence['end'] = caption['end']
        
        # Check for sentence boundaries
        if text.endswith(('.', '!', '?', '...')) or \
           re.search(r'[.!?]\s*["\']?$', text):
            # End of sentence
            duration = current_sentence['end'] - current_sentence['start']
            if duration >= min_duration:
                current_sentence['text'] = current_sentence['text'].strip()
                if current_sentence['words']:
                    sentences.append(current_sentence.copy())
                # Start new sentence
                if captions.index(caption) < len(captions) - 1:
                    next_caption = captions[captions.index(caption) + 1]
                    current_sentence = {
                        'start': next_caption['start'],
                        'text': '',
                        'words': [],
                    }
    
    # Add final sentence if it meets duration
    if current_sentence['text'].strip():
        duration = current_sentence['end'] - current_sentence['start']
        if duration >= min_duration:
            current_sentence['text'] = current_sentence['text'].strip()
            if current_sentence['words']:
                sentences.append(current_sentence)
    
    return sentences
