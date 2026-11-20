import type { Job, VideoInfo, Settings, SocialPackage, JobOptions } from '@/types';

// Server-side: use full backend URL for direct calls
// Client-side: use relative paths so Vercel rewrites work
const API_BASE = typeof window === 'undefined'
  ? (process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:5004')
  : '';

class ApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
    this.name = 'ApiError';
  }
}

async function api<T>(
  path: string,
  options: RequestInit & { timeout?: number } = {}
): Promise<T> {
  const controller = new AbortController();
  const timeout = options.timeout ?? 30000;
  const timer = timeout > 0 ? setTimeout(() => controller.abort(), timeout) : undefined;

  const { timeout: _timeout, ...fetchOptions } = options;
  void _timeout;

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...fetchOptions,
      signal: controller.signal,
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...fetchOptions.headers,
      },
    });

    clearTimeout(timer);

    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error(
        `The server returned an unreadable response (${response.status}). Check your local server and try again.`
      );
    }

    if (!response.ok || data?.success !== true) {
      const error = new ApiError(
        data?.error || `Request failed (${response.status}). Please try again.`,
        response.status
      );
      throw error;
    }

    return data as T;
  } catch (error) {
    clearTimeout(timer);
    if (error instanceof ApiError) throw error;
    if (error instanceof Error && error.name === 'AbortError') {
      throw new Error('The server took too long to respond. Please try again.');
    }
    if (error instanceof TypeError) {
      throw new Error('Cannot reach the local server. Make sure ClipCraft is running, then try again.');
    }
    throw error;
  }
}

export const apiClient = {
  health: () => api<{ success: boolean; engine: string; whisper_model: string }>('/api/health'),

  videoInfo: (url: string) =>
    api<{ success: boolean; info: VideoInfo }>(`/api/video-info?url=${encodeURIComponent(url)}`),

  createClip: (options: Record<string, unknown> | JobOptions, file?: File) => {
    if (file) {
      const formData = new FormData();
      Object.entries(options).forEach(([key, value]) => formData.append(key, String(value)));
      formData.append('video_file', file);
      return api<{ success: boolean; job_id: string }>('/api/clip', {
        method: 'POST',
        body: formData,
        timeout: 0,
      });
    }
    return api<{ success: boolean; job_id: string }>('/api/clip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(options),
      timeout: 0,
    });
  },

  listJobs: () => api<{ success: boolean; jobs: Job[] }>('/api/jobs'),

  getJob: (jobId: string) => api<{ success: boolean; job: Job }>(`/api/jobs/${jobId}`),

  deleteJob: (jobId: string) =>
    api<{ success: boolean }>(`/api/jobs/${jobId}`, { method: 'DELETE' }),

  retryJob: (jobId: string) =>
    api<{ success: boolean; job_id: string }>(`/api/retry/${jobId}`, { method: 'POST' }),

  renderClip: (jobId: string, clipId: string) =>
    api<{ success: boolean; job_id: string; already_rendered?: boolean }>(
      `/api/jobs/${jobId}/clips/${clipId}/render`,
      { method: 'POST' }
    ),

  renderAllClips: (jobId: string) =>
    api<{ success: boolean; job_id: string }>(`/api/jobs/${jobId}/render-all`, { method: 'POST' }),

  compileJob: (jobId: string) =>
    api<{ success: boolean; compilation_video_url: string }>(`/api/jobs/${jobId}/compile`, {
      method: 'POST',
    }),

  trimClip: (jobId: string, clipId: string, startShift: number, endShift: number) =>
    api<{ success: boolean; job_id: string }>(`/api/jobs/${jobId}/clips/${clipId}/trim`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start_shift: startShift, end_shift: endShift }),
    }),

  reeditClip: (jobId: string, clipId: string, changes: Record<string, unknown>) =>
    api<{ success: boolean; job_id: string }>(`/api/jobs/${jobId}/clips/${clipId}/reedit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    }),

  rateClip: (jobId: string, clipId: string, rating: number) =>
    api<{ success: boolean; rating: number; feedback: Record<string, unknown> }>(
      `/api/jobs/${jobId}/clips/${clipId}/feedback`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating }),
      }
    ),

  feedbackSummary: () =>
    api<{ success: boolean; feedback: Record<string, unknown> }>('/api/feedback/summary'),

  socialPackage: (jobId: string, clipId: string) =>
    api<{ success: boolean; package: SocialPackage }>(`/api/social-package/${jobId}/${clipId}`),

  youtubeAuthStatus: () =>
    api<{
      success: boolean;
      authenticated: boolean;
      configured: boolean;
      channel?: {
        id?: string;
        title?: string;
        custom_url?: string;
        thumbnail?: string;
        subscriber_count?: string;
        video_count?: string;
      };
    }>('/api/youtube/auth-status'),

  youtubeGetAuthUrl: (redirectUri?: string) => {
    const q = redirectUri ? `?redirect_uri=${encodeURIComponent(redirectUri)}` : '';
    return api<{ success: boolean; auth_url: string; redirect_uri: string }>(`/api/youtube/auth-url${q}`);
  },

  youtubeSaveCredentials: (clientId: string, clientSecret: string, rawJson?: string) =>
    api<{ success: boolean; message: string }>('/api/youtube/save-credentials', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        client_id: clientId,
        client_secret: clientSecret,
        client_secrets_json: rawJson || '',
      }),
    }),

  youtubeExchangeCode: (code: string, redirectUri?: string) =>
    api<{ success: boolean; channel?: any; message?: string }>('/api/youtube/exchange-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, redirect_uri: redirectUri }),
    }),

  youtubeDisconnect: () =>
    api<{ success: boolean; message: string }>('/api/youtube/disconnect', { method: 'POST' }),

  uploadToYoutube: (
    jobId: string,
    clipId: string,
    title: string,
    description: string,
    tags: string[],
    privacy: string
  ) =>
    api<{ success: boolean; video_id: string; video_url?: string; url?: string }>(`/api/youtube/upload/${jobId}/${clipId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, description, tags, privacy }),
    }),

  uploadAllToYoutube: (jobId: string, privacy: string, clipIds?: string[]) =>
    api<{
      success: boolean;
      total: number;
      uploaded_count: number;
      results: Array<{ clip_id: string; title: string; success: boolean; video_id?: string; video_url?: string; error?: string }>;
    }>(
      `/api/youtube/upload-all/${jobId}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ privacy, clip_ids: clipIds || [] }),
      }
    ),

  getSettings: () =>
    api<{ success: boolean; settings: Partial<Settings>; engine: string }>('/api/settings'),

  updateSettings: (settings: Partial<Settings>) =>
    api<{ success: boolean; settings: Partial<Settings>; engine: string }>('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),

  testRemoteWorker: (url: string) =>
    api<{
      success: boolean;
      ok: boolean;
      error?: string;
      message?: string;
      gpu_count?: number;
      gpus?: Array<{ id: number; name: string }>;
      ram_total_gb?: number;
      is_kaggle?: boolean;
      supports_clipcraft_render?: boolean;
      capabilities?: string[];
    }>('/api/remote_worker/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    }),
};

export { ApiError };
