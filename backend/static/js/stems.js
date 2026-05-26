import { state } from './state.js';
import * as ui from './ui.js';
import * as api from './api.js';

export function initStems(options) {
    const { syncControls, watchJob, refreshHistory, handleInputChange, switchTab, startAutoMixJob } = options;

    const stemTrackInput = document.getElementById('stem-track');
    const splitStemsBtn = document.getElementById('split-stems-btn');
    const stemDownloadSectionEl = document.getElementById('stem-download-section');
    const stemDownloadLinkEl = document.getElementById('stem-download-link');
    const stemStatusLineEl = document.getElementById('stem-status-line');
    const stemVocalPromptEl = document.getElementById('stem-vocal-prompt');
    const stemVocalPromptTextEl = document.getElementById('stem-vocal-prompt-text');
    const stemVocalActionsEl = document.getElementById('stem-vocal-actions');
    const useStemVocalsBtn = document.getElementById('use-stem-vocals-btn');
    const loadAndAutoMixBtn = document.getElementById('load-and-auto-mix-btn');
    const dismissStemVocalsBtn = document.getElementById('dismiss-stem-vocals-btn');
    const stemAcapellaPreviewCardEl = document.getElementById('stem-acapella-preview-card');
    const stemAcapellaPreviewEl = document.getElementById('stem-acapella-preview');
    const stemAcapellaDownloadLinkEl = document.getElementById('stem-acapella-download-link');
    const stemPreviewGridEl = document.getElementById('stem-preview-grid');

    function resetStemPreviewGrid() {
        stemPreviewGridEl.innerHTML = '';
        stemPreviewGridEl.classList.add('hidden');
    }

    function resetStemDownloadPresentation() {
        state.stemSplitResult = null;
        stemDownloadSectionEl.classList.add('hidden');
        stemDownloadLinkEl.href = '#';
        stemStatusLineEl.textContent = 'Your separated stems are bundled and ready to download.';
        stemVocalPromptTextEl.textContent = 'Do you want the joined vocals loaded into the acapella spot automatically?';
        stemVocalActionsEl.classList.remove('hidden');
        stemVocalPromptEl.classList.add('hidden');
        ui.setAudioPreview(stemAcapellaPreviewCardEl, stemAcapellaPreviewEl, null, null);
        stemAcapellaDownloadLinkEl.href = '#';
        resetStemPreviewGrid();
    }

    function showStemVocalPrompt() {
        if (!state.stemSplitResult?.acapella_download_url) {
            stemVocalPromptEl.classList.add('hidden');
            return;
        }
        stemVocalPromptTextEl.textContent = 'Do you want the joined vocals loaded into the acapella spot automatically?';
        stemVocalActionsEl.classList.remove('hidden');
        stemVocalPromptEl.classList.remove('hidden');
        syncControls();
    }

    function showStemVocalsLoadedMessage(fileName) {
        const beatInput = document.getElementById('beat');
        const nextLine = beatInput.files[0]
            ? `${fileName} was loaded into the acapella spot automatically. You can Auto Mix it with the selected beat now or keep working manually.`
            : `${fileName} was loaded into the acapella spot automatically. You can Auto Mix or use Advanced Analyze with it now.`;
        stemVocalPromptTextEl.textContent = nextLine;
        stemVocalActionsEl.classList.add('hidden');
        stemVocalPromptEl.classList.remove('hidden');
    }

    function renderStemDownloads(stemDownloads) {
        resetStemPreviewGrid();
        if (!Array.isArray(stemDownloads) || stemDownloads.length === 0) return;

        for (const stem of stemDownloads) {
            const card = ui.createPreviewCard(
                ui.formatTitleCase(stem.name),
                `${ui.formatTitleCase(stem.name)} stem preview`,
                stem.preview_url,
                stem.thumbnail_url,
                stem.download_url,
                `Download ${ui.formatTitleCase(stem.name)}`
            );
            stemPreviewGridEl.appendChild(card);
        }
        stemPreviewGridEl.classList.remove('hidden');
    }

    function showStemSplitStatusWithRows(result) {
        stemStatusLineEl.textContent = result.status_line || `${result.source_track_name} was separated into ${ui.formatStemList(result.stems)}.`;
        stemDownloadLinkEl.href = result.download_url;
        stemAcapellaDownloadLinkEl.href = result.acapella_download_url || '#';
        ui.setAudioPreview(
            stemAcapellaPreviewCardEl,
            stemAcapellaPreviewEl,
            result.acapella_preview_url || result.acapella_download_url,
            result.acapella_thumbnail_url
        );
        renderStemDownloads(result.stem_downloads || []);
        stemDownloadSectionEl.classList.remove('hidden');
        renderStemRows();
    }

    async function startStemSplitJob() {
        const trackFile = stemTrackInput.files[0];
        if (!trackFile) {
            ui.showError('Please select one full mix to split into stems.');
            return;
        }
        ui.hideError();
        const downloadSectionEl = document.getElementById('download-section');
        if (downloadSectionEl) downloadSectionEl.classList.add('hidden');
        // Reset download presentation in remix tab via event
        document.dispatchEvent(new CustomEvent('reset-download-presentation'));
        resetStemDownloadPresentation();
        state.isSplittingStems = true;
        syncControls();

        const formData = new FormData();
        formData.append('track', trackFile);

        try {
            const response = await fetch('/split-stems/jobs', { method: 'POST', body: formData });
            if (!response.ok) throw new Error(await api.getErrorMessage(response, 'Stem split failed'));
            const initialJob = await response.json();
            const finalJob = await watchJob(initialJob, 'Stem split');
            state.stemSplitResult = { ...finalJob.result, sourceTrackName: trackFile.name };
            showStemSplitStatusWithRows(state.stemSplitResult);
            showStemVocalPrompt();
            await refreshHistory();
        } catch (err) {
            ui.showError('Error splitting stems: ' + err.message);
        } finally {
            state.isSplittingStems = false;
            syncControls();
        }
    }

    async function loadStemVocalsIntoAcapella(options = {}) {
        const { autoMixAfterLoad = false } = options;
        if (!state.stemSplitResult?.acapella_download_url) return;
        ui.hideError();
        state.isLoadingStemVocals = true;
        syncControls();
        try {
            const response = await fetch(state.stemSplitResult.acapella_download_url);
            if (!response.ok) throw new Error(await api.getErrorMessage(response, 'Could not load separated vocals'));
            const audioBlob = await response.blob();
            const vocalFile = new File([audioBlob], state.stemSplitResult.acapella_file_name || 'separated-acapella.wav', { type: 'audio/wav' });
            const transfer = new DataTransfer();
            transfer.items.add(vocalFile);
            const acapellaInput = document.getElementById('acapella');
            acapellaInput.files = transfer.files;
            handleInputChange(acapellaInput, { preserveStemSplit: true });
            showStemSplitStatusWithRows(state.stemSplitResult);
            showStemVocalsLoadedMessage(vocalFile.name);
        } catch (err) {
            ui.showError('Error loading separated vocals: ' + err.message);
            return;
        } finally {
            state.isLoadingStemVocals = false;
            syncControls();
        }
        if (autoMixAfterLoad) {
            const beatInput = document.getElementById('beat');
            const acapellaInput = document.getElementById('acapella');
            if (beatInput.files[0] && acapellaInput.files[0]) {
                await startAutoMixJob({ preserveStemSplit: true });
            }
        }
    }

    function createStemRouteButton(stem, slot) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'stem-route-btn';
        button.dataset.slot = slot;
        button.dataset.stemName = stem.name;
        button.textContent = slot === 'beat' ? 'Beat' : 'Vocal';
        button.addEventListener('click', async () => {
            await routeStemToSlot(stem, slot, button);
        });
        return button;
    }

    async function routeStemToSlot(stem, slot, button) {
        button.disabled = true;
        try {
            await loadStemToSlot(stem.name, stem.download_url, slot === 'beat' ? 'beat' : 'acapella');
            document.querySelectorAll(`.stem-route-btn[data-slot="${slot}"]`).forEach((btn) => {
                btn.classList.toggle('active', btn === button);
            });
        } catch (err) {
            ui.showError(`Error loading ${ui.formatTitleCase(stem.name)} to ${slot}: ${err.message}`);
        } finally {
            button.disabled = false;
        }
    }

    function renderStemRows() {
        const existingRows = document.querySelectorAll('.stem-row');
        existingRows.forEach((row) => row.remove());

        if (!state.stemSplitResult?.stem_downloads?.length) return;

        const container = document.getElementById('stems-tab');
        const insertBefore = stemDownloadSectionEl;

        for (const stem of state.stemSplitResult.stem_downloads) {
            const row = document.createElement('div');
            row.className = 'stem-row';
            row.dataset.stemName = stem.name;
            row.dataset.stemUrl = stem.download_url;

            const nameEl = document.createElement('span');
            nameEl.className = 'stem-row-name';
            nameEl.textContent = ui.formatTitleCase(stem.name);

            const waveformEl = document.createElement('div');
            waveformEl.className = 'stem-row-waveform';
            if (stem.thumbnail_url) {
                const img = document.createElement('img');
                img.src = stem.thumbnail_url;
                img.alt = '';
                waveformEl.appendChild(img);
            }

            const audio = document.createElement('audio');
            audio.className = 'stem-row-audio';
            audio.controls = true;
            audio.preload = 'none';
            audio.src = stem.preview_url || stem.download_url;

            const muteButton = document.createElement('button');
            muteButton.type = 'button';
            muteButton.className = 'stem-monitor-btn';
            muteButton.textContent = 'Mute';
            muteButton.addEventListener('click', () => {
                audio.muted = !audio.muted;
                muteButton.classList.toggle('active', audio.muted);
            });

            const soloButton = document.createElement('button');
            soloButton.type = 'button';
            soloButton.className = 'stem-monitor-btn';
            soloButton.textContent = 'Solo';
            soloButton.addEventListener('click', () => {
                document.querySelectorAll('.stem-row-audio').forEach((otherAudio) => {
                    otherAudio.muted = otherAudio !== audio;
                });
                document.querySelectorAll('.stem-monitor-btn').forEach((btn) => btn.classList.remove('active'));
                soloButton.classList.add('active');
            });

            const controlsEl = document.createElement('div');
            controlsEl.className = 'stem-row-controls';

            controlsEl.appendChild(createStemRouteButton(stem, 'beat'));
            controlsEl.appendChild(createStemRouteButton(stem, 'vocal'));
            controlsEl.appendChild(muteButton);
            controlsEl.appendChild(soloButton);

            row.appendChild(nameEl);
            row.appendChild(waveformEl);
            row.appendChild(audio);
            row.appendChild(controlsEl);

            if (insertBefore && insertBefore.parentNode === container) {
                container.insertBefore(row, insertBefore);
            } else {
                container.appendChild(row);
            }
        }
    }

    async function loadStemToSlot(stemName, url, slot) {
        try {
            const response = await fetch(url);
            if (!response.ok) throw new Error('Could not load stem');
            const blob = await response.blob();
            const fileName = `${stemName}.wav`;
            const file = new File([blob], fileName, { type: 'audio/wav' });
            const input = slot === 'beat' ? document.getElementById('beat') : document.getElementById('acapella');
            const transfer = new DataTransfer();
            transfer.items.add(file);
            input.files = transfer.files;
            handleInputChange(input, { preserveStemSplit: true });
            switchTab('remix');
        } catch (err) {
            ui.showError(`Error loading stem to ${slot}: ${err.message}`);
        }
    }

    // Event Listeners
    if (splitStemsBtn) splitStemsBtn.addEventListener('click', async () => {
        await startStemSplitJob();
    });
    if (useStemVocalsBtn) useStemVocalsBtn.addEventListener('click', async () => {
        await loadStemVocalsIntoAcapella();
    });
    if (loadAndAutoMixBtn) loadAndAutoMixBtn.addEventListener('click', async () => {
        await loadStemVocalsIntoAcapella({ autoMixAfterLoad: true });
    });
    if (dismissStemVocalsBtn) dismissStemVocalsBtn.addEventListener('click', () => {
        stemVocalPromptEl.classList.add('hidden');
    });

    // Listen for cross-module events
    document.addEventListener('reset-stem-download', () => {
        resetStemDownloadPresentation();
    });

    return { startStemSplitJob, loadStemVocalsIntoAcapella, resetStemDownloadPresentation, showStemSplitStatusWithRows };
}
