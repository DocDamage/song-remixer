import { state, MIX_STYLE_DESCRIPTIONS } from './state.js';
import * as ui from './ui.js';
import * as api from './api.js';

export function initRemix(options) {
    const { syncControls, watchJob, refreshHistory, setRestoredFileMeta } = options;

    const beatInput = document.getElementById('beat');
    const acapellaInput = document.getElementById('acapella');
    const mixStyleSelect = document.getElementById('mix-style');
    const mixStyleDescriptionEl = document.getElementById('mix-style-description');
    const finalTempoRatioInput = document.getElementById('final-tempo-ratio');
    const finalTempoValueEl = document.getElementById('final-tempo-value');
    const autoMixBtn = document.getElementById('auto-mix-btn');
    const analyzeBtn = document.getElementById('analyze-btn');
    const advancedMixBtn = document.getElementById('advanced-mix-btn');
    const modalSyncMixBtn = document.getElementById('modal-sync-mix-btn');
    const processBtn = document.getElementById('process-btn');
    const nudgeInput = document.getElementById('nudge');
    const nudgeVal = document.getElementById('nudge-val');
    const analysisResultsEl = document.getElementById('analysis-results');
    const downloadSectionEl = document.getElementById('download-section');
    const downloadTitleEl = document.getElementById('download-title');
    const downloadLinkEl = document.getElementById('download-link');
    const downloadPreviewCardEl = document.getElementById('download-preview-card');
    const downloadPreviewEl = document.getElementById('download-preview');
    const autoMixStatusEl = document.getElementById('auto-mix-status');
    const autoMixStatusLineEl = document.getElementById('auto-mix-status-line');
    const tempoRatioEl = document.getElementById('tempo-ratio');
    const pitchShiftEl = document.getElementById('pitch-shift');
    const overrideBpmInput = document.getElementById('override-bpm');
    const overridePitchShiftInput = document.getElementById('override-pitch-shift');
    const overrideBeatDownbeatInput = document.getElementById('override-beat-downbeat');
    const overrideAcapellaDownbeatInput = document.getElementById('override-acapella-downbeat');
    const advancedMixModal = document.getElementById('advanced-mix-modal');
    const closeModalBtn = document.getElementById('close-modal');
    const restoredSessionBannerEl = document.getElementById('restored-session-banner');
    const timelinePanelEl = document.getElementById('timeline-panel');
    const timelineOffsetLabelEl = document.getElementById('timeline-offset-label');
    const beatTimelineCanvas = document.getElementById('beat-timeline-canvas');
    const acapellaTimelineCanvas = document.getElementById('acapella-timeline-canvas');
    const timelineVocalHandle = document.getElementById('timeline-vocal-handle');

    function updateMixStyleDescription() {
        mixStyleDescriptionEl.textContent = MIX_STYLE_DESCRIPTIONS[getSelectedMixStyle()] || MIX_STYLE_DESCRIPTIONS.balanced;
    }

    function getSelectedMixStyle() {
        return mixStyleSelect.value || 'balanced';
    }

    function getFinalTempoRatio() {
        const ratio = Number(finalTempoRatioInput?.value || 1);
        return Number.isFinite(ratio) ? ratio : 1;
    }

    function updateFinalTempoLabel() {
        if (!finalTempoValueEl) return;
        finalTempoValueEl.textContent = `${Math.round(getFinalTempoRatio() * 100)}%`;
    }

    function getAdvancedMixPayload() {
        const eqBands = Array.from(document.querySelectorAll('[data-advanced-eq-frequency]'))
            .map((slider) => ({
                frequency_hz: Number(slider.dataset.advancedEqFrequency),
                gain_db: Number(slider.value),
                q: Number(slider.dataset.advancedEqQ || 1)
            }))
            .filter((band) => Number.isFinite(band.gain_db) && band.gain_db !== 0);

        const payload = { eq_bands: eqBands, final_tempo_ratio: getFinalTempoRatio() };
        for (const control of document.querySelectorAll('[data-advanced-control]')) {
            const key = control.dataset.advancedControl;
            const value = Number(control.value);
            if (key && Number.isFinite(value)) {
                payload[key] = value;
            }
        }
        if (payload.focus_frequency_hz && payload.focus_q) {
            payload.eq_bands.push({
                frequency_hz: payload.focus_frequency_hz,
                gain_db: payload.vocal_gain_db || 0,
                q: payload.focus_q
            });
        }
        delete payload.focus_frequency_hz;
        delete payload.focus_q;
        return payload;
    }

    function resetDownloadPresentation() {
        downloadTitleEl.textContent = 'Done!';
        autoMixStatusEl.classList.add('hidden');
        autoMixStatusLineEl.textContent = 'Your remix has been aligned, polished, and exported.';
        ui.setAudioPreview(downloadPreviewCardEl, downloadPreviewEl, null, null);
    }

    function showAutoMixStatus(result) {
        downloadTitleEl.textContent = 'Auto Mix ready';
        autoMixStatusLineEl.textContent = result.status_line || `${result.beat_file_name} and ${result.acapella_file_name} were aligned and exported.`;
        autoMixStatusEl.classList.remove('hidden');
    }

    function renderPreviewVariantControls(result) {
        const container = document.getElementById('preview-variant-controls');
        if (!container) return;
        container.innerHTML = '';
        const variants = result.preview_variants || {
            final: {
                label: 'Final Mix',
                preview_url: result.preview_url || result.download_url,
                thumbnail_url: result.thumbnail_url
            }
        };
        for (const [key, variant] of Object.entries(variants)) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'preview-variant-btn';
            button.dataset.variantKey = key;
            button.textContent = variant.label || ui.formatTitleCase(key);
            button.addEventListener('click', () => {
                downloadPreviewEl.pause();
                ui.setAudioPreview(downloadPreviewCardEl, downloadPreviewEl, variant.preview_url, variant.thumbnail_url);
                container.querySelectorAll('.preview-variant-btn').forEach((btn) => btn.classList.remove('active'));
                button.classList.add('active');
            });
            container.appendChild(button);
        }
        const firstButton = container.querySelector('.preview-variant-btn');
        if (firstButton) firstButton.classList.add('active');
    }

    function showRestoredSessionBanner(show) {
        if (!restoredSessionBannerEl) return;
        restoredSessionBannerEl.classList.toggle('hidden', !show);
    }

    function drawWaveformCanvas(canvas, peaks, options = {}) {
        if (!canvas || !Array.isArray(peaks)) return;
        const rect = canvas.getBoundingClientRect();
        const width = Math.max(1, Math.round(rect.width * window.devicePixelRatio));
        const height = Math.max(1, Math.round(rect.height * window.devicePixelRatio));
        if (canvas.width !== width) canvas.width = width;
        if (canvas.height !== height) canvas.height = height;

        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = '#050505';
        ctx.fillRect(0, 0, width, height);

        const midpoint = height / 2;
        const barWidth = width / Math.max(1, peaks.length);
        ctx.fillStyle = options.color || '#ffffff';
        peaks.forEach((peak, index) => {
            const amplitude = Math.max(2, Number(peak || 0) * height * 0.42);
            ctx.fillRect(index * barWidth, midpoint - amplitude, Math.max(1, barWidth - 1), amplitude * 2);
        });

        if (Array.isArray(options.beatTimes) && Number.isFinite(options.durationSec) && options.durationSec > 0) {
            ctx.strokeStyle = 'rgba(255,255,255,0.18)';
            ctx.lineWidth = Math.max(1, window.devicePixelRatio);
            for (const beatTime of options.beatTimes) {
                const x = (beatTime / options.durationSec) * width;
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x, height);
                ctx.stroke();
            }
        }
    }

    function updateTimelineHandle() {
        if (!state.timelineData || !timelineVocalHandle) return;
        const beatDuration = 60 / Math.max(1, state.timelineData.grid.bpm);
        const nudgeBeats = Number(nudgeInput.value || 0);
        state.timelineOffsetSec = state.timelineData.suggested_offset_sec + (nudgeBeats * beatDuration);
        timelineOffsetLabelEl.textContent = `Offset: ${state.timelineOffsetSec.toFixed(3)}s`;

        const canvasRect = acapellaTimelineCanvas.getBoundingClientRect();
        const durationSec = Math.max(state.timelineData.beat.duration_sec, state.timelineData.acapella.duration_sec, 1);
        const x = Math.max(0, Math.min(canvasRect.width, (Math.max(0, state.timelineOffsetSec) / durationSec) * canvasRect.width));
        timelineVocalHandle.style.transform = `translateX(${x}px)`;
    }

    function renderTimeline(data) {
        state.timelineData = data;
        timelinePanelEl.classList.remove('hidden');
        const durationSec = Math.max(data.beat.duration_sec, data.acapella.duration_sec, 1);
        drawWaveformCanvas(beatTimelineCanvas, data.beat.peaks, {
            color: '#f4f4f4',
            beatTimes: data.grid.beat_times,
            durationSec
        });
        drawWaveformCanvas(acapellaTimelineCanvas, data.acapella.peaks, {
            color: '#a8f0ff',
            beatTimes: data.grid.beat_times,
            durationSec
        });
        updateTimelineHandle();
    }

    async function refreshTimeline() {
        if (!state.analysisData) return;
        try {
            const response = await fetch('/analysis/latest/timeline');
            if (!response.ok) return;
            renderTimeline(await response.json());
        } catch (_error) {
            timelinePanelEl.classList.add('hidden');
        }
    }

    function renderAnalysis(data) {
        document.getElementById('beat-bpm').textContent = ui.formatNumber(data.beat.bpm, 1);
        document.getElementById('beat-key').textContent = data.beat.key;
        document.getElementById('beat-downbeat').textContent = ui.formatNumber(data.beat.downbeat, 3);
        document.getElementById('acap-bpm').textContent = ui.formatNumber(data.acapella.bpm, 1);
        document.getElementById('acap-key').textContent = data.acapella.key;
        document.getElementById('acap-downbeat').textContent = ui.formatNumber(data.acapella.downbeat, 3);
        document.getElementById('beat-confidence').textContent = ui.formatConfidence(data.beat.confidence?.bpm);
        document.getElementById('acap-confidence').textContent = ui.formatConfidence(data.acapella.confidence?.bpm);
        tempoRatioEl.textContent = ui.formatTempoRatio(data.suggested.tempo_ratio);
        pitchShiftEl.textContent = ui.formatPitchShift(data.suggested.pitch_shift);
        if (overrideBpmInput) overrideBpmInput.value = ui.formatNumber(data.beat.bpm, 2);
        if (overridePitchShiftInput) overridePitchShiftInput.value = Number.isFinite(data.suggested.pitch_shift) ? data.suggested.pitch_shift : 0;
        if (overrideBeatDownbeatInput) overrideBeatDownbeatInput.value = ui.formatNumber(data.beat.downbeat, 3);
        if (overrideAcapellaDownbeatInput) overrideAcapellaDownbeatInput.value = ui.formatNumber(data.acapella.downbeat, 3);
        analysisResultsEl.classList.remove('hidden');
    }

    function applyAnalysisState(data, options = {}) {
        const { restored = Boolean(data?.restored) } = options;
        state.analysisData = data;
        renderAnalysis(data);

        if (restored) {
            setRestoredFileMeta(beatInput, data.beat?.source_name);
            setRestoredFileMeta(acapellaInput, data.acapella?.source_name);
            showRestoredSessionBanner(true);
            const manualMix = data.manual_mix || {};
            const nudgeValue = manualMix.nudge_beats ?? 0;
            if (Number.isFinite(nudgeValue)) {
                nudgeInput.value = nudgeValue;
                nudgeVal.textContent = nudgeValue;
            } else {
                nudgeInput.value = '0';
                nudgeVal.textContent = '0';
            }
            if (manualMix.mix_style) {
                mixStyleSelect.value = manualMix.mix_style;
                updateMixStyleDescription();
            }
        } else {
            showRestoredSessionBanner(false);
            nudgeInput.value = '0';
            nudgeVal.textContent = '0';
        }
        syncControls();
        refreshTimeline();
    }

    async function persistAnalysisSettings() {
        if (!state.analysisData) return;
        try {
            const resp = await fetch('/analysis/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    beat_file_id: state.analysisData.beat?.file_id,
                    acapella_file_id: state.analysisData.acapella?.file_id,
                    nudge_beats: parseFloat(nudgeInput.value),
                    mix_style: getSelectedMixStyle()
                })
            });
            if (!resp.ok) console.warn('Settings persistence failed');
        } catch (err) {
            console.warn('Settings persistence error:', err);
        }
    }

    async function runAnalysis() {
        const beatFile = beatInput.files[0];
        const acapFile = acapellaInput.files[0];
        if (!beatFile || !acapFile) {
            ui.showError('Please select both a beat and an acapella file.');
            return;
        }
        ui.hideError();
        downloadSectionEl.classList.add('hidden');
        const processingEl = document.getElementById('processing');
        if (processingEl) processingEl.classList.add('hidden');
        analysisResultsEl.classList.add('hidden');
        state.analysisData = null;
        state.isAnalyzing = true;
        syncControls();

        const formData = new FormData();
        formData.append('beat', beatFile);
        formData.append('acapella', acapFile);

        try {
            const response = await fetch('/analyze', { method: 'POST', body: formData });
            if (!response.ok) throw new Error(await api.getErrorMessage(response, 'Analysis failed'));
            const payload = await response.json();
            applyAnalysisState(payload);
        } catch (err) {
            ui.showError('Error analyzing audio: ' + err.message);
        } finally {
            state.isAnalyzing = false;
            syncControls();
        }
    }

    async function startAutoMixJob(options = {}) {
        const { preserveStemSplit = true } = options;
        const beatFile = beatInput.files[0];
        const acapFile = acapellaInput.files[0];
        if (!beatFile || !acapFile) {
            ui.showError('Please select both a beat and an acapella file.');
            return;
        }
        ui.hideError();
        state.analysisData = null;
        analysisResultsEl.classList.add('hidden');
        downloadSectionEl.classList.add('hidden');
        resetDownloadPresentation();
        if (!preserveStemSplit) {
            // resetStemDownloadPresentation is handled via event or caller
            document.dispatchEvent(new CustomEvent('reset-stem-download'));
        }
        state.isAutoMixing = true;
        syncControls();

        const formData = new FormData();
        formData.append('beat', beatFile);
        formData.append('acapella', acapFile);
        formData.append('mix_style', getSelectedMixStyle());
        formData.append('final_tempo_ratio', getFinalTempoRatio());

        try {
            const response = await fetch('/auto-mix/jobs', { method: 'POST', body: formData });
            if (!response.ok) throw new Error(await api.getErrorMessage(response, 'Auto mix failed'));
            const initialJob = await response.json();
            const finalJob = await watchJob(initialJob, 'Auto Mix');
            const result = finalJob.result;
            downloadLinkEl.href = result.download_url;
            ui.setAudioPreview(downloadPreviewCardEl, downloadPreviewEl, result.preview_url || result.download_url, result.thumbnail_url);
            renderPreviewVariantControls(result);
            showAutoMixStatus(result);
            downloadSectionEl.classList.remove('hidden');
            await refreshHistory();
        } catch (err) {
            ui.showError('Error creating auto mix: ' + err.message);
        } finally {
            state.isAutoMixing = false;
            syncControls();
        }
    }

    function setNudgeFromTimelinePointer(clientX) {
        if (!state.timelineData) return;
        const rect = acapellaTimelineCanvas.getBoundingClientRect();
        const clampedX = Math.max(0, Math.min(rect.width, clientX - rect.left));
        const durationSec = Math.max(state.timelineData.beat.duration_sec, state.timelineData.acapella.duration_sec, 1);
        const selectedOffsetSec = (clampedX / Math.max(1, rect.width)) * durationSec;
        const beatDuration = 60 / Math.max(1, state.timelineData.grid.bpm);
        const nudgeBeats = (selectedOffsetSec - state.timelineData.suggested_offset_sec) / beatDuration;
        const clampedNudge = Math.max(Number(nudgeInput.min), Math.min(Number(nudgeInput.max), nudgeBeats));
        nudgeInput.value = clampedNudge.toFixed(2);
        nudgeVal.textContent = nudgeInput.value;
        updateTimelineHandle();
        persistAnalysisSettings();
    }

    function openModal() {
        advancedMixModal.classList.remove('hidden');
    }

    function closeModal() {
        advancedMixModal.classList.add('hidden');
    }

    async function runSyncAndMix() {
        if (!state.analysisData) return;
        const nudge = parseFloat(nudgeInput.value);
        ui.hideError();
        downloadSectionEl.classList.add('hidden');
        resetDownloadPresentation();
        ui.showProcessing('Rendering your styled remix...', 0);
        state.isProcessing = true;
        syncControls();

        const bpm = Number(overrideBpmInput.value || state.analysisData.beat.bpm);
        const pitchShift = Number(overridePitchShiftInput.value || state.analysisData.suggested.pitch_shift);
        const beatDownbeat = Number(overrideBeatDownbeatInput.value || state.analysisData.beat.downbeat);
        const acapellaDownbeat = Number(overrideAcapellaDownbeatInput.value || state.analysisData.acapella.downbeat);

        const formData = new FormData();
        formData.append('beat_file_id', state.analysisData.beat.file_id);
        formData.append('acapella_file_id', state.analysisData.acapella.file_id);
        formData.append('bpm', bpm);
        formData.append('pitch_shift', pitchShift);
        formData.append('tempo_ratio', state.analysisData.suggested.tempo_ratio);
        formData.append('beat_downbeat', beatDownbeat);
        formData.append('acapella_downbeat', acapellaDownbeat);
        formData.append('nudge_beats', nudge);
        formData.append('mix_style', getSelectedMixStyle());
        formData.append('advanced_mix', JSON.stringify(getAdvancedMixPayload()));

        try {
            const response = await fetch('/process', { method: 'POST', body: formData });
            if (!response.ok) throw new Error(await api.getErrorMessage(response, 'Processing failed'));
            const result = await response.json();
            downloadLinkEl.href = result.download_url;
            ui.setAudioPreview(downloadPreviewCardEl, downloadPreviewEl, result.preview_url || result.download_url, result.thumbnail_url);
            renderPreviewVariantControls(result);
            resetDownloadPresentation();
            downloadSectionEl.classList.remove('hidden');
            closeModal();
        } catch (err) {
            ui.showError('Error processing audio: ' + err.message);
        } finally {
            ui.hideProcessing();
            state.isProcessing = false;
            syncControls();
        }
    }

    // Event Listeners
    if (analyzeBtn) analyzeBtn.addEventListener('click', runAnalysis);
    if (finalTempoRatioInput) {
        finalTempoRatioInput.addEventListener('input', updateFinalTempoLabel);
        updateFinalTempoLabel();
    }
    if (autoMixBtn) autoMixBtn.addEventListener('click', async () => {
        await startAutoMixJob({ preserveStemSplit: true });
    });
    if (advancedMixBtn) advancedMixBtn.addEventListener('click', () => {
        if (!beatInput.files[0] || !acapellaInput.files[0]) {
            ui.showError('Please select both a beat and an acapella file first.');
            return;
        }
        ui.hideError();
        openModal();
    });
    if (closeModalBtn) closeModalBtn.addEventListener('click', closeModal);
    if (advancedMixModal) advancedMixModal.querySelector('.modal-overlay')?.addEventListener('click', closeModal);
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && advancedMixModal && !advancedMixModal.classList.contains('hidden')) {
            closeModal();
        }
    });
    if (processBtn) processBtn.addEventListener('click', runSyncAndMix);
    if (modalSyncMixBtn) modalSyncMixBtn.addEventListener('click', runSyncAndMix);
    if (nudgeInput) nudgeInput.addEventListener('input', (event) => {
        nudgeVal.textContent = event.target.value;
        updateTimelineHandle();
        persistAnalysisSettings();
    });
    if (mixStyleSelect) mixStyleSelect.addEventListener('change', () => {
        updateMixStyleDescription();
        persistAnalysisSettings();
    });
    if (timelineVocalHandle) {
        timelineVocalHandle.addEventListener('pointerdown', (event) => {
            event.preventDefault();
            timelineVocalHandle.setPointerCapture(event.pointerId);
        });
        timelineVocalHandle.addEventListener('pointermove', (event) => {
            if (!timelineVocalHandle.hasPointerCapture(event.pointerId)) return;
            setNudgeFromTimelinePointer(event.clientX);
        });
        timelineVocalHandle.addEventListener('pointerup', (event) => {
            if (timelineVocalHandle.hasPointerCapture(event.pointerId)) {
                timelineVocalHandle.releasePointerCapture(event.pointerId);
            }
        });
    }
    window.addEventListener('resize', () => {
        if (state.timelineData) renderTimeline(state.timelineData);
    });
    document.addEventListener('reset-download-presentation', () => {
        resetDownloadPresentation();
    });

    updateMixStyleDescription();

    return { applyAnalysisState, runAnalysis, runSyncAndMix, startAutoMixJob, updateMixStyleDescription, showRestoredSessionBanner, resetDownloadPresentation };
}
