const STORAGE_KEY = 'dashima:learning-events:v1';
const SESSION_KEY = 'dashima:learning-session-id:v1';
const MAX_EVENTS = 500;

const meaningfulBlocklyEventTypes = new Set([
    'create',
    'delete',
    'change',
    'move',
    'var_create',
    'var_delete',
    'var_rename',
    'comment_create',
    'comment_delete',
    'comment_change'
]);

const makeSessionId = () => `session-${Date.now()}-${Math.random().toString(36)
    .slice(2, 8)}`;

const safeLocalStorage = {
    get (key) {
        try {
            if (typeof window === 'undefined' || !window.localStorage) return null;
            return window.localStorage.getItem(key);
        } catch (error) {
            return null;
        }
    },
    set (key, value) {
        try {
            if (typeof window === 'undefined' || !window.localStorage) return;
            window.localStorage.setItem(key, value);
        } catch (error) {
            // Ignore storage failures. The in-memory event stream still works.
        }
    }
};

const getSessionId = () => {
    const existing = safeLocalStorage.get(SESSION_KEY);
    if (existing) return existing;

    const created = makeSessionId();
    safeLocalStorage.set(SESSION_KEY, created);
    return created;
};

const parseStoredEvents = () => {
    try {
        return JSON.parse(safeLocalStorage.get(STORAGE_KEY)) || [];
    } catch (error) {
        return [];
    }
};

let events = parseStoredEvents();
let subscribers = [];

const getEventXml = eventJson => {
    if (eventJson && typeof eventJson.xml === 'string') return eventJson.xml;
    if (eventJson && eventJson.newXml) return eventJson.newXml;
    return '';
};

const extractBlockTypes = eventJson => {
    const xml = getEventXml(eventJson);
    if (!xml) return [];

    const result = [];
    const regex = /type="([^"]+)"/g;
    let match = regex.exec(xml);
    while (match) {
        result.push(match[1]);
        match = regex.exec(xml);
    }
    return result;
};

const getTargetSummary = vm => {
    const editingTarget = vm && vm.editingTarget;
    return {
        id: editingTarget && editingTarget.id,
        name: editingTarget && editingTarget.sprite && editingTarget.sprite.name,
        isStage: editingTarget && editingTarget.isStage
    };
};

const getProjectSummary = vm => {
    try {
        const targets = vm.runtime.targets || [];
        let blockCount = 0;
        let spriteCount = 0;
        const opcodeCounts = {};

        targets.forEach(target => {
            if (!target.isStage) spriteCount++;
            const blocks = target.blocks && target.blocks._blocks;
            if (!blocks) return;

            Object.keys(blocks).forEach(blockId => {
                const block = blocks[blockId];
                if (!block || block.shadow) return;

                blockCount++;
                if (block.opcode) {
                    opcodeCounts[block.opcode] = (opcodeCounts[block.opcode] || 0) + 1;
                }
            });
        });

        return {
            blockCount,
            spriteCount,
            opcodeCounts
        };
    } catch (error) {
        return {
            blockCount: 0,
            spriteCount: 0,
            opcodeCounts: {}
        };
    }
};

const save = () => {
    safeLocalStorage.set(STORAGE_KEY, JSON.stringify(events.slice(-MAX_EVENTS)));
};

const countBy = (items, field) => items.reduce((counts, item) => {
    const value = item[field] || 'unknown';
    counts[value] = (counts[value] || 0) + 1;
    return counts;
}, {});

const sumObjectValues = object => Object.keys(object).reduce((total, key) => total + object[key], 0);

const getTopBlockFamilies = recentEvents => {
    const families = {};
    recentEvents.forEach(event => {
        const blockTypes = event.payload && event.payload.blockTypes;
        if (!blockTypes) return;

        blockTypes.forEach(blockType => {
            const family = blockType.split('_')[0];
            families[family] = (families[family] || 0) + 1;
        });
    });
    return families;
};

const labelFromCounts = (counts, totalEdits, runCount) => {
    if (!totalEdits) return '刚开始观察';
    if (runCount >= 3 && runCount >= Math.max(1, totalEdits / 6)) return '边做边试';
    if ((counts.delete || 0) + (counts.change || 0) > (counts.create || 0)) return '调试修正型';
    if ((counts.create || 0) >= 8 && runCount <= 1) return '先搭结构型';
    return '稳步搭建型';
};

