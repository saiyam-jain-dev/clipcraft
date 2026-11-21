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
  const [configured, setConfigured] = useState(false);
  const [channel, setChannel] = useState<{
    id?: string;
    title?: string;
    description?: string;
    custom_url?: string;
    thumbnail?: string;
    subscriber_count?: string;
    video_count?: string;
  } | null>(null);

  const [loading, setLoading] = useState(true);
  const [authenticating, setAuthenticating] = useState(false);
  const [savingCreds, setSavingCreds] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  // Form states for manual configuration
  const [showCredForm, setShowCredForm] = useState(false);
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [rawJson, setRawJson] = useState('');

  // Manual auth code entry
  const [showCodeInput, setShowCodeInput] = useState(false);
  const [authCode, setAuthCode] = useState('');

  const checkStatus = async () => {
    try {
      setLoading(true);
      let res = await apiClient.youtubeAuthStatus();

      // If server does not have the token in disk (e.g. after container redeploy), restore from localStorage!
      if (!res.authenticated && typeof window !== 'undefined') {
        try {
          const savedToken = localStorage.getItem('clipcraft_youtube_token');
          if (savedToken) {
            const tokenObj = typeof savedToken === 'string' && savedToken.startsWith('{') ? JSON.parse(savedToken) : savedToken;
            const restored = await apiClient.youtubeRestoreToken(tokenObj);
            if (restored.authenticated) {
              res = await apiClient.youtubeAuthStatus();
            }
          }
        } catch (e) {}
      }

      const isAuthed = Boolean(res.authenticated);
      setAuthenticated(isAuthed);
      setConfigured(Boolean(res.configured));
      if (res.channel) {
        setChannel(res.channel);
      } else if (isAuthed) {
        setChannel({
          id: 'connected',
          title: 'YouTube Account Connected',
          description: 'Ready to publish YouTube Shorts',
        });
      }
      if (!res.configured) {
        setShowCredForm(true);
      }
      setLoading(false);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to check status');
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    checkStatus();

    // Listen for OAuth completion message from popup
    const handleMessage = async (event: MessageEvent) => {
      if (event.data?.type === 'YOUTUBE_AUTH_SUCCESS') {
        setAuthenticated(true);
        setAuthenticating(false);
        setSuccessMsg('YouTube channel connected successfully!');
        if (event.data?.channel) {
          setChannel(event.data.channel);
        }
        if (event.data?.token) {
          try {
            localStorage.setItem('clipcraft_youtube_token', typeof event.data.token === 'string' ? event.data.token : JSON.stringify(event.data.token));
          } catch (e) {}
        }
        await checkStatus();
      } else if (event.data?.type === 'YOUTUBE_AUTH_ERROR') {
        setAuthenticating(false);
        setError(event.data?.error || 'Authentication failed');
      }
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [isOpen]);

  useEffect(() => {
    const handleFocus = () => {
      if (authenticating) {
        setTimeout(() => {
          setAuthenticating(false);
          checkStatus();
        }, 1200);
      }
    };
    window.addEventListener('focus', handleFocus);
    const onVisibility = () => {
      if (document.visibilityState === 'visible') handleFocus();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [authenticating]);

  const handleStartOAuth = async () => {
    setAuthenticating(true);
    setError('');
    setSuccessMsg('');
    try {
      // Determine redirect URI: use current origin callback
      const redirectUri = `${window.location.origin}/api/youtube/oauth2callback`;
      const res = await apiClient.youtubeGetAuthUrl(redirectUri);
      if (res.auth_url) {
        // Open Google OAuth in a popup window
        const width = 540;
        const height = 660;
        const left = window.screenX + (window.outerWidth - width) / 2;
        const top = window.screenY + (window.outerHeight - height) / 2;
        const popup = window.open(
          res.auth_url,
          'google_oauth_popup',
          `width=${width},height=${height},left=${left},top=${top},status=0,toolbar=0,menubar=0`
        );
        if (!popup) {
          // If popup blocked, fallback to top-level navigation
          window.location.href = res.auth_url;
        } else {
          // Safety timeout: reset connecting state after 60s if popup was abandoned or unhandled
          const safetyTimeout = setTimeout(() => {
            setAuthenticating(false);
          }, 60000);

          const pollTimer = setInterval(() => {
            try {
              if (popup.closed) {
                clearInterval(pollTimer);
                clearTimeout(safetyTimeout);
                setAuthenticating(false);
                checkStatus();
              }
            } catch (e) {
              clearInterval(pollTimer);
              clearTimeout(safetyTimeout);
              setAuthenticating(false);
            }
          }, 800);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to launch Google authorization');
      setAuthenticating(false);
    }
  };

  const handleSaveCredentials = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingCreds(true);
    setError('');
    setSuccessMsg('');
    try {
      await apiClient.youtubeSaveCredentials(clientId, clientSecret, rawJson);
      setConfigured(true);
      setShowCredForm(false);
      setSuccessMsg('Google OAuth credentials saved! Click "Connect YouTube Channel" below.');
      await checkStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save credentials');
    } finally {
      setSavingCreds(false);
    }
  };

  const handleExchangeCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!authCode.trim()) return;
    setAuthenticating(true);
    setError('');
    setSuccessMsg('');
    try {
      const redirectUri = `${window.location.origin}/api/youtube/oauth2callback`;
      const res = await apiClient.youtubeExchangeCode(authCode.trim(), redirectUri);
      setAuthenticated(true);
      setSuccessMsg(res.message || 'YouTube connected successfully!');
      setShowCodeInput(false);
      await checkStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invalid authorization code');
    } finally {
      setAuthenticating(false);
    }
  };

  const handleDisconnect = async () => {
    if (!confirm('Are you sure you want to disconnect this YouTube channel?')) return;
    try {
      if (typeof window !== 'undefined') {
        localStorage.removeItem('clipcraft_youtube_token');
      }
      await apiClient.youtubeDisconnect();
      setAuthenticated(false);
      setChannel(null);
      setSuccessMsg('YouTube channel disconnected.');
      await checkStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to disconnect');
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="ytAuthTitle">
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '580px', width: '92vw' }}>
        <div className="modal-heading">
          <div>
            <span className="step-label">INTEGRATIONS</span>
            <h2 id="ytAuthTitle">YouTube Connection & Auto-Publish</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close" type="button">
            <Icon name="close" />
          </button>
        </div>

        <p className="modal-intro">
          Connect your YouTube account to publish generated 9:16 vertical clips directly as YouTube Shorts, individually or in bulk.
        </p>

        {loading ? (
          <p className="loading-text" style={{ padding: '24px 0', textAlign: 'center' }}>
            Checking connection status…
          </p>
        ) : (
          <div style={{ marginTop: '16px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
            {error && <p className="inline-message error">{error}</p>}
            {successMsg && <p className="inline-message info" style={{ color: 'var(--lime, #c8ff64)' }}>{successMsg}</p>}

            {/* 1. Connected State */}
            {authenticated ? (
              <div
                style={{
                  background: 'rgba(200, 255, 100, 0.08)',
                  border: '1px solid rgba(200, 255, 100, 0.3)',
                  borderRadius: '12px',
                  padding: '20px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '16px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
                  {channel?.thumbnail ? (
                    <img
                      src={channel.thumbnail}
                      alt={channel.title || 'Channel Avatar'}
                      style={{ width: '48px', height: '48px', borderRadius: '50%', objectFit: 'cover', border: '2px solid var(--lime)' }}
                    />
                  ) : (
                    <div
                      style={{
                        width: '48px',
                        height: '48px',
                        borderRadius: '50%',
                        background: 'rgba(255, 78, 78, 0.15)',
                        color: '#ff4e4e',
                        display: 'grid',
                        placeItems: 'center',
                      }}
                    >
                      <Icon name="youtube" />
                    </div>
                  )}

                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <strong style={{ fontSize: '16px', color: '#fff' }}>{channel?.title || 'YouTube Channel Connected'}</strong>
                      <span
                        style={{
                          background: 'rgba(200, 255, 100, 0.2)',
                          color: 'var(--lime, #c8ff64)',
                          fontSize: '10px',
                          fontWeight: 700,
                          padding: '2px 6px',
                          borderRadius: '8px',
                        }}
                      >
                        ACTIVE
                      </span>
                    </div>
                    {channel?.custom_url && (
                      <p style={{ margin: '2px 0 0', fontSize: '12px', color: 'var(--muted)' }}>{channel.custom_url}</p>
                    )}
                    {channel?.subscriber_count && (
                      <p style={{ margin: '2px 0 0', fontSize: '12px', color: 'var(--muted)' }}>
                        {Number(channel.subscriber_count).toLocaleString()} subscribers
                      </p>
                    )}
                  </div>
                </div>

                <p style={{ fontSize: '13px', color: '#c5d2dd', margin: 0 }}>
                  You can now upload rendered vertical clips directly to YouTube Shorts with 1 click, or batch upload all candidate clips together.
                </p>

                <div style={{ display: 'flex', gap: '10px', marginTop: '4px' }}>
                  <button
                    className="button button-small button-danger"
                    type="button"
                    onClick={handleDisconnect}
                  >
                    Disconnect Channel
                  </button>
                  <button
                    className="button button-small button-quiet"
                    type="button"
                    onClick={() => setShowCredForm(!showCredForm)}
                  >
                    {showCredForm ? 'Hide API Settings' : 'Change OAuth Keys'}
                  </button>
                </div>
              </div>
            ) : configured ? (
              /* 2. Configured but Not Signed In */
              <div
                style={{
                  background: '#141c24',
                  border: '1px solid #283747',
                  borderRadius: '12px',
                  padding: '20px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '14px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <div
                    style={{
                      width: '40px',
                      height: '40px',
                      borderRadius: '50%',
                      background: 'rgba(255, 78, 78, 0.15)',
                      color: '#ff4e4e',
                      display: 'grid',
                      placeItems: 'center',
                    }}
                  >
                    <Icon name="youtube" />
                  </div>
                  <div>
                    <strong style={{ fontSize: '15px' }}>Sign In with YouTube</strong>
                    <p style={{ margin: 0, fontSize: '12px', color: 'var(--muted)' }}>
                      OAuth credentials detected. Click below to authorize ClipCraft to publish Shorts.
                    </p>
                  </div>
                </div>

                <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginTop: '6px' }}>
                  <button
                    className="button button-primary"
                    type="button"
                    onClick={handleStartOAuth}
                    disabled={authenticating}
                    style={{ flex: 1, justifyContent: 'center' }}
                  >
                    <Icon name="youtube" />
                    {authenticating ? 'Connecting…' : 'Sign in with Google'}
                  </button>
                  {authenticating ? (
                    <button
                      className="button button-quiet button-small"
                      type="button"
                      onClick={() => setAuthenticating(false)}
                      title="Reset connecting state"
                    >
                      Cancel
                    </button>
                  ) : (
                    <button
                      className="button button-quiet button-small"
                      type="button"
                      onClick={() => setShowCodeInput(!showCodeInput)}
                      title="If popup does not redirect automatically"
                    >
                      Paste Code
                    </button>
                  )}
                </div>

                <div style={{ fontSize: '11px', color: '#8899a6', lineHeight: 1.4, background: '#0e1620', padding: '8px 10px', borderRadius: '6px' }}>
                  💡 <strong>Google Verification Note:</strong> If Google shows <em>&quot;Access blocked: app has not completed verification (Error 403: access_denied)&quot;</em>, add your Google account email under <strong>Test users</strong> in Google Cloud Console &rarr; APIs &amp; Services &rarr; OAuth consent screen.
                </div>

                {showCodeInput && (
                  <form onSubmit={handleExchangeCode} style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    <label style={{ fontSize: '12px', color: 'var(--muted)' }}>Paste Google Authorization Code:</label>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <input
                        type="text"
                        placeholder="4/0AX4XfWi..."
                        value={authCode}
                        onChange={(e) => setAuthCode(e.target.value)}
                        style={{ flex: 1, fontSize: '13px', padding: '8px 10px' }}
                      />
                      <button className="button button-small button-primary" type="submit" disabled={authenticating || !authCode.trim()}>
                        Connect
                      </button>
                    </div>
                  </form>
                )}

                <div style={{ borderTop: '1px solid #1f2a36', paddingTop: '10px' }}>
                  <button
                    className="button button-quiet button-small"
                    type="button"
                    onClick={() => setShowCredForm(!showCredForm)}
                    style={{ fontSize: '11px' }}
                  >
                    {showCredForm ? 'Hide Google Cloud OAuth Keys' : 'Edit Google Cloud OAuth Keys'}
                  </button>
                </div>
              </div>
            ) : null}

            {/* 3. Credential Setup Form (shown if not configured or toggled) */}
            {(!configured || showCredForm) && (
              <form
                onSubmit={handleSaveCredentials}
                style={{
                  background: '#121820',
                  border: '1px solid #23303e',
                  borderRadius: '12px',
                  padding: '18px 20px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '12px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <strong style={{ fontSize: '14px', color: 'var(--lime, #c8ff64)' }}>
                    Google Cloud OAuth 2.0 Credentials
                  </strong>
                  {configured && (
                    <button
                      type="button"
                      className="button button-quiet button-mini"
                      onClick={() => setShowCredForm(false)}
                    >
                      Close
                    </button>
                  )}
                </div>

                <p style={{ margin: 0, fontSize: '12px', color: 'var(--muted)' }}>
                  To enable automated YouTube uploads, create an OAuth 2.0 Client ID in your Google Cloud Console.
                </p>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <label className="field" style={{ margin: 0 }}>
                    <span style={{ fontSize: '12px', fontWeight: 600 }}>Client ID</span>
                    <input
                      type="text"
                      placeholder="xxxxxx.apps.googleusercontent.com"
                      value={clientId}
                      onChange={(e) => setClientId(e.target.value)}
                      required={!rawJson}
                      style={{ fontSize: '13px' }}
                    />
                  </label>

                  <label className="field" style={{ margin: 0 }}>
                    <span style={{ fontSize: '12px', fontWeight: 600 }}>Client Secret</span>
                    <input
                      type="password"
                      placeholder="GOCSPX-xxxxxx"
                      value={clientSecret}
                      onChange={(e) => setClientSecret(e.target.value)}
                      required={!rawJson}
                      style={{ fontSize: '13px' }}
                    />
                  </label>

                  <div style={{ fontSize: '11px', color: 'var(--muted)', textAlign: 'center', margin: '4px 0' }}>
                    — OR paste raw client_secrets.json —
                  </div>

                  <textarea
                    placeholder='{"web":{"client_id":"...","client_secret":"..."}}'
                    rows={3}
                    value={rawJson}
                    onChange={(e) => setRawJson(e.target.value)}
                    style={{ fontSize: '11px', fontFamily: 'monospace' }}
                  />
                </div>

                <button
                  className="button button-primary button-small"
                  type="submit"
                  disabled={savingCreds}
                  style={{ alignSelf: 'flex-start', marginTop: '4px' }}
                >
                  {savingCreds ? 'Saving…' : 'Save Credentials'}
                </button>

                {/* Step-by-step helper */}
                <details style={{ marginTop: '8px', fontSize: '12px', color: '#b0c0ce' }}>
                  <summary style={{ cursor: 'pointer', fontWeight: 600, color: 'var(--lime, #c8ff64)' }}>
                    How to get Google Cloud credentials in 2 minutes &rarr;
                  </summary>
                  <ol style={{ paddingLeft: '20px', lineHeight: '1.6', marginTop: '8px', fontSize: '12px' }}>
                    <li>
                      Go to{' '}
                      <a
                        href="https://console.cloud.google.com/apis/credentials"
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: 'var(--lime, #c8ff64)', textDecoration: 'underline' }}
                      >
                        Google Cloud Console &rarr; Credentials
                      </a>
                    </li>
                    <li>Create a Project and enable <strong>YouTube Data API v3</strong> under Enabled APIs.</li>
                    <li>Configure your OAuth Consent Screen (User Type: External, add your Gmail under Test Users).</li>
                    <li>Click <strong>Create Credentials &rarr; OAuth client ID</strong>.</li>
                    <li>Application type: <strong>Web application</strong>.</li>
                    <li>
                      Add Authorized Redirect URIs:
                      <div
                        style={{
                          background: '#0b0f14',
                          padding: '6px 10px',
                          borderRadius: '6px',
                          margin: '4px 0',
                          fontFamily: 'monospace',
                          fontSize: '11px',
                          color: '#fff',
                        }}
                      >
                        https://clipcraft-theta.vercel.app/api/youtube/oauth2callback
                        <br />
                        https://clipcraft-production-d672f3fd.up.railway.app/api/youtube/oauth2callback
                      </div>
                    </li>
                    <li>Copy your <strong>Client ID</strong> &amp; <strong>Client Secret</strong> and paste them above!</li>
                  </ol>
                </details>
              </form>
            )}
          </div>
        )}

        <div className="modal-actions" style={{ marginTop: '20px' }}>
          <button className="button button-quiet" onClick={onClose} type="button">
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
