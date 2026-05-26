import { state, jobs } from './js/state.js';
import * as ui from './js/ui.js';
import * as api from './js/api.js';
import { initFileBrowser } from './js/file-browser.js';
import { initRemix } from './js/remix.js';
import { initStems } from './js/stems.js';

// DOM refs
const beatInput = document.getElementById('beat');
const acapellaInput = document.getElementById('acapella');
const stemTrackInput = document.getElementById('stem-track');
const mixStyleSelect = document.getElementById('mix-style');
const autoMixBtn = document.getElementById('auto-mix-btn');
const analyzeBtn = document.getElementById('analyze-btn');
const advancedMixBtn = document.getElementById('advanced-mix-btn');
const modalSyncMixBtn = document.getElementById('modal-sync-mix-btn');
const splitStemsBtn = document.getElementById('split-stems-btn');
const processBtn = document.getElementById('process-btn');
const nudgeInput = document.getElementById('nudge');
const errorEl = document.getElementById('error');
const analysisResultsEl = document.getElementById('analysis-results');
const processingEl = document.getElementById('processing');
const downloadSectionEl = document.getElementById('download-section');
const downloadLinkEl = document.getElementById('download-link');
const autoMixStatusEl = document.getElementById('auto-mix-status');
const autoMixStatusLineEl = document.getElementById('auto-mix-status-line');
const stemDownloadSectionEl = document.getElementById('stem-download-section');
const stemDownloadLinkEl = document.getElementById('stem-download-link');
const stemStatusLineEl = document.getElementById('stem-status-line');
const stemVocalPromptEl = document.getElementById('stem-vocal-prompt');
const stemVocalPromptTextEl = document.getElementById('stem-vocal-prompt-text');
const stemVocalActionsEl = document.getElementById('stem-vocal-actions');
const useStemVocalsBtn = document.getElementById('use-stem-vocals-btn');
const loadAndAutoMixBtn = document.getElementById('load-and-auto-mix-btn');
const dismissStemVocalsBtn = document.getElementById('dismiss-stem-vocals-btn');
const historySectionEl = document.getElementById('history-section');
const historyGridEl = document.getElementById('history-grid');
const dropZones = Array.from(document.querySelectorAll('.drop-zone'));
const tabBtns = Array.from(document.querySelectorAll('.tab-btn'));
const tabPanels = Array.from(document.querySelectorAll('.tab-panel'));
const restoredSessionBannerEl = document.getElementById('restored-session-banner');
const clearRestoredSessionBtn = document.getElementById('clear-restored-session-btn');
const jobTrayEl = document.getElementById('job-tray');
const jobTrayListEl = document.getElementById('job-tray-list');
const jobTrayClearBtn = document.getElementById('job-tray-clear-btn');
const sidebarBeatName = document.getElementById('sidebar-beat-name');
const sidebarAcapellaName = document.getElementById('sidebar-acapella-name');
const sidebarStemName = document.getElementById('sidebar-stem-name');
const fileTreeBeat = document.getElementById('file-tree-beat');
const fileTreeAcapella = document.getElementById('file-tree-acapella');
const fileTreeStem = document.getElementById('file-tree-stem');

function getFileMetaEl(input) {
    return document.querySelector(`[data-file-meta-for="${input.id}"]`);
}

function getTrackLabel(input) {
    return input.dataset.trackLabel || 'track';
}

function updateSidebarFile(input, file) {
    if (input.id === 'beat') {
        sidebarBeatName.textContent = file ? file.name : '—';
        fileTreeBeat.classList.toggle('has-file', !!file);
    } else if (input.id === 'acapella') {
        sidebarAcapellaName.textContent = file ? file.name : '—';
        fileTreeAcapella.classList.toggle('has-file', !!file);
    } else if (input.id === 'stem-track') {
        sidebarStemName.textContent = file ? file.name : '—';
        fileTreeStem.classList.toggle('has-file', !!file);
    }
}

function updateFilePreview(input) {
    const fileMetaEl = getFileMetaEl(input);
    if (!fileMetaEl) return;
    const file = input.files[0];
    updateSidebarFile(input, file);
    if (!file) {
        fileMetaEl.textContent = 'No file selected';
        fileMetaEl.classList.remove('has-file');
        syncControls();
        return;
    }
    fileMetaEl.textContent = `${file.name} (${ui.formatFileSize(file.size)})`;
    fileMetaEl.classList.add('has-file');
    syncControls();
}

function setRestoredFileMeta(input, sourceName) {
    const fileMetaEl = getFileMetaEl(input);
    if (!fileMetaEl || !sourceName) return;
    fileMetaEl.textContent = `${sourceName} (restored on server)`;
    fileMetaEl.classList.add('has-file');
    updateSidebarFile(input, { name: sourceName });
}

function assignDroppedFile(input, file) {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
}

