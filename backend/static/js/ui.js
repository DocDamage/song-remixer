export function formatNumber(value, digits) {
    return Number.isFinite(value) ? value.toFixed(digits) : '--';
}

export function formatPitchShift(value) {
    if (!Number.isFinite(value)) return '--';
    if (value === 0) return '0 st';
    const digits = Number.isInteger(value) ? 0 : 2;
    const formatted = value.toFixed(digits);
    return `${value > 0 ? '+' : ''}${formatted} st`;
}

export function formatTempoRatio(value) {
    return Number.isFinite(value) ? `${value.toFixed(3)}x` : '--';
}

export function formatFileSize(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 KB';
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function formatTitleCase(value) {
    return value.split('-').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

export function formatConfidence(value) {
    if (!Number.isFinite(value)) return 'unknown';
    if (value >= 0.75) return 'high';
    if (value >= 0.45) return 'medium';
    return 'low';
}

export function formatStemList(stems) {
    if (!Array.isArray(stems) || stems.length === 0) return 'stems';
    if (stems.length === 1) return stems[0];
    if (stems.length === 2) return `${stems[0]} and ${stems[1]}`;
    return `${stems.slice(0, -1).join(', ')}, and ${stems[stems.length - 1]}`;
}

export function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

export function isSupportedAudioFile(file) {
    return Boolean(file) && (file.type.startsWith('audio/') || /\.(mp3|wav|flac|m4a|aac|ogg|aif|aiff|wma)$/i.test(file.name));
}

export function hideError() {
    const el = document.getElementById('error');
    if (!el) return;
    el.classList.add('hidden');
    el.textContent = '';
}

export function showError(msg) {
    const el = document.getElementById('error');
    if (!el) return;
    el.textContent = msg;
    el.classList.remove('hidden');
}

export function setHidden(el, hidden) {
    if (el) el.classList.toggle('hidden', hidden);
}

export function updateProcessingProgress(progress) {
    const processingProgressShellEl = document.getElementById('processing-progress-shell');
    const processingProgressBarEl = document.getElementById('processing-progress-bar');
    const processingProgressValueEl = document.getElementById('processing-progress-value');
    const safeProgress = Number.isFinite(progress) ? Math.max(0, Math.min(100, Math.round(progress))) : 0;
    if (processingProgressShellEl) processingProgressShellEl.classList.remove('hidden');
    if (processingProgressBarEl) processingProgressBarEl.style.width = `${safeProgress}%`;
    if (processingProgressValueEl) processingProgressValueEl.textContent = `${safeProgress}%`;
}

export function showProcessing(message, progress = 0) {
    const processingMessageEl = document.getElementById('processing-message');
    const processingEl = document.getElementById('processing');
    if (processingMessageEl) processingMessageEl.textContent = message;
    updateProcessingProgress(progress);
    if (processingEl) processingEl.classList.remove('hidden');
}

export function hideProcessing() {
    const processingEl = document.getElementById('processing');
    const processingMessageEl = document.getElementById('processing-message');
    const processingProgressShellEl = document.getElementById('processing-progress-shell');
    const processingProgressBarEl = document.getElementById('processing-progress-bar');
    const processingProgressValueEl = document.getElementById('processing-progress-value');
    if (processingEl) processingEl.classList.add('hidden');
    if (processingMessageEl) processingMessageEl.textContent = 'Processing... this may take a moment.';
    if (processingProgressShellEl) processingProgressShellEl.classList.add('hidden');
    if (processingProgressBarEl) processingProgressBarEl.style.width = '0%';
    if (processingProgressValueEl) processingProgressValueEl.textContent = '0%';
}

export function clearWaveformThumbnail(cardEl) {
    const existingThumbnail = cardEl.querySelector('.waveform-thumbnail');
    if (existingThumbnail) existingThumbnail.remove();
}

export function ensureWaveformThumbnail(cardEl) {
    let image = cardEl.querySelector('.waveform-thumbnail');
    if (image) return image;
    image = document.createElement('img');
    image.className = 'waveform-thumbnail';
    image.alt = '';
    image.decoding = 'async';
    image.loading = 'lazy';
    const audioEl = cardEl.querySelector('audio');
    if (audioEl) {
        cardEl.insertBefore(image, audioEl);
    } else {
        cardEl.appendChild(image);
    }
    return image;
}

export function resolveWaveformThumbnailUrl(previewUrl, thumbnailUrl) {
    if (thumbnailUrl) return thumbnailUrl;
    if (!previewUrl || !previewUrl.startsWith('/download/')) return null;
    return `/waveform/${previewUrl.slice('/download/'.length)}`;
}

export function setWaveformThumbnail(cardEl, previewUrl, thumbnailUrl) {
    const resolvedUrl = resolveWaveformThumbnailUrl(previewUrl, thumbnailUrl);
    if (!resolvedUrl) {
        clearWaveformThumbnail(cardEl);
        return;
    }
    const image = ensureWaveformThumbnail(cardEl);
    image.src = resolvedUrl;
}

export function setAudioPreview(cardEl, audioEl, url, thumbnailUrl) {
    if (!url) {
        clearWaveformThumbnail(cardEl);
        audioEl.removeAttribute('src');
        audioEl.load();
        cardEl.classList.add('hidden');
        return;
    }
    audioEl.src = url;
    cardEl.classList.remove('hidden');
    setWaveformThumbnail(cardEl, url, thumbnailUrl);
}

export function createPreviewCard(title, description, previewUrl, thumbnailUrl, downloadUrl, downloadLabel) {
    const card = document.createElement('div');
    card.className = 'stem-preview-card';

    const heading = document.createElement('h3');
    heading.textContent = title;
    card.appendChild(heading);

    if (description) {
        const descriptionEl = document.createElement('p');
        descriptionEl.textContent = description;
        card.appendChild(descriptionEl);
    }

    if (previewUrl) {
        const audio = document.createElement('audio');
        audio.className = 'audio-preview';
        audio.controls = true;
        audio.preload = 'none';
        audio.src = previewUrl;
        card.appendChild(audio);
        setWaveformThumbnail(card, previewUrl, thumbnailUrl);
    }

    if (downloadUrl) {
        const linkRow = document.createElement('div');
        linkRow.className = 'preview-link-row';
        const link = document.createElement('a');
        link.className = 'text-link';
        link.href = downloadUrl;
        link.textContent = downloadLabel;
        linkRow.appendChild(link);
        card.appendChild(linkRow);
    }

    return card;
}
