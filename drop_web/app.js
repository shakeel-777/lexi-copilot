// Lexi Beam Web & PWA Client
let ws = null;
let deferredPrompt = null;
const transferList = document.getElementById('transfer-list');
const transfersCount = document.getElementById('transfers-count');
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');
const statusPill = document.getElementById('status-pill');
const statusText = document.getElementById('status-text');
const ledDot = document.getElementById('led-dot');
const btnInstall = document.getElementById('btn-install');
const hostName = document.getElementById('host-name');
const networkAddr = document.getElementById('network-addr');
const btnCameraSnap = document.getElementById('btn-camera-snap');
const cameraInput = document.getElementById('camera-input');
const btnSyncClipboard = document.getElementById('btn-sync-clipboard');
const clipboardBanner = document.getElementById('clipboard-banner');
const clipboardPreview = document.getElementById('clipboard-preview');
let latestLaptopClipboard = '';

// 1. Service Worker Registration
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').then((reg) => {
      console.log('[Lexi PWA] Service Worker registered:', reg.scope);
    }).catch((err) => {
      console.warn('[Lexi PWA] SW registration failed:', err);
    });
  });
}

// 2. PWA 1-Click Desktop Install Support (Windows / Chrome / Edge)
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  if (btnInstall) {
    btnInstall.style.display = 'inline-flex';
    btnInstall.addEventListener('click', async () => {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      const { outcome } = await deferredPrompt.userChoice;
      console.log('[Lexi PWA] User install choice:', outcome);
      deferredPrompt = null;
      btnInstall.style.display = 'none';
    });
  }
});

// Hide install button if app is already running as standalone PWA
window.addEventListener('DOMContentLoaded', () => {
  if (window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true) {
    if (btnInstall) btnInstall.style.display = 'none';
  }
  connectWebSocket();
  loadActiveShares();
});

// Fetch and render existing 24-hour hosted files on load
async function loadActiveShares() {
  try {
    const resp = await fetch('/api/shares');
    if (resp.ok) {
      const data = await resp.json();
      if (data.shares && Array.isArray(data.shares)) {
        data.shares.forEach(renderIncomingTransferCard);
      }
    }
  } catch (e) {
    console.log('[Lexi PWA] Offline or active shares unavailable:', e);
  }
}

// Format byte sizes
function formatSize(bytes) {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// 3. WebSocket Realtime Connection
function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    ledDot.classList.add('online');
    statusText.innerText = 'Connected';
    if (networkAddr) networkAddr.innerText = `${window.location.host} · Ready`;

    // Announce device profile
    const deviceType = /Windows/i.test(navigator.userAgent) ? 'Windows PC' :
                       /Macintosh|Mac OS/i.test(navigator.userAgent) ? 'MacBook' :
                       /Android/i.test(navigator.userAgent) ? 'Android' :
                       /iPhone|iPad/i.test(navigator.userAgent) ? 'iOS Device' : 'Desktop';
    ws.send(JSON.stringify({
      type: 'handshake',
      device: deviceType,
      ua: navigator.userAgent
    }));
  };

  ws.onclose = () => {
    ledDot.classList.remove('online');
    statusText.innerText = 'Disconnected';
    setTimeout(connectWebSocket, 2000);
  };

  ws.onerror = (err) => {
    console.warn('[WS] Error:', err);
    ws.close();
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleServerMessage(msg);
    } catch (e) {
      console.error('[WS] Parse error:', e);
    }
  };
}

function handleServerMessage(msg) {
  if (msg.type === 'incoming_file') {
    // Linux host is sending a file to this client!
    renderIncomingTransferCard(msg.file);
  } else if (msg.type === 'active_shares_sync') {
    if (msg.shares && Array.isArray(msg.shares)) {
      msg.shares.forEach(renderIncomingTransferCard);
    }
  } else if (msg.type === 'peer_update') {
    if (msg.host_name && hostName) {
      hostName.innerText = `💻 ${msg.host_name}`;
    }
  } else if (msg.type === 'clipboard_sync') {
    if (msg.text && msg.source !== 'mobile') {
      latestLaptopClipboard = msg.text;
      if (clipboardBanner && clipboardPreview) {
        clipboardBanner.style.display = 'flex';
        clipboardPreview.innerText = msg.text.replace(/\n/g, ' ');
      }
    }
  } else if (msg.type === 'request_camera_snap') {
    if (cameraInput) {
      cameraInput.click();
    }
  }
}

