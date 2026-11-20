export interface JobLogEntry {
  t: number;
  level: 'info' | 'warn' | 'error';
  msg: string;
}

export interface Job {
  job_id: string;
  source: string;
  source_label: string;
  options: JobOptions;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  stage: 'download' | 'transcribe' | 'analyze' | 'render' | 'completed';
  progress_pct: number;
  status_message: string;
  created_at: number;
  clips: Clip[];
  thumbnail?: string;
  title?: string;
  video_title?: string;
  video_thumbnail?: string;
  video_uploader?: string;
  video_duration?: number;
  video_resolution?: string;
  clips_count?: number;
  warning?: string;
  error?: string;
  log?: JobLogEntry[];
  compilation_video_url?: string;
}

export type CropMode = 'auto' | 'fill' | 'fit' | 'split' | 'gameplay'
  | 'pip_tl' | 'pip_tr' | 'pip_bl' | 'pip_br' | 'circle' | 'side';
export type CaptionStyle = 'bold' | 'clean' | 'minimal' | 'pop';
export type ProcessingMode = 'fast' | 'deep';
export type JobStage = 'download' | 'transcribe' | 'analyze' | 'render' | 'completed';
export type JobStatusFilter = 'all' | 'active' | 'completed' | 'failed';

export interface JobOptions {
  crop_mode: CropMode;
  mode: ProcessingMode;
  target_clips: number;
  min_duration: number;
  max_duration: number;
  range_start: number;
  range_end: number;
  enable_subtitles: boolean;
  enable_branding: boolean;
  brand_name: string;
  caption_style: CaptionStyle;
  caption_color: string;
  prompt: string;
  language: string;
  focus: 'auto' | 'story' | 'highlights';
}

export interface Clip {
  clip_id: string;
  start_time: number;
  end_time: number;
  duration?: number;
  score?: number;
  virality_score?: number;
  title: string;
  hook?: string;
  transcript?: string;
  caption?: string;
  hashtags?: string[];
  reason?: string;
  rating?: number;
  layout?: string;
  learned_score?: number;
  features?: Record<string, number>;
  scores?: Record<string, number>;
  video_file?: string;
  video_url?: string;
  thumbnail?: string;
  thumb_url?: string;
  subtitle_url?: string;
  preview?: boolean;
}

export interface VideoInfo {
  title: string;
  uploader: string;
  duration_str: string;
  duration_sec: number;
  thumbnail: string;
  is_live: boolean;
}

export interface Settings {
  llm_provider: 'none' | 'gemini' | 'openai_compatible';
  gemini_api_key: string;
  gemini_api_key_set?: boolean;
  gemini_model: string;
  openai_api_key: string;
  openai_api_key_set?: boolean;
  openai_base_url: string;
  openai_model: string;
  whisper_model: 'tiny' | 'base' | 'small' | 'medium' | 'large-v3';
  cookies_from_browser: string;
  brand_name: string;
  remote_worker_url?: string;
  remote_render_required?: string;
}

export interface SocialPackage {
  youtube?: {
    title: string;
    description: string;
  };
  instagram?: {
    caption: string;
  };
  title?: string;
  caption?: string;
  hashtags?: string[];
  reason?: string;
}

export interface ApiResult<T> {
  success: boolean;
  error?: string;
  data?: T;
}
