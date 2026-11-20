'use client';

import { useState, useEffect } from 'react';
import Icon from './Icon';
import { apiClient } from '@/lib/api';
import type { Job, Clip } from '@/types';
import { time } from '@/lib/utils';

interface BatchYouTubeModalProps {
  isOpen: boolean;
  onClose: () => void;
  job: Job | null;
  onOpenAuth: () => void;
}

interface UploadResult {
  clip_id: string;
  title: string;
  success: boolean;
  video_id?: string;
  video_url?: string;
  error?: string;
}

export default function BatchYouTubeModal({
  isOpen,
  onClose,
  job,
  onOpenAuth,
}: BatchYouTubeModalProps) {
  const [authenticated, setAuthenticated] = useState(false);
  const [channel, setChannel] = useState<{ title?: string; thumbnail?: string } | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(true);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [privacy, setPrivacy] = useState<'public' | 'unlisted' | 'private'>('public');

  const [uploading, setUploading] = useState(false);
  const [currentUploadIndex, setCurrentUploadIndex] = useState(0);
  const [results, setResults] = useState<UploadResult[]>([]);
  const [error, setError] = useState('');

  const renderedClips = (job?.clips || []).filter((c) => Boolean(c.video_file));

  useEffect(() => {
    if (!isOpen) return;
    setLoadingStatus(true);
    setError('');
    setResults([]);
    setCurrentUploadIndex(0);

    apiClient
      .youtubeAuthStatus()
      .then((res) => {
        setAuthenticated(Boolean(res.authenticated));
        setChannel(res.channel || null);
        setLoadingStatus(false);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to verify YouTube authentication');
        setLoadingStatus(false);
      });

    // Select all rendered clips by default
    const allIds = new Set(renderedClips.map((c) => c.clip_id));
    setSelectedIds(allIds);
  }, [isOpen, job]);

  const toggleSelect = (cid: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(cid)) next.delete(cid);
      else next.add(cid);
      return next;
    });
  };

  const selectAll = () => {
    setSelectedIds(new Set(renderedClips.map((c) => c.clip_id)));
  };

  const deselectAll = () => {
    setSelectedIds(new Set());
  };

  const handleStartBatchUpload = async () => {
    if (!job || selectedIds.size === 0) return;
    setUploading(true);
    setError('');
    setResults([]);

    const clipsToUpload = renderedClips.filter((c) => selectedIds.has(c.clip_id));
    const uploadResults: UploadResult[] = [];

    for (let i = 0; i < clipsToUpload.length; i++) {
      const clip = clipsToUpload[i];
      setCurrentUploadIndex(i + 1);

      try {
        const tags = Array.isArray(clip.hashtags)
          ? clip.hashtags.map((t) => t.replace('#', ''))
          : ['Shorts', 'Viral'];

        const res = await apiClient.uploadToYoutube(
          job.job_id,
          clip.clip_id,
          clip.title || 'Viral Moment #Shorts',
          clip.caption || '',
          tags,
          privacy
        );

        uploadResults.push({
          clip_id: clip.clip_id,
          title: clip.title || 'Clip',
          success: true,
          video_id: res.video_id,
          video_url: res.url || `https://youtube.com/shorts/${res.video_id}`,
        });
      } catch (err) {
        uploadResults.push({
          clip_id: clip.clip_id,
          title: clip.title || 'Clip',
          success: false,
          error: err instanceof Error ? err.message : 'Upload failed',
        });
      }

      setResults([...uploadResults]);
    }

    setUploading(false);
  };

  if (!isOpen || !job) return null;

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="batchYtTitle">
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '640px', width: '92vw' }}>
        {/* Header */}
        <div className="modal-heading">
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
              <span
                style={{
                  background: 'rgba(255, 78, 78, 0.15)',
                  color: '#ff4e4e',
                  border: '1px solid rgba(255, 78, 78, 0.3)',
                  padding: '2px 8px',
                  borderRadius: '12px',
                  fontSize: '11px',
                  fontWeight: 700,
                  letterSpacing: '0.04em',
                }}
              >
                AUTOMATED YOUTUBE PUBLISHER
              </span>
            </div>
            <h2 id="batchYtTitle" style={{ margin: 0, fontSize: '20px' }}>
              Publish Clips to YouTube Shorts
            </h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close" type="button">
            <Icon name="close" />
          </button>
        </div>

        <p className="modal-intro">
          Automatically post your rendered clips directly to YouTube Shorts with optimized titles, viral descriptions, and hashtags.
        </p>

        {loadingStatus ? (
          <p className="loading-text" style={{ padding: '24px 0', textAlign: 'center' }}>
            Checking YouTube account status…
          </p>
        ) : !authenticated ? (
          /* Not Connected Prompt */
          <div
            style={{
              background: '#131b24',
              border: '1px solid #283747',
              borderRadius: '12px',
              padding: '24px',
              textAlign: 'center',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '12px',
              margin: '16px 0',
            }}
          >
            <div
              style={{
                width: '54px',
                height: '54px',
                borderRadius: '50%',
                background: 'rgba(255, 78, 78, 0.15)',
                color: '#ff4e4e',
                display: 'grid',
                placeItems: 'center',
              }}
            >
              <Icon name="youtube" />
            </div>
            <strong style={{ fontSize: '16px' }}>YouTube Channel Not Linked</strong>
            <p style={{ margin: 0, fontSize: '13px', color: 'var(--muted)', maxWidth: '420px' }}>
              Please connect your Google account to authorize ClipCraft to publish videos directly to your YouTube channel.
            </p>
            <button
              className="button button-primary"
              type="button"
              onClick={() => {
                onClose();
                onOpenAuth();
              }}
              style={{ marginTop: '6px' }}
            >
              <Icon name="youtube" />
              Connect YouTube Channel
            </button>
          </div>
        ) : renderedClips.length === 0 ? (
          /* No Rendered Clips */
          <div
            style={{
              background: '#131b24',
              border: '1px solid #283747',
              borderRadius: '12px',
              padding: '24px',
              textAlign: 'center',
              margin: '16px 0',
            }}
          >
            <strong style={{ fontSize: '15px' }}>No Rendered Clips Available Yet</strong>
            <p style={{ margin: '6px 0 0', fontSize: '13px', color: 'var(--muted)' }}>
              Clips must be rendered into vertical 9:16 format with captions before uploading to YouTube Shorts.
              Click &ldquo;Render 9:16&rdquo; on any moment card first!
            </p>
          </div>
        ) : (
          /* Connected & Ready to Upload */
          <div style={{ marginTop: '16px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {error && <p className="inline-message error">{error}</p>}

            {/* Channel Bar */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                background: '#131b24',
                border: '1px solid #233140',
                borderRadius: '10px',
                padding: '10px 14px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                {channel?.thumbnail ? (
                  <img
                    src={channel.thumbnail}
                    alt=""
                    style={{ width: '32px', height: '32px', borderRadius: '50%', objectFit: 'cover' }}
                  />
                ) : (
                  <div
                    style={{
                      width: '32px',
                      height: '32px',
                      borderRadius: '50%',
                      background: 'rgba(255, 78, 78, 0.2)',
                      color: '#ff4e4e',
                      display: 'grid',
                      placeItems: 'center',
                    }}
                  >
                    <Icon name="youtube" />
                  </div>
                )}
                <div>
                  <div style={{ fontSize: '13px', fontWeight: 600, color: '#fff' }}>
                    {channel?.title || 'Connected YouTube Channel'}
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--lime, #c8ff64)' }}>✓ Authorized for Shorts Upload</div>
                </div>
              </div>

              {/* Privacy selector */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <label style={{ fontSize: '12px', color: 'var(--muted)' }}>Privacy:</label>
                <select
                  value={privacy}
                  onChange={(e) => setPrivacy(e.target.value as any)}
                  disabled={uploading}
                  style={{
                    background: '#0d131a',
                    border: '1px solid #2e3e50',
                    color: '#fff',
                    borderRadius: '6px',
                    padding: '4px 8px',
                    fontSize: '12px',
                  }}
                >
                  <option value="public">Public (Immediate Post)</option>
                  <option value="unlisted">Unlisted (Shareable link)</option>
                  <option value="private">Private (Only you)</option>
                </select>
              </div>
            </div>

            {/* Selection Toolbar */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '13px', fontWeight: 600 }}>
                Select Clips ({selectedIds.size} of {renderedClips.length} selected):
              </span>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="button"
                  className="button button-quiet button-mini"
                  onClick={selectAll}
                  disabled={uploading}
                >
                  Select All
                </button>
                <button
                  type="button"
                  className="button button-quiet button-mini"
                  onClick={deselectAll}
                  disabled={uploading}
                >
                  Clear
                </button>
              </div>
            </div>

            {/* Clips List */}
            <div
              style={{
                maxHeight: '220px',
                overflowY: 'auto',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px',
                background: '#0b1016',
                border: '1px solid #1c2734',
                borderRadius: '8px',
                padding: '8px',
              }}
            >
              {renderedClips.map((clip, idx) => {
                const isChecked = selectedIds.has(clip.clip_id);
                const res = results.find((r) => r.clip_id === clip.clip_id);
                const dur = clip.duration || clip.end_time - clip.start_time;

                return (
                  <div
                    key={clip.clip_id}
                    onClick={() => !uploading && toggleSelect(clip.clip_id)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '8px 12px',
                      background: isChecked ? 'rgba(200, 255, 100, 0.05)' : '#121820',
                      border: isChecked ? '1px solid rgba(200, 255, 100, 0.25)' : '1px solid #1f2a36',
                      borderRadius: '6px',
                      cursor: uploading ? 'default' : 'pointer',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => {}}
                        disabled={uploading}
                        style={{ cursor: 'pointer' }}
                      />
                      <span style={{ fontSize: '11px', color: 'var(--muted)', fontWeight: 600 }}>#{idx + 1}</span>
                      <span
                        style={{
                          fontSize: '13px',
                          color: '#e2ebf3',
                          fontWeight: 500,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          maxWidth: '340px',
                        }}
                      >
                        {clip.title || 'Viral Moment'}
                      </span>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{ fontSize: '11px', color: 'var(--muted)' }}>{time(dur)}</span>
                      {res ? (
                        res.success ? (
                          <a
                            href={res.video_url}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            style={{
                              color: 'var(--lime, #c8ff64)',
                              fontSize: '11px',
                              fontWeight: 700,
                              textDecoration: 'underline',
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '3px',
                            }}
                          >
                            ✓ Uploaded
                            <Icon name="external" className="mini-icon" />
                          </a>
                        ) : (
                          <span style={{ color: '#ff6666', fontSize: '11px' }}>✗ Failed</span>
                        )
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Upload Progress Status */}
            {uploading && (
              <div
                style={{
                  background: 'rgba(200, 255, 100, 0.1)',
                  border: '1px solid rgba(200, 255, 100, 0.3)',
                  borderRadius: '8px',
                  padding: '12px 16px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                }}
              >
                <span className="render-spinner"></span>
                <span style={{ fontSize: '13px', color: '#fff' }}>
                  Uploading clip <strong>{currentUploadIndex}</strong> of <strong>{selectedIds.size}</strong> to YouTube Shorts…
                </span>
              </div>
            )}

            {/* Results Summary */}
            {!uploading && results.length > 0 && (
              <div
                style={{
                  background: 'rgba(200, 255, 100, 0.12)',
                  border: '1px solid var(--lime, #c8ff64)',
                  borderRadius: '8px',
                  padding: '12px 16px',
                  fontSize: '13px',
                  color: '#f0f6fc',
                }}
              >
                🎉 <strong>Upload Complete!</strong> Successfully published{' '}
                {results.filter((r) => r.success).length} of {results.length} clips directly to YouTube Shorts!
              </div>
            )}
          </div>
        )}

        {/* Modal Actions */}
        <div className="modal-actions" style={{ marginTop: '20px' }}>
          <button className="button button-quiet" onClick={onClose} disabled={uploading} type="button">
            {results.length > 0 ? 'Close' : 'Cancel'}
          </button>
          {authenticated && renderedClips.length > 0 && (
            <button
              className="button button-primary"
              type="button"
              onClick={handleStartBatchUpload}
              disabled={uploading || selectedIds.size === 0}
            >
              <Icon name="youtube" />
              {uploading
                ? `Uploading (${currentUploadIndex}/${selectedIds.size})…`
                : `Publish ${selectedIds.size} Clip${selectedIds.size === 1 ? '' : 's'} to YouTube`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
