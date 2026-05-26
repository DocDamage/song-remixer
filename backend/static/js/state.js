export const ACCEPTED_AUDIO_EXTENSIONS = /\.(mp3|wav|flac|m4a|aac|ogg|aif|aiff|wma)$/i;

export const MIX_STYLE_DESCRIPTIONS = {
    balanced: 'Balanced studio polish with controlled loudness and clear vocals.',
    club: 'Push the drums and low end harder while keeping the vocal pinned in front.',
    'vocal-focus': 'Create more space for the vocal and keep the beat tucked behind it.',
    'demo-loud': 'The most aggressive master for quick demos and loud references.'
};

export const state = {
    analysisData: null,
    isAnalyzing: false,
    isProcessing: false,
    isAutoMixing: false,
    isSplittingStems: false,
    isLoadingStemVocals: false,
    stemSplitResult: null,
    timelineData: null,
    timelineOffsetSec: 0,
};

export const jobs = {
    activeJobs: new Map(),
};