function resetAnalysisState(options = {}) {
    const { preserveStemSplit = true } = options;
    state.analysisData = null;
    analysisResultsEl.classList.add('hidden');
    downloadSectionEl.classList.add('hidden');
    ui.hideProcessing();
    downloadLinkEl.href = '#';
    document.dispatchEvent(new CustomEvent('reset-download-presentation'));
    if (!preserveStemSplit) {
        document.dispatchEvent(new CustomEvent('reset-stem-download'));
    }
    nudgeInput.value = '0';
    const nudgeVal = document.getElementById('nudge-val');
    if (nudgeVal) nudgeVal.textContent = '0';
    const tempoRatioEl = document.getElementById('tempo-ratio');
    if (tempoRatioEl) tempoRatioEl.textContent = '--';
    const pitchShiftEl = document.getElementById('pitch-shift');
    if (pitchShiftEl) pitchShiftEl.textContent = '--';
    syncControls();
}

function syncControls() {
    const isBusy = state.isAnalyzing || state.isProcessing || state.isAutoMixing || state.isSplittingStems || state.isLoadingStemVocals;
    const canAutoMixAfterStemLoad = Boolean(state.stemSplitResult?.acapella_download_url) && Boolean(beatInput.files[0]);

    if (autoMixBtn) autoMixBtn.textContent = state.isAutoMixing ? 'Auto Mixing...' : 'Auto Mix';
    if (analyzeBtn) analyzeBtn.textContent = state.isAnalyzing ? 'Analyzing...' : 'Analyze Tracks';
    if (splitStemsBtn) splitStemsBtn.textContent = state.isSplittingStems ? 'Splitting Stems...' : 'Split into Stems';
    if (processBtn) processBtn.textContent = state.isProcessing ? 'Mixing...' : 'Sync & Mix';
    if (modalSyncMixBtn) modalSyncMixBtn.textContent = state.isProcessing ? 'Mixing...' : 'Sync & Mix';
    if (useStemVocalsBtn) useStemVocalsBtn.textContent = state.isLoadingStemVocals ? 'Loading Vocals...' : 'Yes, load vocals';
    if (loadAndAutoMixBtn) loadAndAutoMixBtn.textContent = state.isAutoMixing ? 'Auto Mixing...' : 'Load vocals + Auto Mix';

    if (autoMixBtn) autoMixBtn.disabled = isBusy;
    if (analyzeBtn) analyzeBtn.disabled = isBusy;
    if (splitStemsBtn) splitStemsBtn.disabled = isBusy;
    if (processBtn) processBtn.disabled = isBusy || !state.analysisData;
    if (modalSyncMixBtn) modalSyncMixBtn.disabled = isBusy || !state.analysisData;
    if (nudgeInput) nudgeInput.disabled = isBusy || !state.analysisData;
    if (beatInput) beatInput.disabled = isBusy;
    if (acapellaInput) acapellaInput.disabled = isBusy;
    if (stemTrackInput) stemTrackInput.disabled = isBusy;
    if (mixStyleSelect) mixStyleSelect.disabled = isBusy;
    if (useStemVocalsBtn) useStemVocalsBtn.disabled = isBusy || !state.stemSplitResult?.acapella_download_url;
    if (loadAndAutoMixBtn) loadAndAutoMixBtn.disabled = isBusy || !canAutoMixAfterStemLoad;
    if (dismissStemVocalsBtn) dismissStemVocalsBtn.disabled = isBusy;
    if (loadAndAutoMixBtn) loadAndAutoMixBtn.classList.toggle('hidden', !canAutoMixAfterStemLoad);

    for (const zone of dropZones) {
        zone.classList.toggle('is-disabled', isBusy);
    }
}

function handleInputChange(input, options = {}) {
    const file = input.files[0];
    const preserveStemSplit = options.preserveStemSplit ?? input !== stemTrackInput;
    ui.hideError();
    resetAnalysisState({ preserveStemSplit });
    if (!file) {
        updateFilePreview(input);
        return;
    }
    if (!ui.isSupportedAudioFile(file)) {
        input.value = '';
        updateFilePreview(input);
        ui.showError(`Please choose an audio file for the ${getTrackLabel(input).toLowerCase()}.`);
        return;
    }
    updateFilePreview(input);
}

function switchTab(tabName) {
    for (const btn of tabBtns) {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    }
    for (const panel of tabPanels) {
        panel.classList.toggle('active', panel.id === `${tabName}-tab`);
    }
}

function upsertJobTrayCard(job, label) {
    jobs.activeJobs.set(job.job_id, { job, label });
    renderJobTray();
}

function renderJobTray() {
    jobTrayListEl.innerHTML = '';
    const entries = Array.from(jobs.activeJobs.values());
    jobTrayEl.classList.toggle('hidden', entries.length === 0);
    for (const entry of entries) {
        const card = document.createElement('div');
        card.className = 'job-tray-card';
        card.dataset.jobId = entry.job.job_id;
        card.innerHTML = `
            <div class="job-tray-title">
                <span>${ui.escapeHtml(entry.label)}</span>
                <span>${ui.escapeHtml(entry.job.status)}</span>
            </div>
            <div class="progress-track">
                <div class="progress-bar" style="width:${Math.max(0, Math.min(100, entry.job.progress || 0))}%"></div>
            </div>
            <p class="helper-text">${ui.escapeHtml(entry.job.message || '')}</p>
        `;
        jobTrayListEl.appendChild(card);
    }
}

