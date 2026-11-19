'use strict';

(() => {
  const $ = id => document.getElementById(id);
  const text = (value, fallback = '') => typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
  const setText = (node, value) => { const next = text(value); if (node.textContent !== next) node.textContent = next; };
  const number = (value, fallback = 0) => value !== null && value !== '' && Number.isFinite(Number(value)) ? Number(value) : fallback;
  const clamp = (value, min, max) => Math.max(min, Math.min(max, number(value)));
  const activeStatuses = new Set(['queued', 'processing']);
  const statusNames = { queued: 'Queued', processing: 'Processing', completed: 'Completed', failed: 'Needs attention' };
  const stages = ['download', 'transcribe', 'analyze', 'render', 'completed'];
  const stageTitles = { download: 'Bringing in your footage', transcribe: 'Listening for the story', analyze: 'Finding the best moments', render: 'Making the final cuts', completed: 'Your moments are ready' };
  let view = 'home';
  let sourceMode = 'url';
  let selectedFile = null;
  let blobUrl = '';
  let sourceInfo = null;
  let sourceAbort = null;
  let sourceEpoch = 0;
  let submitting = false;
  let jobs = [];
  let jobsLoaded = false;
  let jobsRequest = null;
  let projectsTimer = null;
  let currentJobId = null;
  let currentJob = null;
  let jobEpoch = 0;
  let pollTimer = null;
  let pollAbort = null;
  const clips = new Map();
  const clipNodes = new Map();
  let settingsEpoch = 0;
  let settingsSaving = false;
  let socialTarget = null;
  let socialAbort = null;
  let deleteTarget = null;

  function message(id, value = '', kind = '') {
    const node = $(id);
    node.hidden = !value;
    node.className = `inline-message${kind ? ` ${kind}` : ''}`;
    setText(node, value);
  }

  function storage(action, value) {
    try {
      if (action === 'get') return localStorage.getItem('clipcraft.currentJob');
      if (action === 'set') localStorage.setItem('clipcraft.currentJob', value);
      if (action === 'remove') localStorage.removeItem('clipcraft.currentJob');
    } catch { /* Storage is optional in private or restricted browser contexts. */ }
    return null;
  }

  async function api(path, options = {}) {
    const { signal, timeout = 30000, ...requestOptions } = options;
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    else signal?.addEventListener('abort', abort, { once: true });
    const timer = timeout ? setTimeout(abort, timeout) : null;
    try {
      const response = await fetch(path, { ...requestOptions, signal: controller.signal, credentials: 'same-origin', headers: { Accept: 'application/json', ...requestOptions.headers } });
      let data;
      try { data = await response.json(); } catch { throw new Error(`The server returned an unreadable response (${response.status}). Check your local server and try again.`); }
      if (!response.ok || data?.success !== true) {
        const error = new Error(text(data?.error, `Request failed (${response.status}). Please try again.`));
        error.status = response.status;
        throw error;
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError' && !signal?.aborted) throw new Error('The server took too long to respond. Please try again.');
      if (error instanceof TypeError) throw new Error('Cannot reach the local server. Make sure ClipCraft is running, then try again.');
      throw error;
    } finally {
      if (timer) clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
    }
  }

  function safeUrl(value, prefixes = null) {
    if (!text(value)) return '';
    try {
      const url = new URL(value, location.origin);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return '';
      if (prefixes && (url.origin !== location.origin || !prefixes.some(prefix => url.pathname.startsWith(prefix)))) return '';
      return url.href;
    } catch { return ''; }
  }

  function filename(value) {
    const leaf = text(value).split(/[\\/]/).pop();
    return leaf && leaf !== '.' && leaf !== '..' ? leaf : '';
  }

  function time(value) {
    const seconds = Math.max(0, Math.round(number(value)));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours ? `${hours}:` : ''}${hours ? String(minutes).padStart(2, '0') : minutes}:${String(seconds % 60).padStart(2, '0')}`;
  }

  function date(value) {
    const input = typeof value === 'number' && value < 1e12 ? value * 1000 : value;
    const parsed = new Date(input);
    return Number.isNaN(parsed.getTime()) ? 'Saved in your workspace' : parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function statusBadge(node, status) {
    node.className = `status-badge${Object.hasOwn(statusNames, status) ? ` ${status}` : ''}`;
    setText(node, statusNames[status] || 'Unknown status');
  }

  function pauseVideos(root) { root.querySelectorAll('video').forEach(video => video.pause()); }

  function setView(next) {
    view = next;
    $('homeView').hidden = next !== 'home';
    $('jobView').hidden = next !== 'job';
    $('projectsSection').hidden = next === 'job';
    $('projectsSection').classList.toggle('standalone', next === 'projects');
    setText($('projectsTitle'), next === 'projects' ? 'Your projects' : 'Recent projects');
    $('studioNav').classList.toggle('active', next === 'home');
    $('projectsNav').classList.toggle('active', next !== 'home');
    if (next === 'home') { $('studioNav').setAttribute('aria-current', 'page'); $('projectsNav').removeAttribute('aria-current'); }
    else { $('projectsNav').setAttribute('aria-current', 'page'); $('studioNav').removeAttribute('aria-current'); }
    pauseVideos($('jobView'));
    if (next !== 'home') $('uploadPreview').pause();
    clearTimeout(projectsTimer);
    if (next !== 'job') { renderProjects(); loadProjects(); }
  }

  function stopJob() {
    jobEpoch += 1;
    clearTimeout(pollTimer);
    pollTimer = null;
    pollAbort?.abort();
    pollAbort = null;
    currentJobId = null;
    currentJob = null;
  }

  function newProject() {
    if (submitting) { message('formError', 'Your source is being sent to the server. Please wait before starting another project.', 'warning'); return; }
    stopJob();
    cancelSourceCheck();
    removeFile();
    $('clipForm').reset();
    sourceInfo = null;
    ['minDuration', 'maxDuration', 'rangeStart', 'rangeEnd', 'videoUrl', 'brandName', 'videoFile'].forEach(id => $(id).setCustomValidity(''));
    message('formError');
    message('sourceMessage');
    switchSource('url');
    syncOptions();
    setView('home');
    window.scrollTo({ top: 0, behavior: 'auto' });
    $('videoUrl').focus({ preventScroll: true });
  }

  function showProjects() {
    stopJob();
    setView('projects');
    window.scrollTo({ top: 0, behavior: 'auto' });
  }

  function cancelSourceCheck() {
    sourceEpoch += 1;
    sourceAbort?.abort();
    sourceAbort = null;
    $('checkSource').disabled = false;
    $('checkSource').childNodes[0].textContent = 'Check source';
  }

  function switchSource(mode, focus = false) {
    if (mode !== sourceMode) cancelSourceCheck();
    sourceMode = mode;
    const isUrl = mode === 'url';
    $('urlPanel').hidden = !isUrl;
    $('filePanel').hidden = isUrl;
    $('videoUrl').disabled = !isUrl;
    $('videoUrl').required = isUrl;
    $('videoFile').disabled = isUrl;
    $('videoFile').required = !isUrl;
    for (const [id, selected] of [['urlTab', isUrl], ['fileTab', !isUrl]]) {
      $(id).classList.toggle('active', selected);
      $(id).setAttribute('aria-selected', String(selected));
      $(id).tabIndex = selected ? 0 : -1;
    }
    if (isUrl) $('uploadPreview').pause();
    message('sourceMessage');
    message('formError');
    renderSourcePreview();
    if (focus) $(isUrl ? 'urlTab' : 'fileTab').focus();
  }

  function renderSourcePreview() {
    const hasFile = sourceMode === 'file' && selectedFile;
    const hasInfo = sourceMode === 'url' && sourceInfo && sourceInfo.url === $('videoUrl').value.trim();
    $('sourceEmpty').hidden = !!(hasFile || hasInfo);
    $('sourcePreview').hidden = !(hasFile || hasInfo);
    $('uploadPreview').hidden = !hasFile;
    $('sourceThumbnail').hidden = true;
    $('fileDetails').hidden = !selectedFile;
    if (hasFile) {
      setText($('sourcePreviewTitle'), selectedFile.name);
      const duration = $('uploadPreview').duration;
      setText($('sourcePreviewMeta'), `${(selectedFile.size / 1048576).toFixed(1)} MB${Number.isFinite(duration) ? ` · ${time(duration)}` : ' · Local video'}`);
    } else if (hasInfo) {
      setText($('sourcePreviewTitle'), text(sourceInfo.info.title, 'Your video'));
      setText($('sourcePreviewMeta'), [text(sourceInfo.info.uploader), text(sourceInfo.info.duration_str) || (sourceInfo.info.duration_sec ? time(sourceInfo.info.duration_sec) : ''), sourceInfo.info.is_live ? 'Live source' : ''].filter(Boolean).join(' · '));
      const thumbnail = safeUrl(sourceInfo.info.thumbnail);
      if (thumbnail) { $('sourceThumbnail').src = thumbnail; $('sourceThumbnail').hidden = false; }
      else $('sourceThumbnail').removeAttribute('src');
    }
  }

  function selectFile(file) {
    if (!file) return;
    if (!file.size) { message('sourceMessage', 'This file is empty. Choose a video with content.', 'error'); return; }
    if (file.size > 2 * 1024 ** 3) { message('sourceMessage', 'Choose a video smaller than 2 GB.', 'error'); $('videoFile').value = ''; return; }
    if (!/\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(file.name)) {
      message('sourceMessage', 'Choose a video file, such as MP4, MOV or WebM.', 'error');
      $('videoFile').value = '';
      return;
    }
    removeFile();
    selectedFile = file;
    blobUrl = URL.createObjectURL(file);
    $('uploadPreview').src = blobUrl;
    setText($('fileName'), file.name);
    setText($('fileSize'), `${(file.size / 1048576).toFixed(1)} MB · Preview before clipping`);
    $('videoFile').required = false;
    $('videoFile').setCustomValidity('');
    message('sourceMessage');
    message('formError');
    renderSourcePreview();
  }

  function removeFile() {
    $('uploadPreview').pause();
    $('uploadPreview').removeAttribute('src');
    $('uploadPreview').load();
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    blobUrl = '';
    selectedFile = null;
    $('videoFile').value = '';
    $('videoFile').required = sourceMode === 'file';
    $('fileDetails').hidden = true;
    renderSourcePreview();
  }

  async function checkSource() {
    const url = $('videoUrl').value.trim();
    if (!validSourceUrl(url)) { message('sourceMessage', 'Enter a complete public HTTP or HTTPS video URL.', 'error'); $('videoUrl').focus(); return; }
    cancelSourceCheck();
    const epoch = sourceEpoch;
    const controller = new AbortController();
    sourceAbort = controller;
    $('checkSource').disabled = true;
    $('checkSource').childNodes[0].textContent = 'Checking…';
    message('sourceMessage', 'Checking the source. You can create clips without waiting for this preview.');
    try {
      const result = await api(`/api/video-info?url=${encodeURIComponent(url)}`, { signal: controller.signal, timeout: 60000 });
      if (epoch !== sourceEpoch || sourceMode !== 'url' || url !== $('videoUrl').value.trim()) return;
      if (!result.info || typeof result.info !== 'object') throw new Error('No video details were returned.');
      sourceInfo = { url, info: result.info };
      renderSourcePreview();
      message('sourceMessage', result.info.is_live ? 'This appears to be a live source. A completed, downloadable video works best.' : 'Source found. Choose your direction, then create clips.', result.info.is_live ? 'warning' : 'success');
    } catch (error) {
      if (epoch === sourceEpoch) message('sourceMessage', `Preview unavailable: ${error.message} You can still try creating clips.`, 'warning');
    } finally {
      if (epoch === sourceEpoch) { sourceAbort = null; $('checkSource').disabled = false; $('checkSource').childNodes[0].textContent = 'Check source'; }
    }
  }

  function validSourceUrl(value) {
    try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !!url.hostname && !url.username && !url.password; } catch { return false; }
  }

  function syncOptions() {
    $('captionControls').hidden = !$('enableSubtitles').checked;
    $('brandNameField').hidden = !$('enableBranding').checked;
    $('brandName').required = $('enableBranding').checked;
    $('brandName').disabled = !$('enableBranding').checked;
  }

  function validateForm() {
    ['videoUrl', 'videoFile', 'maxDuration', 'rangeEnd', 'brandName'].forEach(id => $(id).setCustomValidity(''));
    if (sourceMode === 'url' && !validSourceUrl($('videoUrl').value.trim())) $('videoUrl').setCustomValidity('Enter a public HTTP or HTTPS video URL. Local paths are not supported.');
    if (sourceMode === 'file') {
      $('videoFile').required = !selectedFile;
      if (!selectedFile) $('videoFile').setCustomValidity('Choose or drop a video file first.');
    }
    if (number($('maxDuration').value) < number($('minDuration').value)) $('maxDuration').setCustomValidity('Maximum length must be at least the minimum length.');
    if (number($('rangeEnd').value) !== 0 && number($('rangeEnd').value) <= number($('rangeStart').value)) $('rangeEnd').setCustomValidity('End must be later than start, or 0 for the full video.');
    if ($('enableBranding').checked && !$('brandName').value.trim()) $('brandName').setCustomValidity('Enter a brand name or turn branding off.');
    const invalid = Array.from($('clipForm').elements).find(element => element.willValidate && !element.validity.valid);
    if (invalid) {
      const details = invalid.closest('details');
      if (details) details.open = true;
      message('formError', invalid.validationMessage, 'error');
      invalid.focus();
      invalid.reportValidity();
      return false;
    }
    return true;
  }

  async function startClipping(event) {
    event.preventDefault();
    if (submitting || !validateForm()) return;
    submitting = true;
    $('generateButton').disabled = true;
    setText($('generateLabel'), sourceMode === 'file' ? 'Uploading video…' : 'Starting your project…');
    message('formError');
    const options = {
      crop_mode: $('cropMode').value,
      focus: $('focus').value,
      target_clips: number($('targetClips').value, 4),
      min_duration: number($('minDuration').value, 20),
      max_duration: number($('maxDuration').value, 60),
      range_start: number($('rangeStart').value),
      range_end: number($('rangeEnd').value),
      enable_subtitles: $('enableSubtitles').checked,
      enable_branding: $('enableBranding').checked,
      brand_name: $('brandName').value.trim(),
      caption_style: $('captionStyle').value,
      caption_color: $('captionColor').value,
      prompt: $('clipPrompt').value.trim(),
      language: $('language').value
    };
    let request;
    if (sourceMode === 'file') {
      const body = new FormData();
      Object.entries(options).forEach(([key, value]) => body.append(key, String(value)));
      body.append('video_file', selectedFile, selectedFile.name);
      request = { method: 'POST', body, timeout: 0 };
    } else request = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...options, url: $('videoUrl').value.trim() }), timeout: 0 };
    try {
      const result = await api('/api/clip', request);
      if (!text(result.job_id)) throw new Error('The server did not return a project ID. Check Projects before submitting again.');
      cancelSourceCheck();
      openJob(String(result.job_id));
      loadProjects();
    } catch (error) { message('formError', error.message, 'error'); }
    finally { submitting = false; $('generateButton').disabled = false; setText($('generateLabel'), 'Create clips'); }
  }

  function loadProjects() {
    clearTimeout(projectsTimer);
    if (jobsRequest) return jobsRequest;
    $('refreshProjects').disabled = true;
    jobsRequest = (async () => {
      try {
        const result = await api('/api/jobs');
        if (!Array.isArray(result.jobs)) throw new Error('The server did not return a project list.');
        jobs = result.jobs.filter(job => job && text(job.job_id));
        jobsLoaded = true;
        message('projectsError');
        renderProjects();
      } catch (error) { message('projectsError', `${error.message} Use Refresh to reconnect.`, 'error'); }
      finally {
        $('projectsLoading').hidden = true;
        $('refreshProjects').disabled = false;
        jobsRequest = null;
        if (view !== 'job' && !document.hidden) projectsTimer = setTimeout(loadProjects, 8000);
      }
    })();
    return jobsRequest;
  }

  function renderProjects() {
    setText($('projectCount'), jobs.length);
    const query = $('projectSearch').value.trim().toLowerCase();
    const filter = $('projectFilter').value;
    let visible = jobs.filter(job => text(job.title, 'Untitled project').toLowerCase().includes(query) && (filter === 'all' || (filter === 'active' ? activeStatuses.has(job.status) : job.status === filter)));
    visible.sort((a, b) => number(new Date(b.created_at).getTime()) - number(new Date(a.created_at).getTime()));
    if (view === 'home') visible = visible.slice(0, 8);
    const fragment = document.createDocumentFragment();
    for (const job of visible) {
      const card = $('projectTemplate').content.firstElementChild.cloneNode(true);
      const title = text(job.title, 'Untitled project');
      const image = card.querySelector('img');
      const thumbnail = safeUrl(job.thumbnail);
      if (thumbnail) {
        image.src = thumbnail;
        image.hidden = false;
        card.querySelector('.thumbnail-placeholder').hidden = true;
        image.addEventListener('error', () => { image.hidden = true; card.querySelector('.thumbnail-placeholder').hidden = false; });
      }
      statusBadge(card.querySelector('.status-badge'), job.status);
      setText(card.querySelector('h3'), title);
      setText(card.querySelector('.project-date'), date(job.created_at));
      const count = Math.max(0, Math.round(number(job.clips_count)));
      setText(card.querySelector('.project-clips'), `${count} clip${count === 1 ? '' : 's'}`);
      setText(card.querySelector('.project-progress'), activeStatuses.has(job.status) ? `${Math.round(clamp(job.progress_pct, 0, 100))}%` : '');
      const open = card.querySelector('.project-open');
      open.setAttribute('aria-label', `Open ${title}`);
      open.addEventListener('click', () => openJob(String(job.job_id)));
      const remove = card.querySelector('.project-delete');
      remove.setAttribute('aria-label', `Delete ${title}`);
      remove.addEventListener('click', () => openDelete(job));
      fragment.append(card);
    }
    $('projectsGrid').replaceChildren(fragment);
    $('projectsEmpty').hidden = !jobsLoaded || visible.length > 0;
    setText($('projectsEmptyTitle'), query || filter !== 'all' ? 'No matching projects.' : 'A clean slate.');
    setText($('projectsEmptyText'), query || filter !== 'all' ? 'Try a different search or choose All projects.' : 'Add a link or upload a video to create your first project. No demo projects, just your work.');
  }

  function openJob(id, focus = true) {
    if (currentJobId === id && view === 'job') return;
    stopJob();
    currentJobId = id;
    storage('set', id);
    clips.clear();
    clipNodes.clear();
    $('clipsGrid').replaceChildren();
    setText($('clipCount'), '0');
    $('clipsEmpty').hidden = false;
    setText($('clipsEmpty').querySelector('h3'), 'Your moments are on their way.');
    setText($('clipsEmpty').querySelector('p'), 'Clips appear here as soon as each render is ready. You can leave this page and return from Projects.');
    setText($('jobTitle'), 'Loading project…');
    setText($('jobSource'), '');
    setText($('jobStatus'), 'Loading');
    $('jobStatus').className = 'status-badge';
    setText($('statusTitle'), 'Connecting to your project');
    setText($('statusMessage'), 'Fetching the latest progress…');
    setText($('progressLabel'), '0%');
    $('progressFill').style.width = '0%';
    $('jobProgress').setAttribute('aria-valuenow', '0');
    $('processingPanel').classList.remove('failed');
    $('stageList').querySelectorAll('li').forEach(node => { node.classList.remove('done', 'active'); node.removeAttribute('aria-current'); });
    setText($('jobEngine'), '');
    $('jobSourceCard').hidden = true;
    renderLog([]);
    message('jobError');
    message('jobWarning');
    $('jobRecovery').hidden = true;
    $('retryJob').hidden = true;
    $('retryJob').disabled = false;
    setView('job');
    window.scrollTo({ top: 0, behavior: 'auto' });
    if (focus) $('jobTitle').focus({ preventScroll: true });
    pollJob(jobEpoch);
  }

  async function pollJob(epoch) {
    if (epoch !== jobEpoch || !currentJobId || pollAbort) return;
    const controller = new AbortController();
    pollAbort = controller;
    const id = currentJobId;
    let delay = null;
    try {
      const result = await api(`/api/jobs/${encodeURIComponent(id)}`, { signal: controller.signal });
      if (epoch !== jobEpoch) return;
      if (!result.job || typeof result.job !== 'object') throw new Error('Project details are missing from the server response.');
      const previousStatus = currentJob?.status;
      currentJob = result.job;
      renderJob(currentJob);
      if (activeStatuses.has(currentJob.status)) delay = 1500;
      else if (previousStatus !== currentJob.status) loadProjects();
    } catch (error) {
      if (epoch !== jobEpoch) return;
      message('jobError', error.status === 404 ? 'This project could not be found. It may have been removed. Return to Projects to see your saved work.' : `${error.message} Reconnecting automatically; any ready clips stay available.`, 'error');
      $('jobRecovery').hidden = false;
      $('refreshJob').hidden = false;
      $('retryJob').hidden = currentJob?.status !== 'failed';
      if (error.status !== 404) delay = 3000;
      else if (storage('get') === id) storage('remove');
    } finally {
      if (pollAbort === controller) pollAbort = null;
      if (epoch === jobEpoch && delay !== null) pollTimer = setTimeout(() => pollJob(epoch), delay);
    }
  }

  function renderLog(entries) {
    const box = $('logLines');
    const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.replaceChildren(...entries.map(entry => {
      const line = document.createElement('div');
      line.className = `log-line ${['warn', 'error'].includes(entry.level) ? entry.level : 'info'}`;
      const stamp = document.createElement('span');
      stamp.className = 'log-time';
      stamp.textContent = new Date(number(entry.t) * 1000).toLocaleTimeString();
      line.append(stamp, document.createTextNode(` ${text(entry.msg)}`));
      return line;
    }));
    setText($('logCount'), String(entries.length));
    if (stick) box.scrollTop = box.scrollHeight;
  }

  function renderSource(job) {
    const thumb = text(job.video_thumbnail);
    $('jobSourceCard').hidden = !(thumb || text(job.video_title));
    $('jobThumb').hidden = !thumb;
    if (thumb && $('jobThumb').getAttribute('src') !== thumb) $('jobThumb').src = thumb;
    setText($('jobVideoTitle'), text(job.video_title) || text(job.source_label));
    setText($('jobVideoMeta'), [text(job.video_uploader), number(job.video_duration) ? time(number(job.video_duration)) : '', text(job.video_resolution)].filter(Boolean).join(' · '));
  }

  function renderJob(job) {
    const status = text(job.status);
    const completed = status === 'completed';
    const failed = status === 'failed';
    const progress = completed ? 100 : Math.round(clamp(job.progress_pct, 0, 100));
    setText($('jobTitle'), text(job.video_title) || text(job.source_label) || 'Untitled project');
    setText($('jobSource'), text(job.source_label));
    statusBadge($('jobStatus'), status);
    $('processingPanel').classList.toggle('failed', failed);
    setText($('statusTitle'), failed ? 'This run needs attention' : status === 'queued' ? 'Your project is in the queue' : stageTitles[job.stage] || (completed ? stageTitles.completed : 'Working on your footage'));
    setText($('statusMessage'), text(job.status_message) || (completed ? 'Render the moments you want, then download them.' : failed ? 'Any clips already rendered are still available below.' : 'Your local engine is working. You can come back from Projects.'));
    setText($('progressLabel'), `${progress}%`);
    $('progressFill').style.width = `${progress}%`;
    $('jobProgress').setAttribute('aria-valuenow', String(progress));
    let stageIndex = stages.indexOf(job.stage);
    if (completed) stageIndex = stages.length - 1;
    $('stageList').querySelectorAll('li').forEach((node, index) => {
      node.classList.toggle('done', completed || index < stageIndex);
      node.classList.toggle('active', !failed && !completed && status !== 'queued' && index === stageIndex);
      if (index === stageIndex && !completed && !failed) node.setAttribute('aria-current', 'step');
      else node.removeAttribute('aria-current');
    });
    const engines = { ai: 'AI moment selection · Local transcription, reframing, and captions', heuristic: 'Local hook scoring · Add a free Gemini or Groq key in Settings for a stronger editor', audio: 'Audio peaks · Little speech in this video', visual: 'Visual action · Cuts and movement, with little speech', highlights: 'Highlight mode · Strong lines plus the most active stretches' };
    setText($('jobEngine'), engines[job.engine] || 'Local processing · Review framing, captions and context before posting');
    message('jobWarning', text(job.warning), 'warning');
    renderSource(job);
    renderLog(Array.isArray(job.log) ? job.log : []);
    message('jobError', failed ? text(job.error) || 'Processing failed. Check the source and engine settings, then try again.' : '', 'error');
    $('jobRecovery').hidden = !failed;
    $('retryJob').hidden = !failed;
    $('refreshJob').hidden = !failed;
    if (Array.isArray(job.clips)) {
      job.clips.forEach((clip, index) => {
        if (!clip || typeof clip !== 'object') return;
        const key = text(clip.clip_id) || `pending-${index}`;
        const merged = { ...clips.get(key), ...clip };
        clips.set(key, merged);
        updateClip(key, merged);
      });
    }
    const busy = activeStatuses.has(status);
    $('clipsGrid').querySelectorAll('.trim-apply, .clip-render').forEach(button => { button.disabled = busy; });
    const pendingRenders = [...clips.values()].some(clip => clip && !clip.video_file && !clip.video_url && text(clip.clip_id));
    $('renderAll').hidden = !pendingRenders || busy;
    $('renderAll').disabled = busy;
    sortClips();
    setText($('clipCount'), clips.size);
    $('clipsEmpty').hidden = clips.size > 0;
    if (!clips.size && (failed || completed)) {
      setText($('clipsEmpty').querySelector('h3'), failed ? 'No clips were rendered yet.' : 'No clips were produced.');
      setText($('clipsEmpty').querySelector('p'), failed ? 'Check the error above. You can retry this source with the same options or start a new project.' : 'Try a wider source range, shorter durations, or clearer speech. You can adjust your settings in a new project.');
    }
  }

  function updateClip(key, clip) {
    let card = clipNodes.get(key);
    if (!card) {
      card = $('clipTemplate').content.firstElementChild.cloneNode(true);
      card.dataset.clipKey = key;
      const warning = document.createElement('p');
      warning.className = 'inline-message warning video-message';
      warning.hidden = true;
      card.querySelector('.clip-body').prepend(warning);
      const video = card.querySelector('video');
      video.addEventListener('error', () => { setText(warning, 'Preview unavailable. Try the download, or check the rendered file.'); warning.hidden = false; });
      video.addEventListener('loadedmetadata', () => { warning.hidden = true; });
      video.addEventListener('play', () => { $('clipsGrid').querySelectorAll('video').forEach(other => { if (other !== video) other.pause(); }); });
      const scores = card.querySelector('.score-breakdown');
      for (const name of ['hook', 'flow', 'value', 'trend']) {
        const item = document.createElement('div');
        item.className = 'score-item';
        item.dataset.score = name;
        const label = document.createElement('span');
        setText(label, name.charAt(0).toUpperCase() + name.slice(1));
        const value = document.createElement('strong');
        const bar = document.createElement('div');
        bar.className = 'score-bar';
        bar.setAttribute('aria-hidden', 'true');
        bar.append(document.createElement('span'));
        item.append(label, value, bar);
        scores.append(item);
      }
      const jobId = currentJobId;
      card.querySelector('.clip-social').addEventListener('click', () => openSocial(jobId, text(clips.get(key)?.clip_id), text(clips.get(key)?.title, 'Untitled clip')));
      card.querySelector('.trim-apply').addEventListener('click', () => trimClip(jobId, key, card));
      card.querySelector('.clip-render').addEventListener('click', () => renderClip(jobId, key, card));
      clipNodes.set(key, card);
      $('clipsGrid').append(card);
    }
    const signature = JSON.stringify(clip);
    if (card._signature === signature) return;
    card._signature = signature;
    card.querySelector('.trim-start').value = '0';
    card.querySelector('.trim-end').value = '0';
    const title = text(clip.title, 'Untitled clip');
    setText(card.querySelector('.clip-title'), title);
    setText(card.querySelector('.clip-hook'), text(clip.hook));
    const score = number(clip.virality_score, NaN);
    setText(card.querySelector('.clip-score'), Number.isFinite(score) ? Math.round(clamp(score, 1, 99)) : '—');
    const duration = number(clip.duration, Math.max(0, number(clip.end_time) - number(clip.start_time)));
    setText(card.querySelector('.clip-duration'), time(duration));
    setText(card.querySelector('.clip-source-time'), `Source ${time(clip.start_time)} – ${time(number(clip.end_time, number(clip.start_time) + duration))}`);
    setText(card.querySelector('.clip-reason'), text(clip.reason, 'No selection explanation was returned.'));
    setText(card.querySelector('.clip-transcript'), text(clip.transcript, 'No transcript was returned for this clip.'));
    setText(card.querySelector('.clip-caption'), text(clip.caption));
    setText(card.querySelector('.clip-hashtags'), Array.isArray(clip.hashtags) ? clip.hashtags.map(value => text(value)).filter(Boolean).join(' ') : text(clip.hashtags));
    for (const name of ['hook', 'flow', 'value', 'trend']) {
      const item = card.querySelector(`[data-score="${name}"]`);
      const value = number(clip.scores?.[name], NaN);
      setText(item.querySelector('strong'), Number.isFinite(value) ? String(Math.round(clamp(value, 0, 100))) : '—');
      item.querySelector('.score-bar > span').style.width = `${Number.isFinite(value) ? clamp(value, 0, 100) : 0}%`;
    }
    const layoutNames = { auto: 'Auto layout', fill: 'Center crop', fit: 'Full frame', split: 'Split layout', gameplay: 'Gameplay layout' };
    setText(card.querySelector('.clip-layout'), layoutNames[clip.layout] || text(clip.layout, 'Vertical clip'));
    const video = card.querySelector('video');
    const preview = card.querySelector('.clip-preview');
    const videoUrl = safeUrl(clip.video_url, ['/video/']) || (filename(clip.video_file) ? safeUrl(`/video/${encodeURIComponent(filename(clip.video_file))}`, ['/video/']) : '');
    video.setAttribute('aria-label', `Preview ${title}`);
    if (videoUrl && video.dataset.source !== videoUrl) { video.src = videoUrl; video.dataset.source = videoUrl; }
    video.hidden = !videoUrl;
    const poster = safeUrl(clip.thumb_url, ['/media/']);
    if (poster) video.poster = poster;
    const pending = !videoUrl;
    if (pending && poster) { preview.src = poster; preview.hidden = false; }
    else { preview.hidden = true; preview.removeAttribute('src'); }
    if (!videoUrl) { setText(card.querySelector('.video-message'), pending && poster ? 'Preview frame only — render this clip to get the video.' : 'This clip’s video is not available yet.'); card.querySelector('.video-message').hidden = false; }
    const renderButton = card.querySelector('.clip-render');
    renderButton.hidden = !pending || !text(clip.clip_id);
    setText(renderButton, clip.rendering ? 'Rendering…' : 'Render clip');
    renderButton.disabled = Boolean(clip.rendering);
    card.querySelector('.clip-trim').hidden = pending;
    let file = filename(clip.video_file);
    if (!file && videoUrl) {
      try { file = decodeURIComponent(filename(new URL(videoUrl).pathname)); } catch { file = ''; }
    }
    const download = card.querySelector('.clip-download');
    download.hidden = !file;
    if (file) { download.href = `/download/${encodeURIComponent(file)}`; download.setAttribute('aria-label', `Download ${title}`); }
    const subtitleUrl = safeUrl(clip.subtitle_url, ['/download/']);
    const subtitles = card.querySelector('.subtitle-download');
    subtitles.hidden = !subtitleUrl;
    if (subtitleUrl) { subtitles.href = subtitleUrl; subtitles.setAttribute('aria-label', `Download captions for ${title}`); }
    card.querySelector('.clip-social').disabled = !text(clip.clip_id);
  }

  async function renderClip(jobId, key, card) {
    const note = card.querySelector('.trim-message');
    const button = card.querySelector('.clip-render');
    const clipId = text(clips.get(key)?.clip_id);
    if (!clipId || button.disabled || jobId !== currentJobId) return;
    note.hidden = true;
    button.disabled = true;
    setText(button, 'Rendering…');
    const epoch = jobEpoch;
    try {
      await api(`/api/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}/render`, { method: 'POST', timeout: 0 });
      if (epoch !== jobEpoch) return;
      clearTimeout(pollTimer);
      pollJob(epoch);
      $('processingPanel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (error) {
      setText(note, error.message);
      note.hidden = false;
      button.disabled = false;
      setText(button, 'Render clip');
    }
  }

  async function renderAllClips() {
    if (!currentJobId || $('renderAll').disabled) return;
    const id = currentJobId;
    const epoch = jobEpoch;
    $('renderAll').disabled = true;
    try {
      await api(`/api/jobs/${encodeURIComponent(id)}/render-all`, { method: 'POST', timeout: 0 });
      if (epoch === jobEpoch) { clearTimeout(pollTimer); pollJob(epoch); $('processingPanel').scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
    } catch (error) { if (epoch === jobEpoch) message('jobError', `Render failed: ${error.message}`, 'error'); }
    finally { if (epoch === jobEpoch) $('renderAll').disabled = false; }
  }

  async function trimClip(jobId, key, card) {
    const note = card.querySelector('.trim-message');
    const button = card.querySelector('.trim-apply');
    const clipId = text(clips.get(key)?.clip_id);
    if (!clipId || button.disabled || jobId !== currentJobId) return;
    const body = { start_shift: number(card.querySelector('.trim-start').value), end_shift: number(card.querySelector('.trim-end').value) };
    note.hidden = true;
    button.disabled = true;
    const epoch = jobEpoch;
    try {
      await api(`/api/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}/trim`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (epoch !== jobEpoch) return;
      card.querySelector('video').pause();
      clearTimeout(pollTimer);
      pollJob(epoch);
      $('processingPanel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (error) {
      setText(note, error.message);
      note.hidden = false;
      button.disabled = false;
    }
  }

  function sortClips() {
    const order = Array.from(clips.keys()).sort((a, b) => $('clipSort').value === 'time' ? number(clips.get(a).start_time) - number(clips.get(b).start_time) : number(clips.get(b).virality_score) - number(clips.get(a).virality_score) || number(clips.get(a).start_time) - number(clips.get(b).start_time));
    const grid = $('clipsGrid');
    order.forEach((key, index) => { const card = clipNodes.get(key); if (grid.children[index] !== card) grid.insertBefore(card, grid.children[index] || null); });
  }

  async function retryJob() {
    if (!currentJobId || $('retryJob').disabled) return;
    const id = currentJobId;
    const epoch = jobEpoch;
    $('retryJob').disabled = true;
    try {
      const result = await api(`/api/retry/${encodeURIComponent(id)}`, { method: 'POST', timeout: 0 });
      if (!text(result.job_id)) throw new Error('The server did not return a new project ID.');
      loadProjects();
      if (epoch === jobEpoch) {
        if (String(result.job_id) === currentJobId) { currentJob = null; $('retryJob').disabled = false; pollJob(jobEpoch); }
        else openJob(String(result.job_id));
      }
    } catch (error) { if (epoch === jobEpoch) message('jobError', `Retry failed: ${error.message}`, 'error'); }
    finally { if (epoch === jobEpoch) $('retryJob').disabled = false; }
  }

  async function loadHealth() {
    try {
      const result = await api('/api/health');
      const engine = text(result.engine, 'Local engine');
      setText($('engineLabel'), `${engine} · Whisper ${text(result.whisper_model, 'local')}`);
      const heuristic = /heuristic|no AI key/i.test(engine);
      setText($('generationNote'), heuristic
        ? 'Runs on this computer. Settings has no AI key yet, so moments are picked by local hook rules. Add a free Gemini or Groq key for stronger clips.'
        : `Runs on this computer. Moment selection uses ${engine}. You get previews first and only render the clips you keep.`);
    } catch (error) { setText($('engineLabel'), 'Engine unavailable. Check that your local server is running.'); }
  }

  function providerFields() {
    const provider = $('llmProvider').value;
    $('geminiFields').hidden = provider !== 'gemini';
    $('openaiFields').hidden = provider !== 'openai_compatible';
    ['openaiBase', 'openaiModel'].forEach(id => { $(id).required = provider === 'openai_compatible'; $(id).disabled = provider !== 'openai_compatible'; });
    $('geminiModel').required = provider === 'gemini';
    $('geminiModel').disabled = provider !== 'gemini';
  }

  function applySettings(result) {
    const settings = result.settings;
    if (!settings || typeof settings !== 'object') throw new Error('No settings were returned by the server.');
    $('settingsBrand').value = text(settings.brand_name);
    $('llmProvider').value = ['gemini', 'openai_compatible', 'none'].includes(settings.llm_provider) ? settings.llm_provider : 'none';
    $('geminiModel').value = text(settings.gemini_model, 'gemini-3.5-flash-lite');
    $('openaiBase').value = text(settings.openai_base_url, 'https://api.groq.com/openai/v1');
    $('openaiModel').value = text(settings.openai_model, 'llama-3.3-70b-versatile');
    for (const [id, value] of [['whisperModel', settings.whisper_model], ['cookiesBrowser', settings.cookies_from_browser]]) {
      const select = $(id);
      const incoming = text(value, id === 'whisperModel' ? 'small' : '');
      if (!Array.from(select.options).some(option => option.value === incoming)) {
        const option = document.createElement('option');
        option.value = incoming;
        setText(option, incoming || 'None');
        select.append(option);
      }
      select.value = incoming;
    }
    $('geminiKey').value = '';
    $('openaiKey').value = '';
    $('geminiKey').placeholder = settings.gemini_api_key_set ? 'Saved key · leave blank to keep' : 'Enter Gemini API key';
    $('openaiKey').placeholder = settings.openai_api_key_set ? 'Saved key · leave blank to keep' : 'Enter provider API key';
    $('removeGeminiKey').checked = false;
    $('removeOpenaiKey').checked = false;
    $('geminiKey').disabled = false;
    $('openaiKey').disabled = false;
    setText($('settingsEngine'), text(result.engine, 'Local processing engine'));
    if (!$('brandName').value) $('brandName').value = text(settings.brand_name);
    providerFields();
  }

  async function loadSettings() {
    if (settingsSaving) return;
    const epoch = ++settingsEpoch;
    $('settingsFields').disabled = true;
    $('saveSettings').disabled = true;
    $('reloadSettings').disabled = true;
    setText($('settingsEngine'), 'Loading settings…');
    message('settingsMessage');
    try {
      const result = await api('/api/settings');
      if (epoch !== settingsEpoch || !$('settingsDialog').open) return;
      applySettings(result);
      $('settingsFields').disabled = false;
      $('saveSettings').disabled = false;
    } catch (error) { if (epoch === settingsEpoch) message('settingsMessage', error.message, 'error'); }
    finally { if (epoch === settingsEpoch) $('reloadSettings').disabled = false; }
  }

  function openSettings() {
    if ($('settingsDialog').open) return;
    $('settingsDialog').showModal();
    loadSettings();
  }

  async function saveSettings(event) {
    event.preventDefault();
    if (settingsSaving || $('saveSettings').disabled) return;
    if (!$('settingsForm').reportValidity()) return;
    const payload = {
      brand_name: $('settingsBrand').value.trim(),
      llm_provider: $('llmProvider').value,
      whisper_model: $('whisperModel').value,
      cookies_from_browser: $('cookiesBrowser').value
    };
    if ($('llmProvider').value === 'gemini') payload.gemini_model = $('geminiModel').value.trim();
    if ($('llmProvider').value === 'openai_compatible') {
      if (!validSourceUrl($('openaiBase').value.trim())) { message('settingsMessage', 'Enter a complete HTTP or HTTPS API base URL.', 'error'); $('openaiBase').focus(); return; }
      payload.openai_base_url = $('openaiBase').value.trim();
      payload.openai_model = $('openaiModel').value.trim();
    }
    if ($('removeGeminiKey').checked) payload.gemini_api_key = '';
    else if ($('geminiKey').value.trim()) payload.gemini_api_key = $('geminiKey').value.trim();
    if ($('removeOpenaiKey').checked) payload.openai_api_key = '';
    else if ($('openaiKey').value.trim()) payload.openai_api_key = $('openaiKey').value.trim();
    settingsSaving = true;
    $('saveSettings').disabled = true;
    $('settingsFields').disabled = true;
    $('reloadSettings').disabled = true;
    message('settingsMessage', 'Saving your local settings…');
    const epoch = settingsEpoch;
    try {
      const result = await api('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      loadHealth();
      if (epoch === settingsEpoch && $('settingsDialog').open) {
        applySettings(result);
        message('settingsMessage', 'Settings saved. Changes apply to new projects; running projects keep their existing options.', 'success');
      }
    } catch (error) { if (epoch === settingsEpoch) message('settingsMessage', error.message, 'error'); }
    finally {
      settingsSaving = false;
      if (epoch === settingsEpoch) { $('saveSettings').disabled = false; $('settingsFields').disabled = false; $('reloadSettings').disabled = false; }
      else if ($('settingsDialog').open) loadSettings();
    }
  }

  function openSocial(jobId, clipId, title) {
    if (!clipId) return;
    socialTarget = { jobId, clipId };
    setText($('socialClipTitle'), title);
    $('socialContent').hidden = true;
    $('youtubeTitle').value = '';
    $('youtubeDescription').value = '';
    $('instagramCaption').value = '';
    if (!$('socialDialog').open) $('socialDialog').showModal();
    loadSocial();
  }

  async function loadSocial() {
    if (!socialTarget) return;
    socialAbort?.abort();
    const controller = new AbortController();
    socialAbort = controller;
    const target = socialTarget;
    $('reloadSocial').disabled = true;
    message('socialMessage', 'Preparing your social copy…');
    try {
      const result = await api(`/api/social-package/${encodeURIComponent(target.jobId)}/${encodeURIComponent(target.clipId)}`, { signal: controller.signal, timeout: 60000 });
      if (socialAbort !== controller || !$('socialDialog').open) return;
      const pack = result.package;
      if (!pack || typeof pack !== 'object') throw new Error('No social metadata was returned.');
      $('youtubeTitle').value = text(pack.youtube?.title);
      $('youtubeDescription').value = text(pack.youtube?.description);
      $('instagramCaption').value = text(pack.instagram?.caption);
      $('socialContent').hidden = false;
      message('socialMessage');
    } catch (error) { if (socialAbort === controller && $('socialDialog').open) message('socialMessage', `Could not load social copy: ${error.message}`, 'error'); }
    finally { if (socialAbort === controller) { socialAbort = null; $('reloadSocial').disabled = false; } }
  }

  async function copySocial(platform) {
    const value = platform === 'youtube' ? `${$('youtubeTitle').value}\n\n${$('youtubeDescription').value}`.trim() : $('instagramCaption').value;
    if (!value.trim()) { message('socialMessage', 'There is no copy yet. Add text to the fields or reload the package.', 'warning'); return; }
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
      else {
        const field = document.createElement('textarea');
        field.value = value;
        field.className = 'sr-only';
        $('socialDialog').append(field);
        field.select();
        const copied = document.execCommand('copy');
        field.remove();
        if (!copied) throw new Error('Clipboard access is unavailable.');
      }
      if ($('socialDialog').open) message('socialMessage', 'Copied. Review and paste it into your next post.', 'success');
    } catch { message('socialMessage', 'Clipboard access was denied. Select the text in the fields and copy it manually.', 'warning'); }
  }

  function openDelete(job) {
    deleteTarget = { id: String(job.job_id), title: text(job.title, 'Untitled project') };
    setText($('deleteDescription'), `“${deleteTarget.title}”${activeStatuses.has(job.status) ? ' is still processing. Wait until it finishes before deleting it.' : ' will be deleted from your local workspace.'}`);
    message('deleteError');
    $('confirmDelete').disabled = activeStatuses.has(job.status);
    $('deleteDialog').showModal();
  }

  async function deleteProject() {
    if (!deleteTarget || $('confirmDelete').disabled) return;
    const target = deleteTarget;
    $('confirmDelete').disabled = true;
    try {
      await api(`/api/jobs/${encodeURIComponent(target.id)}`, { method: 'DELETE' });
      if (storage('get') === target.id) storage('remove');
      jobs = jobs.filter(job => String(job.job_id) !== target.id);
      renderProjects();
      if (currentJobId === target.id) showProjects();
      if (deleteTarget === target) $('deleteDialog').close();
      await loadProjects();
    } catch (error) { if (deleteTarget === target) message('deleteError', error.message, 'error'); }
    finally { if (deleteTarget === target) $('confirmDelete').disabled = false; }
  }

  $('clipForm').noValidate = true;
  $('clipForm').addEventListener('submit', startClipping);
  $('clipForm').addEventListener('input', event => { event.target.setCustomValidity?.(''); message('formError'); });
  $('urlTab').addEventListener('click', () => switchSource('url'));
  $('fileTab').addEventListener('click', () => switchSource('file'));
  document.querySelector('.source-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    switchSource(event.key === 'Home' ? 'url' : event.key === 'End' ? 'file' : sourceMode === 'url' ? 'file' : 'url', true);
  });
  $('videoUrl').addEventListener('input', () => { cancelSourceCheck(); sourceInfo = null; message('sourceMessage'); renderSourcePreview(); });
  $('checkSource').addEventListener('click', checkSource);
  $('tryExample').addEventListener('click', () => { switchSource('url'); $('videoUrl').value = 'https://www.youtube.com/watch?v=arj7oStGLkU'; $('videoUrl').setCustomValidity(''); sourceInfo = null; renderSourcePreview(); checkSource(); });
  $('videoFile').addEventListener('change', event => selectFile(event.target.files[0]));
  $('removeFile').addEventListener('click', () => { removeFile(); message('sourceMessage'); });
  $('uploadPreview').addEventListener('loadedmetadata', renderSourcePreview);
  $('uploadPreview').addEventListener('error', () => { if (selectedFile && sourceMode === 'file') message('sourceMessage', 'This browser cannot preview the file. You can still try processing it with the local engine.', 'warning'); });
  $('sourceThumbnail').addEventListener('error', () => { $('sourceThumbnail').hidden = true; });
  for (const eventName of ['dragenter', 'dragover']) $('dropzone').addEventListener(eventName, event => { event.preventDefault(); if (sourceMode === 'file') $('dropzone').classList.add('dragging'); });
  for (const eventName of ['dragleave', 'drop']) $('dropzone').addEventListener(eventName, event => { event.preventDefault(); $('dropzone').classList.remove('dragging'); });
  $('dropzone').addEventListener('drop', event => { if (sourceMode === 'file') selectFile(event.dataTransfer?.files[0]); });
  $('enableSubtitles').addEventListener('change', syncOptions);
  $('enableBranding').addEventListener('change', syncOptions);
  $('newProject').addEventListener('click', newProject);
  $('newFromJob').addEventListener('click', newProject);
  $('studioNav').addEventListener('click', () => { stopJob(); setView('home'); window.scrollTo({ top: 0, behavior: 'auto' }); });
  $('brandHome').addEventListener('click', event => { event.preventDefault(); stopJob(); setView('home'); window.scrollTo({ top: 0, behavior: 'auto' }); });
  $('projectsNav').addEventListener('click', showProjects);
  $('backProjects').addEventListener('click', showProjects);
  $('refreshProjects').addEventListener('click', loadProjects);
  $('projectSearch').addEventListener('input', renderProjects);
  $('projectFilter').addEventListener('change', renderProjects);
  $('clipSort').addEventListener('change', sortClips);
  $('renderAll').addEventListener('click', renderAllClips);
  $('retryJob').addEventListener('click', retryJob);
  $('refreshJob').addEventListener('click', () => { clearTimeout(pollTimer); pollJob(jobEpoch); });
  $('settingsOpen').addEventListener('click', openSettings);
  $('footerSettings').addEventListener('click', openSettings);
  $('reloadSettings').addEventListener('click', loadSettings);
  $('settingsForm').addEventListener('submit', saveSettings);
  $('llmProvider').addEventListener('change', providerFields);
  $('removeGeminiKey').addEventListener('change', () => { $('geminiKey').disabled = $('removeGeminiKey').checked; });
  $('removeOpenaiKey').addEventListener('change', () => { $('openaiKey').disabled = $('removeOpenaiKey').checked; });
  $('reloadSocial').addEventListener('click', loadSocial);
  $('copyYoutube').addEventListener('click', () => copySocial('youtube'));
  $('copyInstagram').addEventListener('click', () => copySocial('instagram'));
  $('confirmDelete').addEventListener('click', deleteProject);
  document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
  $('settingsDialog').addEventListener('close', () => { settingsEpoch += 1; $('geminiKey').value = ''; $('openaiKey').value = ''; });
  $('socialDialog').addEventListener('close', () => { socialAbort?.abort(); socialAbort = null; socialTarget = null; });
  $('deleteDialog').addEventListener('close', () => { deleteTarget = null; });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && view !== 'job') loadProjects(); });
  window.addEventListener('beforeunload', () => { if (blobUrl) URL.revokeObjectURL(blobUrl); });
  syncOptions();
  switchSource('url');
  loadProjects();
  loadHealth();
  const saved = storage('get');
  if (saved) openJob(saved, false);
})();
