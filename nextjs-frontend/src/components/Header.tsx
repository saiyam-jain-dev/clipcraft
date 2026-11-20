'use client';

import Icon from './Icon';

interface HeaderProps {
  currentView: 'home' | 'job' | 'projects';
  projectCount: number;
  onViewChange: (view: 'home' | 'job' | 'projects') => void;
  onSettingsClick: () => void;
  onYoutubeAuthClick: () => void;
  remoteWorkerActive?: boolean;
}

export default function Header({
  currentView,
  projectCount,
  onViewChange,
  onSettingsClick,
  onYoutubeAuthClick,
  remoteWorkerActive = false,
}: HeaderProps) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <button
          className="brand brand-btn"
          onClick={() => onViewChange('home')}
          aria-label="ClipCraft home"
          type="button"
        >
          <span className="brand-symbol">
            <Icon name="mark" />
          </span>
          <span className="brand-content">
            <span className="brand-title">
              ClipCraft<span className="brand-dot">.</span>
            </span>
            <span className="brand-author">
              studio
            </span>
          </span>
        </button>

        <nav className="main-nav" aria-label="Studio navigation">
          <button
            className={`nav-button ${currentView === 'home' ? 'active' : ''}`}
            onClick={() => onViewChange('home')}
            type="button"
            aria-current={currentView === 'home' ? 'page' : undefined}
          >
            Studio
          </button>
          <button
            className={`nav-button ${currentView === 'projects' ? 'active' : ''}`}
            onClick={() => onViewChange('projects')}
            type="button"
            aria-current={currentView === 'projects' ? 'page' : undefined}
          >
            Projects {projectCount > 0 && <span className="nav-count">{projectCount}</span>}
          </button>
        </nav>

        <div className="header-actions">
          {remoteWorkerActive ? (
            <span className="local-badge" style={{ background: '#142918', color: '#86efac', borderColor: '#2e4d26' }}>
              <span className="status-dot" style={{ background: '#22c55e' }}></span>
              <span className="badge-text">⚡ Cloud GPU Ready</span>
            </span>
          ) : (
            <span className="local-badge">
              <span className="status-dot"></span>
              <span className="badge-text">🖥️ GTX 1650 Local</span>
            </span>
          )}

          <button
            className="button button-quiet header-btn settings-btn"
            onClick={onSettingsClick}
            type="button"
            aria-label="Open settings"
            title="AI & Studio Settings"
          >
            <Icon name="settings" />
            <span className="btn-label">Settings</span>
          </button>

          <button
            className="youtube-auth-button header-btn yt-btn"
            onClick={onYoutubeAuthClick}
            type="button"
            aria-label="Connect YouTube"
            title="Connect YouTube Channel"
          >
            <Icon name="youtube" />
            <span className="btn-label">YouTube</span>
          </button>
        </div>
      </div>
    </header>
  );
}
