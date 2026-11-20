'use client';

import { useState, useEffect } from 'react';
import Icon from './Icon';
import { apiClient } from '@/lib/api';

interface YoutubeAuthModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function YoutubeAuthModal({ isOpen, onClose }: YoutubeAuthModalProps) {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [authenticating, setAuthenticating] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!isOpen) return;
    let active = true;
    apiClient.youtubeAuthStatus().then(result => {
      if (!active) return;
      setAuthenticated(Boolean(result.authenticated));
      setLoading(false);
      setError('');
    }).catch(err => {
      if (!active) return;
      setError(err instanceof Error ? err.message : 'Failed to check auth status');
      setLoading(false);
    });
    return () => { active = false; };
  }, [isOpen]);

  const handleAuthenticate = async () => {
    setAuthenticating(true);
    setError('');
    try {
      const result = await apiClient.youtubeAuthenticate();
      setAuthenticated(true);
      setAuthenticating(false);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to authenticate');
      setAuthenticating(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="ytAuthTitle">
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-heading">
          <div>
            <span className="step-label">INTEGRATIONS</span>
            <h2 id="ytAuthTitle">YouTube Connection</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close" type="button">
            <Icon name="close" />
          </button>
        </div>

        <p className="modal-intro">
          Connect your YouTube account to publish rendered vertical clips directly as YouTube Shorts.
        </p>

        {loading ? (
          <p className="loading-text">Checking authentication status…</p>
        ) : (
          <div style={{ marginTop: '16px' }}>
            {error && <p className="inline-message error">{error}</p>}

            {authenticated ? (
              <div className="auth-status-card success">
                <div className="auth-status-icon">
                  <Icon name="check" />
                </div>
                <div>
                  <strong>YouTube Connected</strong>
                  <p className="muted" style={{ fontSize: '12px', marginTop: '4px' }}>
                    Your channel is linked. You can publish clips directly to YouTube Shorts from the Social Copy dialog.
                  </p>
                </div>
              </div>
            ) : (
              <div className="auth-status-card">
                <div className="auth-status-icon">
                  <Icon name="youtube" />
                </div>
                <div>
                  <strong>Connect Your YouTube Channel</strong>
                  <p className="muted" style={{ fontSize: '12px', marginTop: '4px', marginBottom: '14px' }}>
                    Authorize ClipCraft with your Google account. OAuth credentials remain local on your machine.
                  </p>
                  <button
                    className="button button-primary button-small"
                    onClick={handleAuthenticate}
                    disabled={authenticating}
                    type="button"
                  >
                    <Icon name="youtube" />
                    {authenticating ? 'Connecting in browser…' : 'Authenticate YouTube'}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="modal-actions">
          <button className="button button-quiet" onClick={onClose} type="button">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