// Continuity Controls (Camera Snap & Universal Clipboard)
if (btnCameraSnap && cameraInput) {
  btnCameraSnap.addEventListener('click', () => {
    cameraInput.click();
  });

  cameraInput.addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;

    const cardId = 'snap-' + Date.now();
    const card = document.createElement('div');
    card.className = 'transfer-card';
    card.id = cardId;
    card.innerHTML = `
      <div class="file-icon">📸</div>
      <div class="transfer-info">
        <div class="transfer-header">
          <span class="file-name">Continuity Snap</span>
          <span class="transfer-status uploading" id="${cardId}-status">Beaming to Lexi Vision...</span>
        </div>
        <div class="transfer-meta">
          <span>${formatSize(file.size)} · Camera Capture</span>
        </div>
        <div class="progress-bar-bg">
          <div class="progress-bar-fill" id="${cardId}-prog" style="width: 50%;"></div>
        </div>
      </div>
    `;
    transferList.prepend(card);
    updateTransfersCount();

    try {
      const formData = new FormData();
      formData.append('file', file, file.name || 'camera_snap.jpg');
      const resp = await fetch('/api/camera-snap', {
        method: 'POST',
        body: formData
      });
      const res = await resp.json();
      if (res.success) {
        document.getElementById(`${cardId}-status`).innerText = '✓ Attached to Lexi Vision Tray!';
        document.getElementById(`${cardId}-status`).className = 'transfer-status completed';
        document.getElementById(`${cardId}-prog`).style.width = '100%';
        playReceiveChime();
      } else {
        throw new Error(res.error || 'Upload failed');
      }
    } catch (err) {
      document.getElementById(`${cardId}-status`).innerText = '⚠️ Failed to send snap';
      document.getElementById(`${cardId}-status`).className = 'transfer-status error';
    }
    cameraInput.value = '';
  });
}

if (btnSyncClipboard) {
  btnSyncClipboard.addEventListener('click', async () => {
    let textToSend = '';
    if (navigator.clipboard && navigator.clipboard.readText) {
      try {
        textToSend = await navigator.clipboard.readText();
      } catch (e) {
        // Fallback to prompt if clipboard read permission denied
      }
    }
    if (!textToSend) {
      textToSend = prompt('Paste or enter text to send to Linux laptop:');
    }
    if (textToSend && textToSend.trim()) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: 'clipboard_sync',
          text: textToSend.trim()
        }));
      } else {
        await fetch('/api/clipboard', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: textToSend.trim() })
        });
      }
      btnSyncClipboard.innerHTML = '<span>✓</span> <span>Sent to Laptop!</span>';
      setTimeout(() => {
        btnSyncClipboard.innerHTML = '<span>📋</span> <span>Send Clipboard</span>';
      }, 2000);
    }
  });
}

if (clipboardBanner) {
  clipboardBanner.addEventListener('click', async () => {
    if (latestLaptopClipboard) {
      try {
        await navigator.clipboard.writeText(latestLaptopClipboard);
        const originalText = clipboardPreview.innerText;
        clipboardPreview.innerText = '✓ Copied to Phone Clipboard!';
        setTimeout(() => {
          clipboardPreview.innerText = originalText;
        }, 2000);
      } catch (e) {
        prompt('Copy text from laptop:', latestLaptopClipboard);
      }
    }
  });
}

// Check action parameter on PWA launch (e.g. from app shortcuts or share sheet)
window.addEventListener('DOMContentLoaded', () => {
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('action') === 'snap' && cameraInput) {
    setTimeout(() => cameraInput.click(), 500);
  } else if (urlParams.get('shared') === 'success') {
    const card = document.createElement('div');
    card.className = 'transfer-card';
    card.innerHTML = `
      <div class="file-icon">⚡</div>
      <div class="transfer-info">
        <div class="transfer-header">
          <span class="file-name">Share Sheet Drop</span>
          <span class="transfer-status completed">✓ Beamed to Linux Downloads!</span>
        </div>
      </div>
    `;
    transferList.prepend(card);
    updateTransfersCount();
  }
});

// 4. Drag and Drop Setup
dropzone.addEventListener('click', () => fileInput.click());

['dragenter', 'dragover'].forEach(eventName => {
  dropzone.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropzone.classList.add('dragover');
  });
});

['dragleave', 'drop'].forEach(eventName => {
  dropzone.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropzone.classList.remove('dragover');
  });
});

dropzone.addEventListener('drop', (e) => {
  const dt = e.dataTransfer;
  const files = dt.files;
  if (files && files.length > 0) {
    handleFilesSelected(files);
  }
});

fileInput.addEventListener('change', (e) => {
  if (e.target.files && e.target.files.length > 0) {
    handleFilesSelected(e.target.files);
    e.target.value = '';
  }
});

function handleFilesSelected(files) {
  Array.from(files).forEach(file => {
    uploadFile(file);
  });
}

