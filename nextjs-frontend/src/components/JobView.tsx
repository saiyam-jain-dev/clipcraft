'use client';

import { useState, useEffect, useRef } from 'react';
import Icon from './Icon';
import SocialModal from './SocialModal';
import PreviewModal from './PreviewModal';
import BatchYouTubeModal from './BatchYouTubeModal';
import YoutubeAuthModal from './YoutubeAuthModal';
import { apiClient } from '@/lib/api';
import { text, number, time, date, safeUrl, statusNames, stages, stageTitles, activeStatuses, getYouTubeVideoId, getYouTubeTimestampUrl } from '@/lib/utils';
import type { Job, Clip, CropMode, CaptionStyle, JobStage } from '@/types';

interface JobViewProps {
  jobId: string;
  onBack: () => void;
  onNewProject: () => void;
}

export default function JobView({ jobId, onBack, onNewProject }: JobViewProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [clipSort, setClipSort] = useState<'score' | 'time'>('score');
  const [editingClip, setEditingClip] = useState<string | null>(null);
  const [socialModalClip, setSocialModalClip] = useState<Clip | null>(null);
  const [previewModalClip, setPreviewModalClip] = useState<Clip | null>(null);
  const [batchYtModalOpen, setBatchYtModalOpen] = useState(false);
  const [youtubeAuthOpen, setYoutubeAuthOpen] = useState(false);
  const [inlinePreviewClipId, setInlinePreviewClipId] = useState<string | null>(null);
  const [renderingClips, setRenderingClips] = useState<Record<string, boolean>>({});
  const [compiling, setCompiling] = useState(false);
  const [compilationUrl, setCompilationUrl] = useState<string | null>(null);

  const loadJob = async () => {
    try {
      const result = await apiClient.getJob(jobId);
      setJob(result.job);
      if (result.job.compilation_video_url) {
        setCompilationUrl(result.job.compilation_video_url);
      }
      setLoading(false);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load job');
      setLoading(false);
    }
  };

  const handleCreateCompilation = async () => {
    try {
      setCompiling(true);
      const res = await apiClient.compileJob(jobId);
      if (res.compilation_video_url) {
        setCompilationUrl(res.compilation_video_url);
        await loadJob();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to compile clips');
    } finally {
      setCompiling(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    let pollTimer: NodeJS.Timeout;

    const pollJob = async () => {
      try {
        const result = await apiClient.getJob(jobId);
        if (mounted) {
          setJob(result.job);
          if (result.job.compilation_video_url) {
            setCompilationUrl(result.job.compilation_video_url);
          }
          setLoading(false);
          setError('');

          // Keep polling if job is active OR if any clip is still in preview without a video file
          const hasPendingRenders = result.job.clips?.some(c => !c.video_file && c.preview);
          const isJobActive = activeStatuses.has(result.job.status);

          if (isJobActive || hasPendingRenders) {
            pollTimer = setTimeout(pollJob, 1800);
          }
        }
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : 'Failed to load job');
          setLoading(false);
        }
      }
    };

    pollJob();

    return () => {
      mounted = false;
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [jobId]);

  const handleRetry = async () => {
    if (!job) return;
    try {
      setLoading(true);
      const result = await apiClient.retryJob(jobId);
      if (result.job_id) {
        await loadJob();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to retry job');
      setLoading(false);
    }
  };

  const handleRenderClip = async (clipId: string) => {
    try {
      setRenderingClips(prev => ({ ...prev, [clipId]: true }));
      await apiClient.renderClip(jobId, clipId);
      await loadJob();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to render clip');
    } finally {
      setRenderingClips(prev => ({ ...prev, [clipId]: false }));
    }
  };

  const handleRenderAll = async () => {
    try {
      setLoading(true);
      await apiClient.renderAllClips(jobId);
      await loadJob();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to render all clips');
      setLoading(false);
    }
  };

  const handleRateClip = async (clipId: string, rating: number) => {
    try {
      await apiClient.rateClip(jobId, clipId, rating);
      setJob(prev => prev ? {
        ...prev,
        clips: prev.clips.map(c => c.clip_id === clipId ? { ...c, rating } : c),
      } : prev);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save rating');
    }
  };

  const handleReeditClip = async (clipId: string, changes: Record<string, unknown>) => {
    try {
      setRenderingClips(prev => ({ ...prev, [clipId]: true }));
      await apiClient.reeditClip(jobId, clipId, changes);
      setEditingClip(null);
      await loadJob();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to re-edit clip');
    } finally {
      setRenderingClips(prev => ({ ...prev, [clipId]: false }));
    }
  };

  const sortedClips = job?.clips
    ? [...job.clips].sort((a, b) => {
        if (clipSort === 'score') {
          const scoreA = a.virality_score ?? a.score ?? 0;
          const scoreB = b.virality_score ?? b.score ?? 0;
          return scoreB - scoreA;
        }
        return (a.start_time || 0) - (b.start_time || 0);
      })
    : [];

  const getScoreColorClass = (score: number) => {
    if (score >= 80) return 'score-high';
    if (score >= 65) return 'score-medium';
    return 'score-low';
  };

  const getLayoutLabel = (layout?: string) => {
    const layoutNames: Record<string, string> = {
      auto: 'Auto · Speaker Tracking',
      fill: 'Fill · Center Crop',
      fit: 'Fit · Full Frame Blur',
      split: 'Split · Two Speakers',
      gameplay: 'Gameplay · Face + Screen',
      pip_br: 'Facecam · Bottom Right',
      pip_bl: 'Facecam · Bottom Left',
      pip_tr: 'Facecam · Top Right',
      pip_tl: 'Facecam · Top Left',
      circle: 'Circle Facecam',
      side: 'Side by Side',
    };
    return (layout && layoutNames[layout]) || layout || '9:16 Vertical';
  };

  if (loading && !job) {
    return (
      <section className="job-view" aria-labelledby="jobTitle">
        <div className="job-navigation">
          <button className="text-button" type="button" onClick={onBack}>
            <Icon name="back" />
            All projects
          </button>
        </div>
        <div className="job-heading">
          <div>
            <div className="eyebrow">YOUR CLIPPING STUDIO</div>
            <h1 id="jobTitle">Connecting to project…</h1>
          </div>
        </div>
        <div className="processing-panel panel">
          <div className="processing-header">
            <div>
              <h2>Fetching project status</h2>
              <p className="muted">Please wait while we load your clipping session…</p>
            </div>
            <span className="render-spinner"></span>
          </div>
        </div>
      </section>
    );
  }

  if (error && !job) {
    return (
      <section className="job-view" aria-labelledby="jobTitle">
        <div className="job-navigation">
          <button className="text-button" type="button" onClick={onBack}>
            <Icon name="back" />
            All projects
          </button>
        </div>
        <div className="job-heading">
          <div>
            <div className="eyebrow">YOUR CLIPPING STUDIO</div>
            <h1 id="jobTitle">Project Error</h1>
          </div>
        </div>
        <div className="processing-panel panel">
          <p className="inline-message error">{error}</p>
          <div style={{ marginTop: '16px' }}>
            <button className="button button-small" type="button" onClick={handleRetry}>
              <Icon name="refresh" />
              Retry Project
            </button>
          </div>
        </div>
      </section>
    );
  }

  if (!job) return null;

  const currentStageIndex = stages.indexOf(job.stage as JobStage);
  const progressPct = number(job.progress_pct, 0);
  const isFailed = job.status === 'failed';
  const isBusy = activeStatuses.has(job.status);
  const pendingClipsCount = sortedClips.filter(c => !c.video_file).length;

  return (
    <section className="job-view" aria-labelledby="jobTitle">
      {/* Top navigation */}
      <div className="job-navigation">
        <button className="text-button" type="button" onClick={onBack}>
          <Icon name="back" />
          All projects
        </button>
        <button className="button button-small" type="button" onClick={onNewProject}>
          <Icon name="plus" />
          New project
        </button>
      </div>

      {/* Main heading */}
      <div className="job-heading">
        <div>
          <div className="eyebrow">YOUR CLIPPING SESSION</div>
          <h1 id="jobTitle" tabIndex={-1}>
            {text(job.video_title || job.title, 'Untitled project')}
          </h1>
          <p className="muted">{text(job.source_label, job.source)}</p>
        </div>
        <span className={`status-badge ${job.status}`}>{statusNames[job.status] || job.status}</span>
      </div>

      {/* Progress & Status Panel */}
      <div className={`processing-panel panel ${isFailed ? 'failed' : ''}`}>
        {job.video_thumbnail && (
          <div className="job-source-card">
            <img src={safeUrl(job.video_thumbnail)} alt="" referrerPolicy="no-referrer" />
            <div>
              <span className="step-label">SOURCE FOOTAGE</span>
              <strong>{text(job.video_title || job.title, 'Untitled project')}</strong>
              <span className="muted">
                {[
                  job.video_uploader,
                  job.video_duration ? time(job.video_duration) : '',
                  job.video_resolution,
                  date(job.created_at)
                ].filter(Boolean).join(' · ')}
              </span>
            </div>
          </div>
        )}

        <div className="processing-header">
          <div>
            <h2>
              {stageTitles[job.stage] || 'Processing'}
              {isBusy && <span className="render-spinner" style={{ marginLeft: '10px' }}></span>}
            </h2>
            <p className="muted" role="status" aria-live="polite">
              {text(job.status_message || (isBusy ? 'Analyzing video moments…' : 'Ready'))}
            </p>
          </div>
          <span className="progress-label">{Math.round(progressPct)}%</span>
        </div>

        {/* Glowing animated progress track */}
        <div
          className="progress-track"
          role="progressbar"
          aria-label="Clip creation progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progressPct}
        >
          <span style={{ width: `${Math.min(100, Math.max(2, progressPct))}%` }}></span>
        </div>

        {/* Stage list */}
        <ol className="stage-list">
          {stages.map((stage, index) => {
            const isDone = index < currentStageIndex || job.status === 'completed';
            const isActive = index === currentStageIndex && job.status !== 'completed';
            return (
              <li key={stage} data-stage={stage} className={`${isActive ? 'active' : ''} ${isDone ? 'done' : ''}`}>
                <span>{isDone ? '✓' : index + 1}</span>
                {stageTitles[stage]}
              </li>
            );
          })}
        </ol>

        {job.warning && (
          <p className="inline-message warning" style={{ marginTop: '14px' }}>
            {job.warning}
          </p>
        )}

        {isFailed && (
          <div className="job-recovery" style={{ marginTop: '16px' }}>
            <p className="inline-message error" style={{ margin: 0 }}>
              {job.error || job.status_message || 'Processing failed.'}
            </p>
            <button className="button button-small" type="button" onClick={handleRetry}>
              <Icon name="refresh" />
              Retry with same options
            </button>
          </div>
        )}

        {/* Collapsible Activity Log */}
        {Array.isArray(job.log) && job.log.length > 0 && (
          <details className="activity-log">
            <summary>View Activity Log ({job.log.length} events)</summary>
            <div className="log-lines">
              {job.log.map((entry, idx) => (
                <div key={idx} className={`log-line ${entry.level || 'info'}`}>
                  <span className="log-time">[{time(entry.t - (job.created_at || entry.t))}] </span>
                  {entry.msg}
                </div>
              ))}
            </div>
          </details>
        )}
      </div>

      {/* Results Section */}
      <div className="section-heading results-heading">
        <div>
          <h2>
            Generated Clips <span className="count-label">{sortedClips.length}</span>
          </h2>
          <p>Opus-style AI-selected moments ranked by Virality Score with hooks, active speaker reframing, and captions.</p>
        </div>

        <div className="results-actions">
          {pendingClipsCount > 0 && (
            <button
              className="button button-small button-primary"
              type="button"
              onClick={handleRenderAll}
              disabled={isBusy}
            >
              <Icon name="play" />
              Render all ({pendingClipsCount} pending)
            </button>
          )}

          {/* Batch YouTube Upload */}
          {sortedClips.filter((c) => c.video_file).length > 0 && (
            <button
              className="button button-small"
              type="button"
              onClick={() => setBatchYtModalOpen(true)}
              style={{
                background: 'rgba(255, 78, 78, 0.12)',
                borderColor: 'rgba(255, 78, 78, 0.35)',
                color: '#ff6666',
                fontWeight: 600,
              }}
              title="Post rendered clips directly to YouTube Shorts"
            >
              <Icon name="youtube" />
              Publish to YouTube ({sortedClips.filter((c) => c.video_file).length})
            </button>
          )}

          <label className="sort-control" htmlFor="clipSort">
            Sort by
            <select
              id="clipSort"
              value={clipSort}
              onChange={(e) => setClipSort(e.target.value as 'score' | 'time')}
            >
              <option value="score">Highest Virality Score</option>
              <option value="time">Timeline Order</option>
            </select>
          </label>
        </div>
      </div>

      {/* All-In-One Compilation Card */}
      {sortedClips.filter((c) => c.video_file).length > 1 && (
        <div
          className="panel"
          style={{
            marginBottom: '28px',
            background: 'linear-gradient(135deg, #131b24 0%, #171d2b 100%)',
            border: '1px solid #2e415a',
            borderRadius: '12px',
            padding: '20px 24px',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: '16px',
            }}
          >
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                <span
                  style={{
                    background: '#22c55e22',
                    color: '#86efac',
                    border: '1px solid #22c55e44',
                    padding: '2px 8px',
                    borderRadius: '12px',
                    fontSize: '11px',
                    fontWeight: 600,
                  }}
                >
                  ALL-IN-ONE VIDEO
                </span>
                <span className="muted" style={{ fontSize: '13px' }}>
                  {sortedClips.filter((c) => c.video_file).length} viral moments combined into one full reel
                </span>
              </div>
              <h3 style={{ margin: 0, fontSize: '18px', color: '#f0f0f0' }}>
                Combined Full Video (All Clips in One)
              </h3>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <button
                type="button"
                className="button button-quiet"
                onClick={handleCreateCompilation}
                disabled={compiling}
              >
                {compiling ? 'Stitching…' : compilationUrl ? 'Re-stitch Video' : 'Generate Full Video'}
              </button>
              {compilationUrl && (
                <a
                  href={compilationUrl}
                  download={`${job.video_title || 'compilation'}_full.mp4`}
                  className="button button-primary"
                  style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}
                >
                  <Icon name="download" />
                  Download Full Video
                </a>
              )}
            </div>
          </div>

          {compilationUrl && (
            <div
              style={{
                marginTop: '16px',
                borderRadius: '8px',
                overflow: 'hidden',
                maxWidth: '320px',
                margin: '16px auto 0',
              }}
            >
              <video
                src={compilationUrl}
                controls
                playsInline
                preload="metadata"
                style={{ width: '100%', maxHeight: '420px', borderRadius: '8px', background: '#000' }}
              />
            </div>
          )}
        </div>
      )}

      {/* Clips Grid */}
      {(() => {
        const jobYtVideoId =
          getYouTubeVideoId(job?.source || job?.source_label || '') ||
          getYouTubeVideoId(job?.video_thumbnail || '');

        return (
          <div className="clips-grid">
            {sortedClips.map((clip) => {
              const virality = Math.round(clip.virality_score ?? clip.score ?? 50);
              const scoreClass = getScoreColorClass(virality);
              const duration = clip.duration || (clip.end_time - clip.start_time);
              const isPending = !clip.video_file;
              const isRendering = Boolean(renderingClips[clip.clip_id]);
              const posterUrl = safeUrl(clip.thumb_url || '');
              const videoUrl = clip.video_file ? `/video/${encodeURIComponent(clip.video_file)}` : '';
              const subtitleUrl = clip.subtitle_url || (clip.video_file ? `/download/${clip.video_file.replace('.mp4', '.srt')}` : '');
              const clipYtVideoId = jobYtVideoId || (clip.thumb_url ? getYouTubeVideoId(clip.thumb_url) : null);
              const isInlinePlaying = inlinePreviewClipId === clip.clip_id;

              return (
                <article key={clip.clip_id} className={`clip-card ${scoreClass}`}>
                  {/* 9:16 Vertical Video Container */}
                  <div className="clip-video-wrap">
                    {clip.video_file ? (
                      <video
                        className="clip-video"
                        controls
                        playsInline
                        preload="metadata"
                        poster={posterUrl}
                        src={videoUrl}
                        aria-label={`Preview of ${clip.title}`}
                      />
                    ) : isInlinePlaying && clipYtVideoId ? (
                      <div className="clip-inline-player">
                        <iframe
                          className="clip-inline-iframe"
                          src={`https://www.youtube-nocookie.com/embed/${clipYtVideoId}?start=${Math.floor(clip.start_time)}&end=${Math.ceil(clip.end_time)}&autoplay=1&rel=0&modestbranding=1`}
                          title={`Preview of ${clip.title}`}
                          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                          allowFullScreen
                        />
                        <button
                          type="button"
                          className="clip-inline-close-btn"
                          onClick={(e) => {
                            e.stopPropagation();
                            setInlinePreviewClipId(null);
                          }}
                          title="Close inline preview"
                        >
                          <Icon name="close" /> Stop Preview
                        </button>
                      </div>
                    ) : (
                      <>
                        {posterUrl ? (
                          <img className="clip-preview" src={posterUrl} alt="" />
                        ) : (
                          <div className="clip-preview-placeholder">
                            <Icon name="play" />
                          </div>
                        )}
                        <div className="clip-pending-overlay">
                          {isRendering ? (
                            <div className="pending-badge">
                              <span className="render-spinner"></span>
                              Rendering 9:16 clip…
                            </div>
                          ) : (
                            <div className="clip-pending-controls">
                              <button
                                className="clip-play-hero-btn"
                                type="button"
                                onClick={() => setPreviewModalClip(clip)}
                                title="Play instant preview with timestamps (no download needed)"
                              >
                                <span className="play-icon-glow">
                                  <Icon name="play" />
                                </span>
                                <span className="play-title">Play Preview</span>
                                <span className="play-timing">
                                  {time(clip.start_time)} – {time(clip.end_time)}
                                </span>
                              </button>

                              <div className="clip-pending-subactions">
                                {clipYtVideoId && (
                                  <button
                                    className="button button-quiet button-mini"
                                    type="button"
                                    onClick={() => setInlinePreviewClipId(clip.clip_id)}
                                    title="Play inline inside this card"
                                  >
                                    Inline
                                  </button>
                                )}
                                <button
                                  className="button button-primary button-mini"
                                  type="button"
                                  onClick={() => handleRenderClip(clip.clip_id)}
                                  disabled={isBusy}
                                  title="Render as 9:16 vertical short with captions"
                                >
                                  Render 9:16
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      </>
                    )}
                    <span className="clip-layout">{getLayoutLabel(clip.layout)}</span>
                  </div>

                  {/* Clip Body */}
                  <div className="clip-body">
                    {/* Score Row */}
                    <div className="clip-score-row">
                      <span className="score-badge">
                        <strong className="clip-score">{virality}</strong>
                        <span>/ 99</span>
                      </span>
                      <span className="score-description">Virality Score</span>
                      <span className="clip-duration">{time(duration)}</span>

                      {/* Feedback rating */}
                      <div className="clip-rating" role="radiogroup" aria-label="Rate this moment">
                        {[1, 2, 3, 4, 5].map((star) => (
                          <button
                            key={star}
                            type="button"
                            className={`star-btn ${(clip.rating || 0) >= star ? 'filled' : ''}`}
                            onClick={() => handleRateClip(clip.clip_id, star)}
                            aria-label={`Rate ${star} star`}
                            title={`Rate ${star} star to train learned weights`}
                          >
                            ★
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Title */}
                    <h3 className="clip-title">{text(clip.title, 'Viral Moment')}</h3>

                    {/* Hook Callout */}
                    {clip.hook && (
                      <div className="clip-hook-box">
                        <p className="clip-hook-label">OPENS ON</p>
                        <p className="clip-hook">&ldquo;{clip.hook}&rdquo;</p>
                      </div>
                    )}

                    {/* Timestamps */}
                    <div className="clip-source-time-row">
                      <span className="clip-source-time">
                        Source {time(clip.start_time)} – {time(clip.end_time)}
                      </span>
                      {clipYtVideoId && (
                        <a
                          href={getYouTubeTimestampUrl(job?.source || job?.source_label, clip.start_time)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="clip-youtube-link"
                          title="Open at this exact timestamp on YouTube"
                        >
                          <Icon name="youtube" />
                          <span>{time(clip.start_time)}</span>
                          <Icon name="external" className="mini-icon" />
                        </a>
                      )}
                    </div>

                    {/* Why this moment? (Opus Virality Breakdown) */}
                    <details className="clip-details" open>
                      <summary>Why this moment?</summary>
                      {clip.reason && <p className="clip-reason">{clip.reason}</p>}

                      {/* 4-signal breakdown bars */}
                      <div className="score-breakdown">
                        {(['hook', 'flow', 'value', 'trend'] as const).map((dim) => {
                          const val = Math.round(number(clip.scores?.[dim], virality));
                          return (
                            <div key={dim} className="score-item" data-score={dim}>
                              <span>{dim.charAt(0).toUpperCase() + dim.slice(1)}</span>
                              <strong>{val}</strong>
                              <div className="score-bar" aria-hidden="true">
                                <span style={{ width: `${Math.max(0, Math.min(100, val))}%` }}></span>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                      <p className="score-disclaimer">
                        Calculated from speech hook impact, completeness of thought, engagement value, and audio/motion peaks.
                      </p>
                    </details>

                    {/* Transcript & Caption Accordion */}
                    <details className="clip-details">
                      <summary>Transcript & caption</summary>
                      <p className="clip-transcript">{text(clip.transcript, 'No transcript available.')}</p>
                      {clip.caption && (
                        <div style={{ marginTop: '8px' }}>
                          <strong style={{ fontSize: '11px', color: '#c0cdce' }}>Suggested caption:</strong>
                          <p className="clip-caption">{clip.caption}</p>
                        </div>
                      )}
                      {Array.isArray(clip.hashtags) && clip.hashtags.length > 0 && (
                        <div className="clip-hashtags-wrap">
                          {clip.hashtags.map((tag, i) => (
                            <span key={i} className="hashtag-pill">{tag}</span>
                          ))}
                        </div>
                      )}
                    </details>

                    {/* Action Buttons Toolbar */}
                    <div className="clip-actions">
                      {clip.video_file ? (
                        <>
                          <a
                            className="button button-primary button-small"
                            href={`/download/${encodeURIComponent(clip.video_file)}`}
                            download
                          >
                            <Icon name="download" />
                            Download MP4
                          </a>
                          <button
                            className="button button-small button-quiet"
                            type="button"
                            onClick={() => setSocialModalClip(clip)}
                            title="Publish this clip directly as a YouTube Short"
                            style={{ color: '#ff6666' }}
                          >
                            <Icon name="youtube" />
                            Shorts
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            className="button button-small button-preview"
                            type="button"
                            onClick={() => setPreviewModalClip(clip)}
                            title="Instant preview without downloading"
                          >
                            <Icon name="play" />
                            Preview
                          </button>
                          <button
                            className="button button-primary button-small"
                            type="button"
                            onClick={() => handleRenderClip(clip.clip_id)}
                            disabled={isBusy || isRendering}
                          >
                            <Icon name="play" />
                            {isRendering ? 'Rendering…' : 'Render 9:16'}
                          </button>
                        </>
                      )}

                      <button
                        className="button button-small"
                        type="button"
                        onClick={() => setSocialModalClip(clip)}
                      >
                        <Icon name="copy" />
                        Social copy
                      </button>

                      {subtitleUrl && (
                        <a
                          className="button button-small button-quiet"
                          href={subtitleUrl}
                          download
                          title="Download Subtitles (.srt)"
                        >
                          <Icon name="download" />
                          SRT
                        </a>
                      )}

                      <button
                        className="button button-small button-quiet"
                        type="button"
                        onClick={() => setEditingClip(editingClip === clip.clip_id ? null : clip.clip_id)}
                        title="Customize layout, caption style, or trim times"
                      >
                        <Icon name="settings" />
                        {editingClip === clip.clip_id ? 'Close' : 'Re-edit'}
                      </button>
                    </div>

                    {/* Re-edit / Editor Section */}
                    {editingClip === clip.clip_id && (
                      <ClipEditor
                        clip={clip}
                        job={job}
                        onApply={(changes) => handleReeditClip(clip.clip_id, changes)}
                        isBusy={isBusy || isRendering}
                      />
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        );
      })()}

      {sortedClips.length === 0 && !isBusy && (
        <div className="empty-state">
          <span className="empty-symbol">
            <Icon name="mark" />
          </span>
          <h3>No moments found yet</h3>
          <p>
            {isFailed
              ? 'Check the error message above and click Retry.'
              : 'Clips will appear here as soon as speech and hooks are analyzed.'}
          </p>
        </div>
      )}

      {/* Instant Stream Preview Modal */}
      <PreviewModal
        isOpen={Boolean(previewModalClip)}
        onClose={() => setPreviewModalClip(null)}
        clip={previewModalClip}
        job={job}
        onRenderClip={handleRenderClip}
        isRendering={Boolean(previewModalClip && renderingClips[previewModalClip.clip_id])}
      />

      {/* Social Copy Modal */}
      <SocialModal
        isOpen={Boolean(socialModalClip)}
        onClose={() => setSocialModalClip(null)}
        jobId={jobId}
        clip={socialModalClip}
      />

      {/* Batch YouTube Shorts Publisher */}
      <BatchYouTubeModal
        isOpen={batchYtModalOpen}
        onClose={() => setBatchYtModalOpen(false)}
        job={job}
        onOpenAuth={() => setYoutubeAuthOpen(true)}
      />

      {/* YouTube Connection & OAuth Modal */}
      <YoutubeAuthModal
        isOpen={youtubeAuthOpen}
        onClose={() => setYoutubeAuthOpen(false)}
      />
    </section>
  );
}

const LAYOUT_OPTIONS: { value: CropMode; label: string }[] = [
  { value: 'fit', label: 'Fit · Full Video + Blurred Top/Bottom (Reels/Shorts)' },
  { value: 'fill', label: 'Fill · Center Crop (9:16)' },
  { value: 'auto', label: 'Auto · Fit with Blurred Background' },
  { value: 'split', label: 'Split · Two Speakers' },
  { value: 'gameplay', label: 'Gameplay · Face + Screen' },
  { value: 'side', label: 'Side by Side' },
];

const STYLE_OPTIONS: { value: CaptionStyle; label: string }[] = [
  { value: 'bold', label: 'Bold (High Contrast)' },
  { value: 'clean', label: 'Clean (Modern Minimal)' },
  { value: 'minimal', label: 'Minimal (Subtle Text)' },
  { value: 'pop', label: 'Pop · Hormozi Karaoke' },
];

function ClipEditor({
  clip,
  job,
  onApply,
  isBusy,
}: {
  clip: Clip;
  job: Job;
  onApply: (changes: Record<string, unknown>) => void;
  isBusy: boolean;
}) {
  const [layout, setLayout] = useState<CropMode>((clip.layout as CropMode) || (job.options?.crop_mode as CropMode) || 'fit');
  const [enableSubs, setEnableSubs] = useState<boolean>(job.options?.enable_subtitles ?? false);
  const [style, setStyle] = useState<CaptionStyle>((job.options?.caption_style as CaptionStyle) || 'bold');
  const [color, setColor] = useState<string>(job.options?.caption_color || '#c8ff64');
  const [startShift, setStartShift] = useState(0);
  const [endShift, setEndShift] = useState(0);

  const duration = Math.max(0.1, (clip.end_time + endShift) - (clip.start_time + startShift));

  return (
    <div className="clip-editor">
      <div className="clip-editor-header">
        <strong>Customize Clip & Trim</strong>
        <span className="muted">
          New duration: {time(duration)} ({startShift !== 0 || endShift !== 0 ? `shift ± sec` : 'unchanged'})
        </span>
      </div>

      <div className="clip-editor-grid">
        <label className="field">
          Layout
          <select value={layout} onChange={(e) => setLayout(e.target.value as CropMode)}>
            {LAYOUT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>

        <label className="field" style={{ display: 'flex', flexDirection: 'row', alignItems: 'center', gap: '8px', cursor: 'pointer', marginTop: 'auto', marginBottom: '8px' }}>
          <input
            type="checkbox"
            checked={enableSubs}
            onChange={(e) => setEnableSubs(e.target.checked)}
            style={{ width: '18px', height: '18px' }}
          />
          <span>Burn in captions</span>
        </label>

        {enableSubs && (
          <>
            <label className="field">
              Caption Style
              <select value={style} onChange={(e) => setStyle(e.target.value as CaptionStyle)}>
                {STYLE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              Caption Color
              <input
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
                style={{ height: '38px', padding: '2px', cursor: 'pointer' }}
              />
            </label>
          </>
        )}

        <div className="trim-shift-group">
          <label className="field">
            Start Shift <span className="field-unit">± sec</span>
            <input
              type="number"
              min={-30}
              max={30}
              step={0.5}
              value={startShift}
              onChange={(e) => setStartShift(Number(e.target.value) || 0)}
            />
          </label>
          <label className="field">
            End Shift <span className="field-unit">± sec</span>
            <input
              type="number"
              min={-30}
              max={30}
              step={0.5}
              value={endShift}
              onChange={(e) => setEndShift(Number(e.target.value) || 0)}
            />
          </label>
        </div>
      </div>

      <div className="clip-editor-actions">
        <button
          className="button button-small button-primary"
          type="button"
          disabled={isBusy}
          onClick={() =>
            onApply({
              crop_mode: layout,
              enable_subtitles: enableSubs,
              caption_style: style || 'bold',
              caption_color: color || '#c8ff64',
              ...(startShift ? { start_shift: startShift } : {}),
              ...(endShift ? { end_shift: endShift } : {}),
            })
          }
        >
          <Icon name="refresh" />
          {isBusy ? 'Re-rendering…' : 'Re-render with changes'}
        </button>
      </div>
    </div>
  );
}
