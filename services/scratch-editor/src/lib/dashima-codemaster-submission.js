const DEFAULT_AUTOSAVE_INTERVAL_MS = 60000;
const MIN_AUTOSAVE_INTERVAL_MS = 15000;

const getSearchParams = search => {
    if (typeof URLSearchParams === 'undefined') return new Map();
    if (typeof search === 'string') return new URLSearchParams(search);
    if (typeof window !== 'undefined' && window.location) {
        return new URLSearchParams(window.location.search);
    }
    return new URLSearchParams('');
};

const getParam = (params, name) => {
    if (!params || typeof params.get !== 'function') return '';
    return String(params.get(name) || '').trim();
};

const parseAutosaveInterval = value => {
    const interval = Number.parseInt(value, 10);
    if (!Number.isFinite(interval) || interval <= 0) return DEFAULT_AUTOSAVE_INTERVAL_MS;
    return Math.max(interval, MIN_AUTOSAVE_INTERVAL_MS);
};

const parseCodemasterLaunchParams = search => {
    const params = getSearchParams(search);
    const source = getParam(params, 'source');
    const submissionId = getParam(params, 'submission_id');
    const token = getParam(params, 'token');
    const callbackUrl = getParam(params, 'callback_url');
    const returnUrl = getParam(params, 'return_url');
    const questionNo = getParam(params, 'question_no');

    return {
        enabled: source === 'codemaster' && Boolean(submissionId && token && callbackUrl),
        source,
        platform: getParam(params, 'platform'),
        submissionId,
        token,
        callbackUrl,
        returnUrl,
        sessionId: getParam(params, 'session_id'),
        paperId: getParam(params, 'paper_id'),
        questionId: getParam(params, 'question_id'),
        questionNo,
        autosaveIntervalMs: parseAutosaveInterval(
            getParam(params, 'autosave_interval_ms') || getParam(params, 'autosave_interval')
        )
    };
};

const buildCodemasterProjectTitle = (projectTitle, launchParams) => {
    const title = String(projectTitle || '').trim();
    if (title) return title;
    if (launchParams && launchParams.questionNo) {
        return `第 ${launchParams.questionNo} 题 Scratch 作品`;
    }
    return 'Scratch 作品';
};

const buildCodemasterArtifacts = snapshot => {
    const safeSnapshot = snapshot || {};
    const profile = safeSnapshot.profile || {};
    const project = safeSnapshot.project || {};
    const recentEvents = Array.isArray(safeSnapshot.recentEvents) ? safeSnapshot.recentEvents : [];
    return {
        learning_event_count: recentEvents.length,
        learning_session_id: safeSnapshot.sessionId || '',
        learning_updated_at: safeSnapshot.updatedAt || '',
        block_count: Number(project.blockCount || 0),
        sprite_count: Number(project.spriteCount || 0),
        programming_style: profile.programmingStyle || '',
        learning_style: profile.learningStyle || '',
        profile_confidence: Number(profile.confidence || 0)
    };
};

const appendJsonField = (formData, key, value) => {
    formData.append(key, JSON.stringify(value || (Array.isArray(value) ? [] : {})));
};

const buildCodemasterSubmissionFormData = ({
    token,
    status,
    projectTitle,
    projectBlob,
    projectFileName,
    snapshot,
    aiSummary
}) => {
    const safeSnapshot = snapshot || {};
    const formData = new FormData();
    formData.append('token', token);
    formData.append('status', status);
    formData.append('project_title', projectTitle);
    appendJsonField(formData, 'artifacts', buildCodemasterArtifacts(safeSnapshot));
    appendJsonField(formData, 'learning_events', safeSnapshot.recentEvents || []);
    appendJsonField(formData, 'ai_summary', aiSummary || {
        profile: safeSnapshot.profile || {},
        project: safeSnapshot.project || {}
    });
    if (projectBlob) {
        formData.append('project_file', projectBlob, projectFileName || 'scratch-project.sb3');
    }
    return formData;
};

export {
    buildCodemasterArtifacts,
    buildCodemasterProjectTitle,
    buildCodemasterSubmissionFormData,
    parseCodemasterLaunchParams
};