async function watchJob(initialJob, fallbackLabel) {
    let job = initialJob;
    upsertJobTrayCard(job, fallbackLabel);
    while (job.status === 'queued' || job.status === 'running') {
        upsertJobTrayCard(job, fallbackLabel);
        await api.wait(900);
        const response = await fetch(job.status_url);
        if (!response.ok) throw new Error(await api.getErrorMessage(response, `${fallbackLabel} status check failed`));
        job = await response.json();
    }
    upsertJobTrayCard(job, fallbackLabel);
    if (job.status === 'completed') {
        return job;
    }
    throw new Error(job.error || job.message || `${fallbackLabel} failed`);
}

async function refreshHistory() {
    try {
        const response = await fetch('/history');
        if (!response.ok) return;
        const payload = await response.json();
        renderHistory(payload.items || []);
    } catch (_error) {}
}

async function restoreLatestAnalysis() {
    try {
        const response = await fetch('/analysis/latest');
        if (!response.ok) return;
        const payload = await response.json();
        remixApi.applyAnalysisState(payload, { restored: true });
    } catch (_error) {}
}

function renderHistory(items) {
    historyGridEl.innerHTML = '';
    if (!Array.isArray(items) || items.length === 0) {
        historySectionEl.classList.add('hidden');
        return;
    }
    for (const item of items) {
        const result = item.result || {};
        const title = item.kind === 'auto-mix' ? 'Auto Mix ready' : 'Stem bundle ready';
        const previewUrl = result.preview_url || result.acapella_preview_url;
        const thumbnailUrl = result.thumbnail_url || result.acapella_thumbnail_url;
        const card = ui.createPreviewCard(
            title,
            result.status_line || item.message || 'Recent export',
            previewUrl,
            thumbnailUrl,
            result.download_url,
            item.kind === 'auto-mix' ? 'Download Remix' : 'Download Stem ZIP'
        );
        card.classList.remove('stem-preview-card');
        card.classList.add('history-card');
        if (result.acapella_download_url && item.kind === 'split-stems') {
            const linkRow = document.createElement('div');
            linkRow.className = 'preview-link-row';
            const acapellaLink = document.createElement('a');
            acapellaLink.className = 'text-link';
            acapellaLink.href = result.acapella_download_url;
            acapellaLink.textContent = 'Download prepared acapella';
            linkRow.appendChild(acapellaLink);
            card.appendChild(linkRow);
        }
        historyGridEl.appendChild(card);
    }
    historySectionEl.classList.remove('hidden');
}

// Initialize modules
const remixApi = initRemix({ syncControls, watchJob, refreshHistory, setRestoredFileMeta });
const stemsApi = initStems({ syncControls, watchJob, refreshHistory, handleInputChange, switchTab, startAutoMixJob: remixApi.startAutoMixJob });
initFileBrowser({ switchTab, assignDroppedFile });

// Event Listeners
for (const input of [beatInput, acapellaInput, stemTrackInput]) {
    if (input) {
        input.addEventListener('change', () => handleInputChange(input));
        updateFilePreview(input);
    }
}

for (const zone of dropZones) {
    const input = document.getElementById(zone.dataset.inputId);
    if (!input) continue;
    zone.addEventListener('dragenter', (event) => {
        if (input.disabled) return;
        event.preventDefault();
        zone.classList.add('is-dragging');
    });
    zone.addEventListener('dragover', (event) => {
        if (input.disabled) return;
        event.preventDefault();
        zone.classList.add('is-dragging');
    });
    zone.addEventListener('dragleave', () => {
        zone.classList.remove('is-dragging');
    });
    zone.addEventListener('drop', (event) => {
        if (input.disabled) return;
        event.preventDefault();
        zone.classList.remove('is-dragging');
        const files = Array.from(event.dataTransfer?.files || []);
        if (files.length === 0) return;
        if (files.length > 1) {
            ui.showError(`Please drop one file for the ${getTrackLabel(input).toLowerCase()}.`);
            return;
        }
        if (!ui.isSupportedAudioFile(files[0])) {
            ui.showError(`Please choose an audio file for the ${getTrackLabel(input).toLowerCase()}.`);
            return;
        }
        assignDroppedFile(input, files[0]);
    });
}

for (const btn of tabBtns) {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
}

if (clearRestoredSessionBtn) {
    clearRestoredSessionBtn.addEventListener('click', async () => {
        try {
            const resp = await fetch('/analysis/latest', { method: 'DELETE' });
            if (resp.ok) {
                remixApi.showRestoredSessionBanner(false);
                resetAnalysisState();
            } else {
                ui.showError('Could not clear restored session.');
            }
        } catch (err) {
            ui.showError('Could not clear restored session.');
        }
    });
}

if (jobTrayClearBtn) {
    jobTrayClearBtn.addEventListener('click', () => {
        for (const [jobId, entry] of jobs.activeJobs.entries()) {
            if (entry.job.status === 'completed' || entry.job.status === 'failed') {
                jobs.activeJobs.delete(jobId);
            }
        }
        renderJobTray();
    });
}

// Boot
restoreLatestAnalysis();
refreshHistory();
syncControls();