// 5. High-Speed Upload with Wire-Speed Tracking
function uploadFile(file) {
  const cardId = 'upload_' + Math.random().toString(36).substring(2, 9);
  const card = document.createElement('div');
  card.className = 'transfer-card';
  card.id = cardId;

  card.innerHTML = `
    <div class="transfer-header">
      <div class="file-info">
        <span class="file-glyph">📦</span>
        <div class="file-meta">
          <span class="file-name" title="${file.name}">${file.name}</span>
          <span class="file-size">${formatSize(file.size)}</span>
        </div>
      </div>
      <div class="transfer-stats">
        <span class="speed-tag" id="${cardId}_speed">0.0 MB/s</span>
        <span class="eta-tag" id="${cardId}_eta">Starting...</span>
      </div>
    </div>
    <div class="progress-container">
      <div class="progress-bar" id="${cardId}_bar"></div>
    </div>
  `;

  transferList.prepend(card);
  updateTransfersCount();

  const xhr = new XMLHttpRequest();
  const startTime = Date.now();
  let lastBytes = 0;
  let lastTime = Date.now();

  xhr.upload.addEventListener('progress', (e) => {
    if (e.lengthComputable) {
      const now = Date.now();
      const dt = (now - lastTime) / 1000;
      const pct = Math.round((e.loaded / e.total) * 100);
      
      const bar = document.getElementById(`${cardId}_bar`);
      const speedTag = document.getElementById(`${cardId}_speed`);
      const etaTag = document.getElementById(`${cardId}_eta`);

      if (bar) bar.style.width = pct + '%';

      if (dt > 0.3) {
        const bytesDiff = e.loaded - lastBytes;
        const speedBps = bytesDiff / dt;
        const speedMbps = speedBps / (1024 * 1024);
        if (speedTag) speedTag.innerText = `${speedMbps.toFixed(1)} MB/s`;

        const remainingBytes = e.total - e.loaded;
        const etaSec = speedBps > 0 ? Math.ceil(remainingBytes / speedBps) : 0;
        if (etaTag) etaTag.innerText = etaSec > 0 ? `ETA: ${etaSec}s` : 'Finishing...';

        lastBytes = e.loaded;
        lastTime = now;
      }
    }
  });

  xhr.addEventListener('load', () => {
    const bar = document.getElementById(`${cardId}_bar`);
    const speedTag = document.getElementById(`${cardId}_speed`);
    const etaTag = document.getElementById(`${cardId}_eta`);
    if (xhr.status >= 200 && xhr.status < 300) {
      if (bar) {
        bar.style.width = '100%';
        bar.style.background = 'var(--accent-emerald)';
      }
      const totalDur = (Date.now() - startTime) / 1000;
      const avgSpeed = (file.size / (1024 * 1024)) / Math.max(totalDur, 0.05);
      if (speedTag) speedTag.innerText = `${avgSpeed.toFixed(1)} MB/s`;
      if (etaTag) etaTag.innerText = '✨ Received on Linux';
    } else {
      if (etaTag) {
        etaTag.innerText = '⚠️ Upload failed';
        etaTag.style.color = 'var(--danger)';
      }
    }
  });

  xhr.addEventListener('error', () => {
    const etaTag = document.getElementById(`${cardId}_eta`);
    if (etaTag) {
      etaTag.innerText = '⚠️ Network error';
      etaTag.style.color = 'var(--danger)';
    }
  });

  const uploadUrl = `/api/upload?filename=${encodeURIComponent(file.name)}`;
  xhr.open('POST', uploadUrl, true);
  xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
  xhr.send(file);
}

// 6. Render Incoming File from Linux Host
function renderIncomingTransferCard(file) {
  const cardId = 'recv_' + file.token;
  if (document.getElementById(cardId)) return;

  const card = document.createElement('div');
  card.className = 'transfer-card';
  card.id = cardId;

  const isZip = file.is_zip || (file.name && file.name.toLowerCase().endsWith('.zip'));
  const fileGlyph = isZip ? '📦' : '⚡';
  const downloadHref = file.named_url || `/download/${encodeURIComponent(file.name)}` || `/api/download/${file.token}`;
  const hostBadge = file.pin_all_day !== false ? ' · 🕒 24h Hosted (Static Port 7890)' : '';

  card.innerHTML = `
    <div class="transfer-header">
      <div class="file-info">
        <span class="file-glyph">${fileGlyph}</span>
        <div class="file-meta">
          <span class="file-name" title="${file.name}">${file.name}</span>
          <span class="file-size">${formatSize(file.size)} · 🟢 Direct Local Wi-Fi${hostBadge}</span>
        </div>
      </div>
      <div class="transfer-stats">
        <a href="${downloadHref}" class="btn-download-action" download="${file.name}">
          ${isZip ? '📦 Download Zip' : '⚡ Save to Device'}
        </a>
      </div>
    </div>
    <div class="progress-container">
      <div class="progress-bar" id="${cardId}_bar" style="width: 100%; background: var(--accent-emerald);"></div>
    </div>
  `;

  transferList.prepend(card);
  updateTransfersCount();

  // Trigger sound chime
  playReceiveChime();
}

function updateTransfersCount() {
  const count = transferList.children.length;
  if (transfersCount) {
    transfersCount.innerText = `${count} ${count === 1 ? 'file' : 'files'}`;
  }
}

function playReceiveChime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
    osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.15); // A5
    gain.gain.setValueAtTime(0.1, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.3);
  } catch (e) {}
}
