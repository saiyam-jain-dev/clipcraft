'use client';

import { useState, useRef, FormEvent } from 'react';
import Icon from './Icon';
import { apiClient, ApiError } from '@/lib/api';
import { validSourceUrl } from '@/lib/utils';
import type { VideoInfo, JobOptions } from '@/types';

interface HomeViewProps {
  onJobCreated: (jobId: string) => void;
}

export default function HomeView({ onJobCreated }: HomeViewProps) {
  const [sourceMode, setSourceMode] = useState<'url' | 'file'>('url');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [blobUrl, setBlobUrl] = useState('');
  const [sourceInfo, setSourceInfo] = useState<VideoInfo | null>(null);
  const [sourceMessage, setSourceMessage] = useState<{ text: string; type: 'error' | 'warning' | 'success' } | null>(null);
  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [checkingSource, setCheckingSource] = useState(false);
  const [enableSubtitles, setEnableSubtitles] = useState(true);
  const [enableBranding, setEnableBranding] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const videoUrlRef = useRef<HTMLInputElement>(null);
  const uploadPreviewRef = useRef<HTMLVideoElement>(null);

  const handleFileSelect = (file: File) => {
    if (!file.size) {
      setSourceMessage({ text: 'This file is empty. Choose a video with content.', type: 'error' });
      return;
    }
    if (file.size > 2 * 1024 ** 3) {
      setSourceMessage({ text: 'Choose a video smaller than 2 GB.', type: 'error' });
      if (fileInputRef.current) fileInputRef.current.value = '';
      return;
    }
    if (!/\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(file.name)) {
      setSourceMessage({ text: 'Choose a video file, such as MP4, MOV or WebM.', type: 'error' });
      if (fileInputRef.current) fileInputRef.current.value = '';
      return;
    }

    if (blobUrl) URL.revokeObjectURL(blobUrl);
    setSelectedFile(file);
    const newBlobUrl = URL.createObjectURL(file);
    setBlobUrl(newBlobUrl);
    setSourceMessage(null);
    setFormError('');
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFileSelect(file);
  };

  const handleRemoveFile = () => {
    if (uploadPreviewRef.current) {
      uploadPreviewRef.current.pause();
      uploadPreviewRef.current.removeAttribute('src');
      uploadPreviewRef.current.load();
    }
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    setBlobUrl('');
    setSelectedFile(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
    setSourceMessage(null);
  };

  const handleCheckSource = async () => {
    const url = videoUrlRef.current?.value.trim();
    if (!validSourceUrl(url || '')) {
      setSourceMessage({ text: 'Enter a complete public HTTP or HTTPS video URL.', type: 'error' });
      return;
    }

    setCheckingSource(true);
    setSourceMessage({ text: 'Checking the source. You can create clips without waiting for this preview.', type: 'success' });

    try {
      const result = await apiClient.videoInfo(url || '');
      if (result.info) {
        setSourceInfo(result.info);
        setSourceMessage({ text: result.info.is_live ? 'This appears to be a live source. A completed, downloadable video works best.' : 'Source found. Choose your direction, then create clips.', type: result.info.is_live ? 'warning' : 'success' });
      }
    } catch (error) {
      setSourceMessage({ text: `Preview unavailable: ${error instanceof Error ? error.message : 'Unknown error'}. You can still try creating clips.`, type: 'warning' });
    } finally {
      setCheckingSource(false);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;

    const videoUrl = videoUrlRef.current?.value.trim() || '';
    if (sourceMode === 'url' && !validSourceUrl(videoUrl)) {
      setFormError('Enter a public HTTP or HTTPS video URL. Local paths are not supported.');
      return;
    }
    if (sourceMode === 'file' && !selectedFile) {
      setFormError('Choose or drop a video file first.');
      return;
    }

    const formData = new FormData(e.currentTarget as HTMLFormElement);
    const options: JobOptions = {
      mode: (formData.get('processing_mode') as JobOptions['mode']) || 'fast',
      crop_mode: (formData.get('crop_mode') as JobOptions['crop_mode']) || 'fit',
      focus: (formData.get('focus') as JobOptions['focus']) || 'auto',
      target_clips: Number(formData.get('target_clips')) || 5,
      min_duration: Number(formData.get('min_duration')) || 25,
      max_duration: Number(formData.get('max_duration')) || 60,
      range_start: Number(formData.get('range_start')) || 0,
      range_end: Number(formData.get('range_end')) || 0,
      enable_subtitles: enableSubtitles,
      enable_branding: enableBranding,
      brand_name: formData.get('brand_name') as string || '',
      caption_style: (formData.get('caption_style') as JobOptions['caption_style']) || 'bold',
      caption_color: formData.get('caption_color') as string || '#c8ff64',
      prompt: formData.get('prompt') as string || '',
      language: formData.get('language') as string || '',
    };

    if (options.max_duration < options.min_duration) {
      setFormError('Maximum length must be at least the minimum length.');
      return;
    }
    if (options.range_end !== 0 && options.range_end <= options.range_start) {
      setFormError('End must be later than start, or 0 for the full video.');
      return;
    }
    if (enableBranding && !options.brand_name.trim()) {
      setFormError('Enter a brand name or turn branding off.');
      return;
    }

    setSubmitting(true);
    setFormError('');

    try {
      const result = sourceMode === 'file'
        ? await apiClient.createClip(options, selectedFile || undefined)
        : await apiClient.createClip({ ...options, url: videoUrl });

      if (result.job_id) {
        onJobCreated(result.job_id);
      }
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'Failed to create clips. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const hasFile = sourceMode === 'file' && selectedFile;
  const hasInfo = sourceMode === 'url' && sourceInfo;

  return (
    <section id="homeView" aria-labelledby="heroTitle">
      <div className="hero">
        <div className="eyebrow">
          <span className="tiny-line"></span>
          LONG VIDEO. MORE POSSIBILITIES.
        </div>
        <h1 id="heroTitle">
          Turn any footage into<br />
          <span>a week of posts.</span>
        </h1>
        <p>
          Your best moments, a shorter format. Find the story, add captions,<br className="desktop-break" /> and make
          vertical clips — right from your own machine.
        </p>
        <div className="hero-footnote">
          <span className="status-dot"></span>
          No ClipCraft subscription. Your footage stays in your local workspace.
        </div>
      </div>

      <form id="clipForm" className="studio-workbench" onSubmit={handleSubmit}>
        <section className="source-panel panel" aria-labelledby="sourceTitle">
          <div className="panel-heading">
            <div>
              <span className="step-label">01 / THE FOOTAGE</span>
              <h2 id="sourceTitle">Start with something good.</h2>
            </div>
            <span className="format-label">16:9 → 9:16</span>
          </div>

          <div className="source-tabs" role="tablist" aria-label="Video source">
            <button
              id="urlTab"
              className={`source-tab ${sourceMode === 'url' ? 'active' : ''}`}
              type="button"
              role="tab"
              aria-selected={sourceMode === 'url'}
              aria-controls="urlPanel"
              onClick={() => setSourceMode('url')}
            >
              <Icon name="link" />
              Paste a link
            </button>
            <button
              id="fileTab"
              className={`source-tab ${sourceMode === 'file' ? 'active' : ''}`}
              type="button"
              role="tab"
              aria-selected={sourceMode === 'file'}
              aria-controls="filePanel"
              onClick={() => setSourceMode('file')}
            >
              <Icon name="upload" />
              Upload a video
            </button>
          </div>

          {sourceMode === 'url' && (
            <div id="urlPanel" role="tabpanel" aria-labelledby="urlTab">
              <label className="field-label" htmlFor="videoUrl">
                Video URL
              </label>
              <div className="url-input-wrap">
                <Icon name="link" />
                <input
                  id="videoUrl"
                  name="url"
                  type="url"
                  ref={videoUrlRef}
                  placeholder="Paste a YouTube or public video link"
                  autoComplete="off"
                  spellCheck={false}
                  aria-describedby="urlHelp"
                  required
                />
                <button
                  id="checkSource"
                  className="button button-small"
                  type="button"
                  onClick={handleCheckSource}
                  disabled={checkingSource}
                >
                  {checkingSource ? 'Checking…' : 'Check source'}
                  <Icon name="arrow" />
                </button>
              </div>
              <div className="source-help">
                <span id="urlHelp">Public HTTP / HTTPS links. No local paths.</span>
                <button
                  id="tryExample"
                  className="text-button"
                  type="button"
                  onClick={() => {
                    if (videoUrlRef.current) {
                      videoUrlRef.current.value = 'https://www.youtube.com/watch?v=arj7oStGLkU';
                    }
                  }}
                >
                  Try a TED talk <span aria-hidden="true">↗</span>
                </button>
              </div>
            </div>
          )}

          {sourceMode === 'file' && (
            <div id="filePanel" role="tabpanel" aria-labelledby="fileTab">
              <label className="dropzone" htmlFor="videoFile">
                <Icon name="upload" className="upload-icon" />
                <strong>Drop your video here</strong>
                <span>or choose a file from your computer</span>
                <span className="file-types">MP4, MOV, MKV, WebM, AVI or M4V · up to 2 GB</span>
                <input
                  id="videoFile"
                  name="video_file"
                  type="file"
                  ref={fileInputRef}
                  accept="video/*,.mkv,.mov,.mp4,.webm,.avi,.m4v"
                  aria-label="Choose a video file"
                  onChange={handleFileChange}
                />
              </label>
              {selectedFile && (
                <div className="file-details">
                  <div>
                    <strong>{selectedFile.name}</strong>
                    <span className="muted">{(selectedFile.size / 1048576).toFixed(1)} MB · Preview before clipping</span>
                  </div>
                  <button className="icon-button" type="button" onClick={handleRemoveFile} aria-label="Remove selected video">
                    <Icon name="close" />
                  </button>
                </div>
              )}
            </div>
          )}

          {sourceMessage && (
            <p className={`inline-message ${sourceMessage.type}`} role="status">
              {sourceMessage.text}
            </p>
          )}

          {hasFile && (
            <div className="source-preview">
              <video
                ref={uploadPreviewRef}
                controls
                preload="metadata"
                playsInline
                src={blobUrl}
                aria-label="Uploaded source preview"
              />
              <div className="preview-meta">
                <span className="step-label">YOUR SOURCE</span>
                <h3>{selectedFile.name}</h3>
                <p className="muted">{(selectedFile.size / 1048576).toFixed(1)} MB</p>
              </div>
            </div>
          )}

          {hasInfo && (
            <div className="source-preview">
              {sourceInfo.thumbnail && <img src={sourceInfo.thumbnail} alt="Source video thumbnail" referrerPolicy="no-referrer" />}
              <div className="preview-meta">
                <span className="step-label">YOUR SOURCE</span>
                <h3>{sourceInfo.title}</h3>
                <p className="muted">
                  {[sourceInfo.uploader, sourceInfo.duration_str, sourceInfo.is_live ? 'Live source' : '']
                    .filter(Boolean)
                    .join(' · ')}
                </p>
              </div>
            </div>
          )}

          {!hasFile && !hasInfo && (
            <div className="source-empty" aria-hidden="true">
              <div className="frame-stack">
                <div className="frame-back"></div>
                <div className="frame-front">
                  <Icon name="play" />
                  <div className="frame-bars">
                    <span></span>
                    <span></span>
                    <span></span>
                    <span></span>
                    <span></span>
                    <span></span>
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                  <span className="frame-caption">MAKE THE CUT.</span>
                </div>
              </div>
              <div>
                <strong>A long story. A fresh perspective.</strong>
                <p>Your source preview will appear here.</p>
              </div>
            </div>
          )}

          <div className="source-footer">
            <Icon name="mark" />
            <span>Use footage you own or have permission to repurpose.</span>
          </div>
        </section>

        <section className="configure-panel panel" aria-labelledby="configureTitle">
          <div className="panel-heading">
            <div>
              <span className="step-label">02 / YOUR DIRECTION</span>
              <h2 id="configureTitle">Make it your own.</h2>
            </div>
            <Icon name="settings" className="muted" />
          </div>

          <div className="form-grid">
            <label className="field">
              Processing Mode
              <select name="processing_mode" defaultValue="fast">
                <option value="fast">Fast (recommended)</option>
                <option value="deep">Deep (higher quality)</option>
              </select>
            </label>
            <label className="field">
              What to find
              <select name="focus" defaultValue="auto">
                <option value="auto">Reactions, jokes, and stories</option>
                <option value="story">Complete stories</option>
                <option value="highlights">Loud and visual peaks</option>
              </select>
            </label>
            <label className="field">
              Framing
              <select name="crop_mode" defaultValue="fit">
                <option value="fit">Fit · Full original video + blurred top/bottom (Reels / Shorts)</option>
                <option value="auto">Auto · Fit with blurred background</option>
                <option value="fill">Fill · Center crop (9:16)</option>
                <option value="gameplay">Gameplay · Screen + face</option>
                <option value="split">Split · Two speakers</option>
                <option value="side">Side by side</option>
              </select>
            </label>
            <label className="field">
              Number of clips
              <input name="target_clips" type="number" min="1" max="24" step="1" defaultValue={5} required />
            </label>
            <label className="field">
              Min length <span className="field-unit">seconds</span>
              <input name="min_duration" type="number" min="8" max="180" defaultValue={15} required />
            </label>
            <label className="field">
              Max length <span className="field-unit">seconds</span>
              <input name="max_duration" type="number" min="10" max="180" defaultValue={60} required />
            </label>
          </div>

          <p className="field-hint">
            Auto chooses a layout, not universal visual tracking. Review each crop before posting.
            Five clips is a fast starting batch. Videos with native captions skip Whisper; analysis and rendering time still depend on source and hardware.
          </p>

          <label className="toggle-row" htmlFor="enableSubtitles">
            <span>
              <strong>Burn in captions</strong>
              <small>Readable, even with the sound off</small>
            </span>
            <input
              id="enableSubtitles"
              name="enable_subtitles"
              type="checkbox"
              checked={enableSubtitles}
              onChange={(e) => setEnableSubtitles(e.target.checked)}
            />
            <span className="switch" aria-hidden="true"></span>
          </label>

          {enableSubtitles && (
            <div className="caption-controls">
              <label className="field">
                Caption style
                <select name="caption_style" defaultValue="bold">
                  <option value="bold">Bold</option>
                  <option value="clean">Clean</option>
                  <option value="minimal">Minimal</option>
                  <option value="pop">Pop · Hormozi style</option>
                </select>
              </label>
              <label className="field color-field">
                Caption color
                <input name="caption_color" type="color" defaultValue="#c8ff64" />
              </label>
            </div>
          )}

          <details className="advanced-options">
            <summary>
              Fine-tune your clips <span aria-hidden="true">+</span>
            </summary>
            <div className="advanced-body">
              <div className="form-grid">
                <label className="field">
                  Start at <span className="field-unit">seconds</span>
                  <input name="range_start" type="number" min="0" step="0.1" defaultValue={0} required />
                </label>
                <label className="field">
                  End at <span className="field-unit">0 = full video</span>
                  <input name="range_end" type="number" min="0" step="0.1" defaultValue={0} required />
                </label>
              </div>
              <label className="field">
                Content direction <span className="field-unit">optional</span>
                <textarea
                  name="prompt"
                  rows={2}
                  maxLength={2000}
                  placeholder="Find practical advice, surprising insights, or a strong opening…"
                />
              </label>
              <label className="field">
                Spoken language
                <select name="language" defaultValue="">
                  <option value="">Auto-detect</option>
                  <option value="en">English</option>
                  <option value="es">Spanish</option>
                  <option value="fr">French</option>
                  <option value="de">German</option>
                  <option value="pt">Portuguese</option>
                  <option value="it">Italian</option>
                  <option value="nl">Dutch</option>
                  <option value="hi">Hindi</option>
                  <option value="hinglish">Hinglish · Hindi in English letters</option>
                  <option value="ja">Japanese</option>
                  <option value="ko">Korean</option>
                  <option value="zh">Chinese</option>
                  <option value="ar">Arabic</option>
                  <option value="ru">Russian</option>
                </select>
              </label>
              <label className="toggle-row" htmlFor="enableBranding">
                <span>
                  <strong>Add your brand name</strong>
                  <small>A small text watermark</small>
                </span>
                <input
                  id="enableBranding"
                  name="enable_branding"
                  type="checkbox"
                  checked={enableBranding}
                  onChange={(e) => setEnableBranding(e.target.checked)}
                />
                <span className="switch" aria-hidden="true"></span>
              </label>
              {enableBranding && (
                <label className="field">
                  Brand name
                  <input name="brand_name" maxLength={80} placeholder="Your name or handle" />
                </label>
              )}
            </div>
          </details>

          {formError && <p className="inline-message error" role="alert">{formError}</p>}

          <button className="button button-primary generate-button" type="submit" disabled={submitting}>
            <span>{submitting ? (sourceMode === 'file' ? 'Uploading video…' : 'Starting your project…') : 'Create clips'}</span>
            <Icon name="arrow" />
          </button>

          <p className="generation-note">
            Runs on this computer. Without an AI key in Settings, clips are chosen by local hook rules, which are weaker
            than Gemini or Groq.
          </p>
        </section>
      </form>

      <div className="studio-note">
        <span>Connecting to your local engine…</span>
        <span>Speech-led selection · Captions · Vertical output</span>
      </div>
    </section>
  );
}
