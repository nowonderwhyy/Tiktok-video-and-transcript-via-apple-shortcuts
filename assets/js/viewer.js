function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

async function init() {
  const r = await fetch(`/api/files/${FOLDER}`);

  if (!r.ok) return;

  const j = await r.json();
  const files = j.files || [];

  if (!files.length) {
    document.getElementById('emptyState').style.display = 'block';
    return;
  }

  const grid = document.getElementById('fileGrid');
  grid.style.display = 'grid';

  const vidExt = ['mp4', 'webm', 'mov', 'gif'];
  const audExt = ['mp3', 'm4a', 'wav', 'ogg'];

  const cards = files
    .map((f) => {
      const ext = (f.name.split('.').pop() || '').toLowerCase();
      const isVid = vidExt.includes(ext);
      const isAud = audExt.includes(ext);

      let preview = '<div class="file-icon">file</div>';
      if (isVid) preview = `<video controls preload="metadata" src="${esc(f.url)}"></video>`;
      if (isAud) preview = `<audio controls preload="metadata" src="${esc(f.url)}"></audio>`;

      return `
        <article class="file-card">
          <div class="file-card-header"><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.name)}</a></div>
          <div class="file-card-preview">${preview}</div>
        </article>
      `;
    })
    .join('');

  grid.innerHTML = cards;
}

init();
