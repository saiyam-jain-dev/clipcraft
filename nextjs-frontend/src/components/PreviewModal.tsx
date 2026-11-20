'use client';

import Icon from './Icon';
import { time, text, getYouTubeVideoId, getYouTubeTimestampUrl } from '@/lib/utils';
import type { Clip, Job } from '@/types';

interface PreviewModalProps {
  isOpen: boolean;
  onClose: () => void;
  clip: Clip | null;
  job: Job | null;
  onRenderClip?: (clipId: string) => void;
  isRendering?: boolean;
}

export default function PreviewModal({
  isOpen,
  onClose,
  clip,
  job,
  onRenderClip,
  isRendering = false,
}: PreviewModalProps) {
  if (!isOpen || !clip || !job) return null;

  const ytVideoId =
    getYouTubeVideoId(job.source || job.source_label || '') ||
    getYouTubeVideoId(job.video_thumbnail || '') ||
    (clip.thumb_url ? getYouTubeVideoId(clip.thumb_url) : null);
  const virality = Math.round(clip.virality_score ?? clip.score ?? 50);
  const duration = clip.duration || (clip.end_time - clip.start_time);
  const startSec = Math.floor(clip.start_time);
  const endSec = Math.ceil(clip.end_time);
  const videoUrl = clip.video_file ? `/video/${encodeURIComponent(clip.video_file)}` : '';

  return (
    <div
      className="modal-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="previewModalTitle"
    >
      <div
        className="modal modal-large"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '820px', width: '95vw', padding: '24px' }}
      >
        {/* Header */}
        <div className="modal-heading" style={{ marginBottom: '16px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
              <span
                style={{
                  background: 'rgba(200, 255, 100, 0.15)',
                  color: 'var(--lime, #c8ff64)',
                  border: '1px solid rgba(200, 255, 100, 0.3)',
                  padding: '2px 8px',
                  borderRadius: '12px',
                  fontSize: '11px',
                  fontWeight: 700,
                  letterSpacing: '0.04em',
                }}
              >
                LIVE PREVIEW · NO DOWNLOAD NEEDED
              </span>
              <span className="muted" style={{ fontSize: '12px' }}>
                {time(clip.start_time)} – {time(clip.end_time)} ({time(duration)})
              </span>
            </div>
            <h2 id="previewModalTitle" style={{ margin: 0, fontSize: '20px' }}>
              {text(clip.title, 'Moment Preview')}
            </h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Close preview"
            type="button"
          >
            <Icon name="close" />
          </button>
        </div>

        {/* Video Player */}
        <div
          className="preview-player-stage"
          style={{
            position: 'relative',
            width: '100%',
            aspectRatio: clip.video_file ? '9 / 16' : '16 / 9',
            maxHeight: clip.video_file ? '520px' : '440px',
            background: '#000',
            borderRadius: '10px',
            overflow: 'hidden',
            margin: '0 auto 18px',
            border: '1px solid #283442',
            boxShadow: '0 8px 30px rgba(0,0,0,0.6)',
          }}
        >
          {clip.video_file ? (
            <video
              src={videoUrl}
              controls
              autoPlay
              playsInline
              style={{ width: '100%', height: '100%', objectFit: 'contain' }}
            />
          ) : ytVideoId ? (
            <iframe
              src={`https://www.youtube-nocookie.com/embed/${ytVideoId}?start=${startSec}&end=${endSec}&autoplay=1&rel=0&modestbranding=1`}
              title={`Preview of ${clip.title}`}
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              allowFullScreen
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: '100%',
                border: 'none',
              }}
            />
          ) : (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
                color: 'var(--muted)',
                gap: '10px',
              }}
            >
              <Icon name="play" />
              <span>Preview not available for this source format</span>
            </div>
          )}
        </div>

        {/* Moment Details */}
        <div
          style={{
            background: '#0b0f14',
            border: '1px solid #1f2834',
            borderRadius: '10px',
            padding: '14px 18px',
            marginBottom: '20px',
          }}
        >
          {/* Virality & Hook */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '8px', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontSize: '12px', color: 'var(--muted)', fontWeight: 500 }}>Virality Score:</span>
              <strong style={{ fontSize: '15px', color: 'var(--lime, #c8ff64)' }}>{virality} / 99</strong>
            </div>
            {clip.hook && (
              <div style={{ fontSize: '13px', color: '#e2eaf0' }}>
                <span style={{ color: 'var(--muted)', fontSize: '11px', textTransform: 'uppercase', marginRight: '6px' }}>
                  Opens on:
                </span>
                &ldquo;{clip.hook}&rdquo;
              </div>
            )}
          </div>

          {/* Reason */}
          {clip.reason && (
            <p style={{ margin: 0, fontSize: '12px', color: 'var(--muted)', lineHeight: 1.5 }}>
              {clip.reason}
            </p>
          )}
        </div>

        {/* Modal Actions */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '12px',
            paddingTop: '16px',
            borderTop: '1px solid #1f2834',
          }}
        >
          <div>
            {ytVideoId && (
              <a
                href={getYouTubeTimestampUrl(job.source, clip.start_time)}
                target="_blank"
                rel="noopener noreferrer"
                className="button button-quiet button-small"
                style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '6px' }}
              >
                <Icon name="youtube" />
                Open on YouTube at {time(clip.start_time)}
                <Icon name="external" />
              </a>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            {clip.video_file ? (
              <a
                href={videoUrl}
                download={`${clip.title || 'clip'}.mp4`}
                className="button button-primary"
                style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '6px' }}
              >
                <Icon name="download" />
                Download 9:16 Video
              </a>
            ) : (
              onRenderClip && (
                <button
                  type="button"
                  className="button button-primary"
                  onClick={() => onRenderClip(clip.clip_id)}
                  disabled={isRendering}
                >
                  <Icon name="mark" />
                  {isRendering ? 'Rendering 9:16 Short…' : 'Render as Vertical Short'}
                </button>
              )
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
