export const text = (value: unknown, fallback = ''): string => {
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  return fallback;
};

export const number = (value: unknown, fallback = 0): number => {
  if (value !== null && value !== '' && Number.isFinite(Number(value))) return Number(value);
  return fallback;
};

export const clamp = (value: number, min: number, max: number): number => {
  return Math.max(min, Math.min(max, number(value)));
};

export const filename = (value: string): string => {
  const leaf = text(value).split(/[\\/]/).pop();
  return leaf && leaf !== '.' && leaf !== '..' ? leaf : '';
};

export const time = (value: number): string => {
  const seconds = Math.max(0, Math.round(number(value)));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours ? `${hours}:` : ''}${hours ? String(minutes).padStart(2, '0') : minutes}:${String(seconds % 60).padStart(2, '0')}`;
};

export const date = (value: number | string): string => {
  const input = typeof value === 'number' && value < 1e12 ? value * 1000 : value;
  const parsed = new Date(input);
  return Number.isNaN(parsed.getTime())
    ? 'Saved in your workspace'
    : parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
};

export const safeUrl = (value: string, prefixes: string[] | null = null): string => {
  if (!text(value)) return '';
  try {
    const url = new URL(value, location.origin);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return '';
    if (prefixes && (url.origin !== location.origin || !prefixes.some(prefix => url.pathname.startsWith(prefix)))) {
      return '';
    }
    return url.href;
  } catch {
    return '';
  }
};

export const validSourceUrl = (value: string): boolean => {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !!url.hostname && !url.username && !url.password;
  } catch {
    return false;
  }
};

export const statusNames: Record<string, string> = {
  queued: 'Queued',
  processing: 'Processing',
  completed: 'Completed',
  failed: 'Needs attention',
};

export const stages = ['download', 'transcribe', 'analyze', 'render', 'completed'] as const;

export const stageTitles: Record<string, string> = {
  download: 'Bringing in your footage',
  transcribe: 'Listening for the story',
  analyze: 'Finding the best moments',
  render: 'Making the final cuts',
  completed: 'Your moments are ready',
};

export const activeStatuses = new Set(['queued', 'processing']);