const learningLabelFromFamilies = (families, counts) => {
    const logicBlocks = (families.control || 0) + (families.operator || 0) + (families.data || 0);
    const visualBlocks = (families.motion || 0) + (families.looks || 0) + (families.sound || 0);

    if ((counts.change || 0) >= 5) return '参数调试敏感';
    if (logicBlocks > visualBlocks) return '逻辑结构优先';
    if (visualBlocks > logicBlocks) return '视觉反馈优先';
    return '需要继续采样';
};

const buildProfile = recentEvents => {
    const counts = countBy(recentEvents, 'type');
    const blocklyEvents = recentEvents.filter(event => event.type.indexOf('blockly_') === 0);
    const blocklyCounts = countBy(blocklyEvents.map(event => ({
        type: event.payload && event.payload.blocklyType
    })), 'type');
    const runCount = (counts.project_run_requested || 0) + (counts.green_flag || 0);
    const totalEdits = sumObjectValues(blocklyCounts);
    const families = getTopBlockFamilies(recentEvents);

    return {
        programmingStyle: labelFromCounts(blocklyCounts, totalEdits, runCount),
        learningStyle: learningLabelFromFamilies(families, blocklyCounts),
        confidence: Math.min(100, Math.round((recentEvents.length / 30) * 100)),
        counts,
        blocklyCounts,
        families,
        summary: {
            totalEvents: recentEvents.length,
            totalEdits,
            runCount,
            createCount: blocklyCounts.create || 0,
            changeCount: blocklyCounts.change || 0,
            deleteCount: blocklyCounts.delete || 0,
            moveCount: blocklyCounts.move || 0
        }
    };
};

const buildSnapshot = () => {
    const recentEvents = events.slice(-120);
    const lastProjectSummary = recentEvents
        .map(event => event.payload && event.payload.project)
        .filter(Boolean)
        .pop() || {
        blockCount: 0,
        spriteCount: 0,
        opcodeCounts: {}
    };

    return {
        sessionId: getSessionId(),
        updatedAt: new Date().toISOString(),
        recentEvents,
        profile: buildProfile(recentEvents),
        project: lastProjectSummary
    };
};

const notify = () => {
    const snapshot = buildSnapshot();
    subscribers.forEach(subscriber => subscriber(snapshot));
    if (typeof window !== 'undefined' && typeof CustomEvent === 'function') {
        window.dispatchEvent(new CustomEvent('dashima-learning-events-updated', {
            detail: snapshot
        }));
    }
};

const DashimaLearningEventRecorder = {
    record (type, payload) {
        const event = {
            id: `evt-${Date.now()}-${Math.random().toString(36)
                .slice(2, 8)}`,
            sessionId: getSessionId(),
            occurredAt: new Date().toISOString(),
            type,
            payload: payload || {}
        };

        events = events.concat(event).slice(-MAX_EVENTS);
        save();
        notify();
        return event;
    },

    recordBlocklyEvent (blocklyEvent, vm) {
        if (!blocklyEvent || blocklyEvent.isUiEvent) return null;

        const eventJson = typeof blocklyEvent.toJson === 'function' ?
            blocklyEvent.toJson() :
            {
                type: blocklyEvent.type,
                blockId: blocklyEvent.blockId,
                element: blocklyEvent.element,
                name: blocklyEvent.name,
                oldValue: blocklyEvent.oldValue,
                newValue: blocklyEvent.newValue
            };

        const blocklyType = eventJson.type || blocklyEvent.type;
        if (!meaningfulBlocklyEventTypes.has(blocklyType)) return null;

        return this.record(`blockly_${blocklyType}`, {
            blocklyType,
            blockId: eventJson.blockId,
            group: eventJson.group,
            element: eventJson.element,
            name: eventJson.name,
            oldValue: eventJson.oldValue,
            newValue: eventJson.newValue,
            blockTypes: extractBlockTypes(eventJson),
            target: getTargetSummary(vm),
            project: getProjectSummary(vm)
        });
    },

    recordProjectAction (action, payload) {
        return this.record(action, payload);
    },

    getEvents () {
        return events.slice();
    },

    getSnapshot () {
        return buildSnapshot();
    },

    subscribe (subscriber) {
        subscribers = subscribers.concat(subscriber);
        subscriber(this.getSnapshot());
        return () => {
            subscribers = subscribers.filter(item => item !== subscriber);
        };
    },

    clear () {
        events = [];
        save();
        notify();
    }
};

if (typeof window !== 'undefined') {
    window.DashimaLearningEventRecorder = DashimaLearningEventRecorder;
}

export default DashimaLearningEventRecorder;
