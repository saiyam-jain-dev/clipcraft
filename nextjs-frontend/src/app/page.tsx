'use client';

import { useState, useEffect } from 'react';
import Header from '@/components/Header';
import HomeView from '@/components/HomeView';
import JobView from '@/components/JobView';
import ProjectsView from '@/components/ProjectsView';
import SettingsModal from '@/components/SettingsModal';
import YoutubeAuthModal from '@/components/YoutubeAuthModal';
import { apiClient } from '@/lib/api';

type View = 'home' | 'job' | 'projects';

export default function Home() {
  const [currentView, setCurrentView] = useState<View>('home');
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [projectCount, setProjectCount] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [youtubeAuthOpen, setYoutubeAuthOpen] = useState(false);
  const [remoteWorkerActive, setRemoteWorkerActive] = useState(false);

  useEffect(() => {
    let active = true;
    apiClient.getSettings().then(async (res) => {
      const url = res.settings?.remote_worker_url;
      if (!url) {
        if (active) setRemoteWorkerActive(false);
        return;
      }
      try {
        const worker = await apiClient.testRemoteWorker(url);
        if (active) setRemoteWorkerActive(Boolean(worker.ok && worker.supports_clipcraft_render));
      } catch {
        if (active) setRemoteWorkerActive(false);
      }
    }).catch(() => {
      if (active) setRemoteWorkerActive(false);
    });
    return () => { active = false; };
  }, [settingsOpen]);

  useEffect(() => {
    const updateCount = async () => {
      try {
        const res = await apiClient.listJobs();
        if (Array.isArray(res.jobs)) {
          setProjectCount(res.jobs.length);
        }
      } catch {
        // ignore network error on first load
      }
    };
    updateCount();
    const interval = setInterval(updateCount, 12000);
    return () => clearInterval(interval);
  }, [currentView]);

  const handleJobCreated = (jobId: string) => {
    setCurrentJobId(jobId);
    setCurrentView('job');
  };

  const handleOpenJob = (jobId: string) => {
    setCurrentJobId(jobId);
    setCurrentView('job');
  };

  const handleNewProject = () => {
    setCurrentJobId(null);
    setCurrentView('home');
  };

  const handleBack = () => {
    setCurrentView('projects');
    setCurrentJobId(null);
  };

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to studio
      </a>
      <Header
        currentView={currentView}
        projectCount={projectCount}
        remoteWorkerActive={remoteWorkerActive}
        onViewChange={(view) => {
          setCurrentView(view);
          if (view === 'home') handleNewProject();
        }}
        onSettingsClick={() => setSettingsOpen(true)}
        onYoutubeAuthClick={() => setYoutubeAuthOpen(true)}
      />
      <main id="main" className="page">
        {currentView === 'home' && <HomeView onJobCreated={handleJobCreated} />}
        {currentView === 'job' && currentJobId && (
          <JobView jobId={currentJobId} onBack={handleBack} onNewProject={handleNewProject} />
        )}
        {currentView === 'projects' && (
          <ProjectsView onOpenJob={handleOpenJob} onNewProject={handleNewProject} standalone={true} />
        )}
      </main>
      <SettingsModal isOpen={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <YoutubeAuthModal isOpen={youtubeAuthOpen} onClose={() => setYoutubeAuthOpen(false)} />
    </>
  );
}
