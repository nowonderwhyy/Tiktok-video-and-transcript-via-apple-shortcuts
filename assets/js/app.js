const urlInput = document.getElementById('url');
const submitBtn = document.getElementById('submit');
const statusEl = document.getElementById('status');
const transcriptEl = document.getElementById('transcription');
const videoLink = document.getElementById('videoLink');
const copyBtn = document.getElementById('copyBtn');
const spinner = document.getElementById('spinner');
const dropZone = document.getElementById('dropZone');
const recentList = document.getElementById('recentList');
const dots = [document.getElementById('dot1'), document.getElementById('dot2'), document.getElementById('dot3')];

const RECENT_KEY = 'tiktok_dl_recent';
const MAX_RECENT = 8;

function getRecent() {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
  } catch {
    return [];
  }
}

function addRecent(url) {
  const list = getRecent().filter((u) => u !== url);
  list.unshift(url);
  localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, MAX_RECENT)));
  renderRecent();
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function renderRecent() {
  const list = getRecent();
  if (!list.length) {
    recentList.innerHTML = '<div class="recent-empty">No recent URLs yet.</div>';
    return;
  }

  recentList.innerHTML = list
    .map((u) => `<div class="recent-item" role="button" tabindex="0" data-url="${esc(u)}" title="Use this URL">${esc(u)}</div>`)
    .join('');

  recentList.querySelectorAll('.recent-item[data-url]').forEach((el) => {
    const useUrl = () => {
      urlInput.value = el.dataset.url;
      urlInput.focus();
    };
    el.onclick = useUrl;
    el.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        useUrl();
      }
    };
  });
}

function setStatus(text, step) {
  statusEl.textContent = text;
  statusEl.classList.toggle('active', step !== null);

  dots.forEach((d, i) => {
    d.classList.toggle('active', step !== null && i === step && step < 2);
    d.classList.toggle('done', step !== null && (i < step || step === 2));
  });

  spinner.style.display = (text === 'Downloading...' || text === 'Transcribing...') ? 'block' : 'none';
}

dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('dragover');
});

dropZone.addEventListener('dragleave', () => {
  dropZone.classList.remove('dragover');
});

dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  const text = (e.dataTransfer.getData('text/plain') || '').trim();
  if (text && (text.startsWith('http://') || text.startsWith('https://'))) {
    urlInput.value = text;
  }
});

async function submitUrl() {
  const url = urlInput.value.trim();
  if (!url) return;

  submitBtn.disabled = true;

  try {
    const r = await fetch('/set_url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });

    const j = await r.json();
    if (!r.ok) {
      setStatus(j.error || 'Error', null);
      return;
    }

    addRecent(url);
    setStatus('Downloading...', 0);
  } catch (e) {
    setStatus(`Error: ${e.message}`, null);
  } finally {
    submitBtn.disabled = false;
  }
}

submitBtn.onclick = submitUrl;
urlInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    submitUrl();
  }
});

async function poll() {
  try {
    const r = await fetch('/get_transcription');
    if (r.ok) {
      const j = await r.json();
      const txt = j.transcription || '';
      const hasContent = txt && txt.trim() && txt !== '...';

      transcriptEl.textContent = txt || '';
      transcriptEl.classList.toggle('empty', !hasContent);
      transcriptEl.classList.toggle('has-content', hasContent);
      copyBtn.style.display = hasContent ? 'inline-block' : 'none';

      if (j.video_url) {
        videoLink.href = j.video_url;
        videoLink.style.display = 'inline-flex';
      }

      if (hasContent) {
        setStatus('Completed', 2);
      } else if (j.video_url) {
        setStatus('Transcribing...', 1);
      } else if (statusEl.textContent !== 'Idle' && statusEl.textContent !== 'Completed') {
        setStatus('Downloading...', 0);
      }
    }
  } catch (_) {
    // Keep polling even if one request fails.
  }

  setTimeout(poll, 1500);
}

copyBtn.onclick = () => {
  const t = transcriptEl.textContent;
  if (!t) return;

  navigator.clipboard.writeText(t).then(() => {
    copyBtn.textContent = 'Copied!';
    copyBtn.classList.add('copied');
    setTimeout(() => {
      copyBtn.textContent = 'Copy';
      copyBtn.classList.remove('copied');
    }, 2000);
  });
};

async function loadPaths() {
  try {
    const r = await fetch('/paths');
    if (r.ok) {
      const j = await r.json();
      const vEl = document.getElementById('videosPath');
      const aEl = document.getElementById('audioPath');

      vEl.textContent = j.videos || '-';
      vEl.title = j.videos || '';
      aEl.textContent = j.audio || '-';
      aEl.title = j.audio || '';

      document.getElementById('copyVideos').onclick = () => copyPath(j.videos, 'copyVideos');
      document.getElementById('copyAudio').onclick = () => copyPath(j.audio, 'copyAudio');
    }
  } catch (_) {
    // Ignore transient path loading errors.
  }
}

function copyPath(path, btnId) {
  if (!path) return;

  navigator.clipboard.writeText(path).then(() => {
    const b = document.getElementById(btnId);
    b.textContent = 'Copied!';
    b.classList.add('copied');
    setTimeout(() => {
      b.textContent = 'Copy';
      b.classList.remove('copied');
    }, 2000);
  });
}

renderRecent();
loadPaths();
poll();
