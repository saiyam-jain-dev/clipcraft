'use client';

import { useState, useEffect } from 'react';
import Icon from './Icon';
import { apiClient } from '@/lib/api';
import type { Settings } from '@/types';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const [settings, setSettings] = useState<Partial<Settings>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [engine, setEngine] = useState('');
  const [workerStatus, setWorkerStatus] = useState<{
    testing: boolean;
    result?: { ok: boolean; message?: string; error?: string; supports_clipcraft_render?: boolean };
  }>({ testing: false });

  useEffect(() => {
    if (!isOpen) return;
    let active = true;
    apiClient.getSettings().then(result => {
      if (!active) return;
      setSettings(result.settings || {});
      setEngine(result.engine || '');
      setLoading(false);
      setError('');
    }).catch(err => {
      if (!active) return;
      setError(err instanceof Error ? err.message : 'Failed to load settings');
      setLoading(false);
    });
    return () => { active = false; };
  }, [isOpen]);

  const handleTestWorker = async () => {
    if (!settings.remote_worker_url) return;
    setWorkerStatus({ testing: true });
    try {
      const res = await apiClient.testRemoteWorker(settings.remote_worker_url);
      setWorkerStatus({ testing: false, result: res });
    } catch (e) {
      setWorkerStatus({ testing: false, result: { ok: false, error: e instanceof Error ? e.message : 'Connection failed' } });
    }
  };

  const useFastGroqModel = () => {
    setSettings({
      ...settings,
      llm_provider: 'openai_compatible',
      openai_base_url: 'https://api.groq.com/openai/v1',
      openai_model: 'openai/gpt-oss-120b',
    });
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      const payload: Record<string, string> = {};
      Object.entries(settings).forEach(([k, v]) => {
        if (k.endsWith('_set') || typeof v !== 'string') return;
        const isSavedSecret = (k === 'gemini_api_key' || k === 'openai_api_key')
          && Boolean(settings[`${k}_set` as keyof Settings])
          && !v;
        if (!isSavedSecret) payload[k] = v;
      });
      const result = await apiClient.updateSettings(payload);
      setSettings(result.settings || {});
      setEngine(result.engine || '');
      setSaving(false);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings');
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-heading">
          <div>
            <span className="step-label">CONFIGURATION</span>
            <h2 id="settingsTitle">Studio Settings</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close settings" type="button">
            <Icon name="close" />
          </button>
        </div>

        <p className="modal-intro">
          Configure your AI providers, local Whisper models, and download preferences. Settings are saved locally on your machine.
        </p>

        {loading ? (
          <p className="loading-text">Loading settings…</p>
        ) : (
          <form onSubmit={handleSave}>
            {error && <p className="inline-message error">{error}</p>}

            {/* AI Provider Section */}
            <div className="settings-section">
              <h3>AI Moment Picker</h3>
              <p className="field-hint" style={{ marginTop: '2px', marginBottom: '12px' }}>
                Used to find the strongest hooks and viral storylines. Falls back to local acoustic & emotion heuristics if offline or without keys.
              </p>

              <label className="field">
                Provider
                <select
                  value={settings.llm_provider || 'none'}
                  onChange={(e) => setSettings({ ...settings, llm_provider: e.target.value as Settings['llm_provider'] })}
                >
                  <option value="none">None (Local Heuristics Only)</option>
                  <option value="openai_compatible">OpenAI-Compatible (OpenRouter, Groq, Kilo, Ollama API, etc.)</option>
                  <option value="gemini">Google Gemini API</option>
                </select>
              </label>

              {settings.llm_provider === 'gemini' && (
                <div className="provider-fields">
                  <label className="field">
                    Gemini API Key
                    <input
                      type="password"
                      value={settings.gemini_api_key || ''}
                      onChange={(e) => setSettings({ ...settings, gemini_api_key: e.target.value })}
                      placeholder="Paste your Gemini API key"
                    />
                  </label>
                  <label className="field">
                    Gemini Model
                    <input
                      type="text"
                      value={settings.gemini_model || 'gemini-1.5-flash'}
                      onChange={(e) => setSettings({ ...settings, gemini_model: e.target.value })}
                      placeholder="gemini-1.5-flash"
                    />
                  </label>
                </div>
              )}

              {settings.llm_provider === 'openai_compatible' && (
                <div className="provider-fields">
                  <div className="field-hint" style={{ gridColumn: '1 / -1' }}>
                    <button type="button" className="button button-quiet" onClick={useFastGroqModel}>
                      Use fast model (Groq GPT-OSS 120B)
                    </button>
                    <span style={{ display: 'block', marginTop: '6px' }}>
                      Use any OpenAI-compatible service here, including a free open model hosted on your Kaggle notebook. The fast-model button fills Groq settings.
                    </span>
                  </div>
                  <label className="field">
                    Base URL
                    <input
                      type="url"
                      value={settings.openai_base_url || ''}
                      onChange={(e) => setSettings({ ...settings, openai_base_url: e.target.value })}
                      placeholder="https://your-host/v1"
                    />
                  </label>
                  <label className="field">
                    API Key
                    <input
                      type="password"
                      value={settings.openai_api_key || ''}
                      onChange={(e) => setSettings({ ...settings, openai_api_key: e.target.value })}
                      placeholder={settings.openai_api_key_set ? 'Saved key — leave blank to keep it' : 'API key or Kaggle notebook token'}
                    />
                  </label>
                  <label className="field">
                    Model Identifier
                    <input
                      type="text"
                      value={settings.openai_model || ''}
                      onChange={(e) => setSettings({ ...settings, openai_model: e.target.value })}
                      placeholder="e.g. Qwen/Qwen2.5-7B-Instruct"
                    />
                  </label>
                </div>
              )}

              {engine && (
                <div className="engine-status-pill">
                  <span className="status-dot"></span>
                  Active Engine: <strong>{engine}</strong>
                </div>
              )}
            </div>

            {/* Transcription Section */}
            <div className="settings-section">
              <h3>Whisper Speech-to-Text</h3>
              <p className="field-hint" style={{ marginTop: '2px', marginBottom: '12px' }}>
                Used when YouTube auto-captions are not available, or for uploaded video files.
              </p>
              <label className="field">
                Whisper Model
                <select
                  value={settings.whisper_model || 'small'}
                  onChange={(e) => setSettings({ ...settings, whisper_model: e.target.value as Settings['whisper_model'] })}
                >
                  <option value="tiny">Tiny · Fastest (least RAM)</option>
                  <option value="base">Base · Quick & balanced</option>
                  <option value="small">Small · Recommended (high accuracy)</option>
                  <option value="medium">Medium · Very accurate (slower)</option>
                  <option value="large-v3">Large-v3 · Best quality (requires 6GB+ VRAM)</option>
                </select>
              </label>
            </div>

            {/* Video Download Section */}
            <div className="settings-section">
              <h3>YouTube Ingest & Cookies</h3>
              <label className="field">
                Browser Cookies
                <select
                  value={settings.cookies_from_browser || ''}
                  onChange={(e) => setSettings({ ...settings, cookies_from_browser: e.target.value })}
                >
                  <option value="">None (Public downloads)</option>
                  <option value="chrome">Google Chrome</option>
                  <option value="edge">Microsoft Edge</option>
                  <option value="firefox">Mozilla Firefox</option>
                  <option value="brave">Brave</option>
                  <option value="opera">Opera</option>
                  <option value="vivaldi">Vivaldi</option>
                </select>
              </label>
              <p className="field-hint">
                Use browser cookies if YouTube prompts for &quot;confirm you are not a bot&quot; or for age-gated videos.
              </p>
            </div>

            {/* Branding Section */}
            <div className="settings-section">
              <h3>Default Branding</h3>
              <label className="field">
                Brand Watermark Name
                <input
                  type="text"
                  value={settings.brand_name || ''}
                  onChange={(e) => setSettings({ ...settings, brand_name: e.target.value })}
                  placeholder="Your channel or creator handle"
                  maxLength={80}
                />
              </label>
            </div>

            {/* Kaggle / Remote Dual-GPU Worker Section */}
            <div className="settings-section">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <h3>Remote GPU Renderer</h3>
                <span style={{ background: '#1c2818', color: '#86efac', border: '1px solid #2e4d26', padding: '2px 8px', borderRadius: '12px', fontSize: '11px', fontWeight: 600 }}>
                  CAPABILITY CHECKED
                </span>
              </div>
              <p className="field-hint" style={{ marginTop: '2px', marginBottom: '12px' }}>
                Send clip rendering to the cloud GPU. New results download into this studio as soon as each clip is ready.
              </p>
              <label className="field">
                Remote Worker URL
                <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                  <input
                    type="url"
                    value={settings.remote_worker_url || ''}
                    onChange={(e) => setSettings({ ...settings, remote_worker_url: e.target.value })}
                    placeholder="https://xxxx.trycloudflare.com"
                    style={{ flex: 1 }}
                  />
                  <button
                    type="button"
                    className="button button-quiet"
                    onClick={handleTestWorker}
                    disabled={workerStatus.testing || !settings.remote_worker_url}
                    style={{ whiteSpace: 'nowrap', padding: '0 14px' }}
                  >
                    {workerStatus.testing ? 'Testing…' : 'Test Link'}
                  </button>
                </div>
              </label>

              <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', marginTop: '12px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  aria-label="Require remote GPU rendering"
                  checked={(settings.remote_render_required || 'false') === 'true'}
                  onChange={(e) => setSettings({
                    ...settings,
                    remote_render_required: e.target.checked ? 'true' : 'false',
                  })}
                  style={{ marginTop: '3px' }}
                />
                <span>
                  <strong>Require cloud rendering</strong>
                  <span className="field-hint" style={{ display: 'block', marginTop: '3px' }}>
                    When enabled, an offline cloud worker stops the job instead of using this laptop’s GPU. Caption lookup and moment selection still run in the app.
                  </span>
                </span>
              </label>

              {workerStatus.result && (
                <div style={{
                  marginTop: '8px',
                  padding: '8px 12px',
                  borderRadius: '6px',
                  fontSize: '13px',
                  background: workerStatus.result.ok ? '#0f291e' : '#2b1414',
                  border: `1px solid ${workerStatus.result.ok ? '#22c55e44' : '#ef444444'}`,
                  color: workerStatus.result.ok ? '#86efac' : '#fca5a5'
                }}>
                  {workerStatus.result.ok
                    ? `✅ ${workerStatus.result.message || 'Worker connected successfully!'}`
                    : `❌ ${workerStatus.result.error || 'Connection failed'}`}
                </div>
              )}
            </div>

            <div className="modal-actions">
              <button className="button button-quiet" type="button" onClick={onClose} disabled={saving}>
                Cancel
              </button>
              <button className="button button-primary" type="submit" disabled={saving || loading}>
                {saving ? 'Saving…' : 'Save Settings'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
