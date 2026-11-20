'use client';

import { useState, useEffect } from 'react';
import Icon from './Icon';
import { apiClient } from '@/lib/api';
import type { SocialPackage, Clip } from '@/types';

interface SocialModalProps {
  isOpen: boolean;
  onClose: () => void;
  jobId: string;
  clip: Clip | null;
}

export default function SocialModal({ isOpen, onClose, jobId, clip }: SocialModalProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [ytTitle, setYtTitle] = useState('');
  const [ytDesc, setYtDesc] = useState('');
  const [igCaption, setIgCaption] = useState('');
  const [activeTab, setActiveTab] = useState<'youtube' | 'instagram'>('youtube');
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  // YouTube direct upload state
  const [ytAuthenticated, setYtAuthenticated] = useState(false);
  const [privacy, setPrivacy] = useState<'private' | 'unlisted' | 'public'>('private');
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState('');

  useEffect(() => {
    if (!isOpen || !clip) return;
    let active = true;
    apiClient.socialPackage(jobId, clip.clip_id).then(result => {
      if (!active) return;
      setError('');
      if (result.package) {
        setYtTitle(result.package.youtube?.title || `${clip.title || 'Clip'} #Shorts`);
        setYtDesc(result.package.youtube?.description || clip.caption || '');
        setIgCaption(result.package.instagram?.caption || clip.caption || '');
      }
    }).catch(() => {
      if (!active) return;
      setYtTitle(`${clip.title || 'Highlight'} #Shorts`);
      const tags = Array.isArray(clip.hashtags) ? clip.hashtags.join(' ') : (clip.hashtags || '#Shorts');
      setYtDesc(`${clip.caption || clip.hook || clip.title || ''}\n\n${tags}`);
      setIgCaption(`${clip.caption || clip.hook || clip.title || ''}\n\n${tags} #reels`);
    }).finally(() => {
      if (active) setLoading(false);
    });
    apiClient.youtubeAuthStatus().then(result => {
      if (active) setYtAuthenticated(Boolean(result.authenticated));
    }).catch(() => {
      if (active) setYtAuthenticated(false);
    });
    return () => { active = false; };
  }, [isOpen, clip, jobId]);

  const copyToClipboard = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => {
      setCopiedKey(null);
    }, 2000);
  };

  const handleUploadYoutube = async () => {
    if (!clip || !clip.video_file) return;
    setUploading(true);
    setUploadMessage('Uploading clip to YouTube Shorts…');
    try {
      const tags = Array.isArray(clip.hashtags)
        ? clip.hashtags.map(t => t.replace('#', ''))
        : ['Shorts', 'Viral'];
      const result = await apiClient.uploadToYoutube(
        jobId,
        clip.clip_id,
        ytTitle,
        ytDesc,
        tags,
        privacy
      );
      setUploadMessage(result.video_id ? `Uploaded successfully! Video ID: ${result.video_id}` : 'Upload complete!');
    } catch (err) {
      setUploadMessage(`Upload failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setUploading(false);
    }
  };

  if (!isOpen || !clip) return null;

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="socialTitle">
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-heading">
          <div>
            <span className="step-label">SOCIAL POST PACKAGE</span>
            <h2 id="socialTitle">Ready-to-post copy</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close social copy dialog" type="button">
            <Icon name="close" />
          </button>
        </div>

        <p className="modal-intro">
          Copy-paste ready titles, captions, and hashtags formatted for YouTube Shorts, Instagram Reels, and TikTok.
        </p>

        {loading ? (
          <p className="loading-text">Generating social package…</p>
        ) : (
          <div className="social-modal-body">
            {error && <p className="inline-message error">{error}</p>}

            <div className="source-tabs social-tabs" role="tablist">
              <button
                className={`source-tab ${activeTab === 'youtube' ? 'active' : ''}`}
                onClick={() => setActiveTab('youtube')}
                type="button"
                role="tab"
                aria-selected={activeTab === 'youtube'}
              >
                <Icon name="youtube" />
                YouTube Shorts
              </button>
              <button
                className={`source-tab ${activeTab === 'instagram' ? 'active' : ''}`}
                onClick={() => setActiveTab('instagram')}
                type="button"
                role="tab"
                aria-selected={activeTab === 'instagram'}
              >
                <Icon name="mark" />
                Reels & TikTok
              </button>
            </div>

            {activeTab === 'youtube' && (
              <div className="social-tab-pane">
                <div className="field">
                  <div className="field-header-row">
                    <label htmlFor="ytTitle">Shorts Title</label>
                    <button
                      className="button button-small button-quiet"
                      type="button"
                      onClick={() => copyToClipboard(ytTitle, 'ytTitle')}
                    >
                      <Icon name="copy" />
                      {copiedKey === 'ytTitle' ? 'Copied!' : 'Copy title'}
                    </button>
                  </div>
                  <input
                    id="ytTitle"
                    type="text"
                    value={ytTitle}
                    onChange={(e) => setYtTitle(e.target.value)}
                    maxLength={100}
                  />
                </div>

                <div className="field" style={{ marginTop: '14px' }}>
                  <div className="field-header-row">
                    <label htmlFor="ytDesc">Description & Tags</label>
                    <button
                      className="button button-small button-quiet"
                      type="button"
                      onClick={() => copyToClipboard(ytDesc, 'ytDesc')}
                    >
                      <Icon name="copy" />
                      {copiedKey === 'ytDesc' ? 'Copied!' : 'Copy description'}
                    </button>
                  </div>
                  <textarea
                    id="ytDesc"
                    rows={4}
                    value={ytDesc}
                    onChange={(e) => setYtDesc(e.target.value)}
                  />
                </div>

                <div className="social-quick-actions">
                  <button
                    className="button button-primary button-small"
                    type="button"
                    onClick={() => copyToClipboard(`${ytTitle}\n\n${ytDesc}`, 'ytAll')}
                  >
                    <Icon name="copy" />
                    {copiedKey === 'ytAll' ? 'Copied all!' : 'Copy title & description'}
                  </button>

                  {ytAuthenticated && clip.video_file && (
                    <div className="yt-direct-upload">
                      <select
                        value={privacy}
                        onChange={(e) => setPrivacy(e.target.value as 'private' | 'unlisted' | 'public')}
                        className="privacy-select"
                      >
                        <option value="private">Private</option>
                        <option value="unlisted">Unlisted</option>
                        <option value="public">Public</option>
                      </select>
                      <button
                        className="button button-small"
                        type="button"
                        onClick={handleUploadYoutube}
                        disabled={uploading}
                      >
                        <Icon name="upload" />
                        {uploading ? 'Uploading…' : 'Publish to YouTube'}
                      </button>
                    </div>
                  )}
                </div>

                {uploadMessage && (
                  <p className="inline-message info" style={{ marginTop: '10px' }}>
                    {uploadMessage}
                  </p>
                )}
              </div>
            )}

            {activeTab === 'instagram' && (
              <div className="social-tab-pane">
                <div className="field">
                  <div className="field-header-row">
                    <label htmlFor="igCaption">Reels / TikTok Caption & Hashtags</label>
                    <button
                      className="button button-small button-quiet"
                      type="button"
                      onClick={() => copyToClipboard(igCaption, 'igCaption')}
                    >
                      <Icon name="copy" />
                      {copiedKey === 'igCaption' ? 'Copied!' : 'Copy caption'}
                    </button>
                  </div>
                  <textarea
                    id="igCaption"
                    rows={6}
                    value={igCaption}
                    onChange={(e) => setIgCaption(e.target.value)}
                  />
                </div>

                <div className="social-quick-actions">
                  <button
                    className="button button-primary button-small"
                    type="button"
                    onClick={() => copyToClipboard(igCaption, 'igAll')}
                  >
                    <Icon name="copy" />
                    {copiedKey === 'igAll' ? 'Copied!' : 'Copy entire caption'}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="modal-actions">
          <button className="button button-quiet" onClick={onClose} type="button">
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
