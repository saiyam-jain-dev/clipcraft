'use client';

import { useState, useEffect } from 'react';
import Icon from './Icon';
import { apiClient } from '@/lib/api';
import { text, number, date, safeUrl, statusNames, activeStatuses, clamp } from '@/lib/utils';
import type { Job, JobStatusFilter } from '@/types';

interface ProjectsViewProps {
  onOpenJob: (jobId: string) => void;
  onNewProject: () => void;
  standalone?: boolean;
}

export default function ProjectsView({ onOpenJob, onNewProject, standalone = false }: ProjectsViewProps) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<JobStatusFilter>('all');

  const loadJobs = async () => {
    try {
      const result = await apiClient.listJobs();
      setJobs(result.jobs || []);
      setLoading(false);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load projects');
      setLoading(false);
    }
  };

  useEffect(() => {
    let active = true;
    const refresh = () => {
      apiClient.listJobs().then(result => {
        if (!active) return;
        setJobs(result.jobs || []);
        setLoading(false);
        setError('');
      }).catch(err => {
        if (!active) return;
        setError(err instanceof Error ? err.message : 'Failed to load projects');
        setLoading(false);
      });
    };
    refresh();
    const timer = setInterval(refresh, 10000);
    return () => { active = false; clearInterval(timer); };
  }, []);

  const handleDeleteJob = async (jobId: string) => {
    if (!confirm('Delete this project? This cannot be undone.')) return;
    try {
      await apiClient.deleteJob(jobId);
      setJobs(jobs.filter(j => j.job_id !== jobId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete project');
    }
  };

  const filteredJobs = jobs
    .filter(job => {
      const title = text(job.title, 'Untitled project').toLowerCase();
      const matchesSearch = title.includes(searchQuery.toLowerCase());
      const matchesFilter =
        statusFilter === 'all' ||
        (statusFilter === 'active' && activeStatuses.has(job.status)) ||
        (statusFilter === 'completed' && job.status === 'completed') ||
        (statusFilter === 'failed' && job.status === 'failed');
      return matchesSearch && matchesFilter;
    })
    .sort((a, b) => number(new Date(b.created_at).getTime()) - number(new Date(a.created_at).getTime()));

  return (
    <section className={`projects-section ${standalone ? 'standalone' : ''}`} aria-labelledby="projectsTitle">
      <div className="section-heading">
        <div>
          <div className="eyebrow">YOUR WORKSPACE</div>
          <h2 id="projectsTitle">{standalone ? 'Your projects' : 'Recent projects'}</h2>
          <p>Made here. Saved here. Ready when you are.</p>
        </div>
        <div className="section-actions">
          <button className="icon-button" type="button" onClick={loadJobs} aria-label="Refresh projects">
            <Icon name="refresh" />
          </button>
          <button className="button button-small" type="button" onClick={onNewProject}>
            <Icon name="plus" />
            New project
          </button>
        </div>
      </div>

      <div className="project-toolbar">
        <label className="search-field">
          <span className="sr-only">Search projects</span>
          <input
            type="search"
            placeholder="Search your projects"
            autoComplete="off"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </label>
        <label className="filter-field">
          <span className="sr-only">Filter by status</span>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as JobStatusFilter)}>
            <option value="all">All projects</option>
            <option value="active">In progress</option>
            <option value="completed">Completed</option>
            <option value="failed">Needs attention</option>
          </select>
        </label>
      </div>

      {error && <p className="inline-message error">{error}</p>}
      {loading && <p className="loading-text">Loading your workspace…</p>}

      <div className="projects-grid">
        {filteredJobs.map(job => {
          const thumbnail = safeUrl(job.thumbnail || '');
          const clipsCount = Math.max(0, Math.round(number(job.clips_count)));
          return (
            <div key={job.job_id} className="project-card">
              <button
                className="project-open"
                onClick={() => onOpenJob(job.job_id)}
                aria-label={`Open ${text(job.title, 'Untitled project')}`}
              >
                <div className="project-thumbnail">
                  {thumbnail ? (
                    <img src={thumbnail} alt="" referrerPolicy="no-referrer" />
                  ) : (
                    <div className="thumbnail-placeholder">
                      <Icon name="mark" />
                    </div>
                  )}
                  <span className={`status-badge ${job.status}`}>{statusNames[job.status]}</span>
                </div>
                <div className="project-info">
                  <h3>{text(job.title, 'Untitled project')}</h3>
                  <p className="project-date">{date(job.created_at)}</p>
                  <div className="project-meta">
                    <span>
                      {clipsCount} clip{clipsCount === 1 ? '' : 's'}
                    </span>
                    {activeStatuses.has(job.status) && (
                      <span className="project-progress">{Math.round(clamp(job.progress_pct, 0, 100))}%</span>
                    )}
                  </div>
                </div>
              </button>
              <button
                className="project-delete icon-button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDeleteJob(job.job_id);
                }}
                aria-label={`Delete ${text(job.title, 'Untitled project')}`}
              >
                <Icon name="trash" />
              </button>
            </div>
          );
        })}
      </div>

      {!loading && filteredJobs.length === 0 && (
        <div className="empty-state compact">
          <span className="empty-symbol">
            <Icon name="grid" />
          </span>
          <h3>{searchQuery || statusFilter !== 'all' ? 'No matching projects.' : 'A clean slate.'}</h3>
          <p>
            {searchQuery || statusFilter !== 'all'
              ? 'Try a different search or choose All projects.'
              : 'Add a link or upload a video to create your first project. No demo projects, just your work.'}
          </p>
        </div>
      )}
    </section>
  );
}
