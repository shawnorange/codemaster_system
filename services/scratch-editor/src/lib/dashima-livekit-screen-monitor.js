const TOKEN_ENDPOINT = '/api/dashima/livekit-token';

const globalLiveKitNames = [
    'LivekitClient',
    'LiveKitClient',
    'LiveKit'
];

const makeError = (message, code) => {
    const error = new Error(message);
    error.code = code;
    return error;
};

const getGlobal = name => {
    if (typeof window === 'undefined') return null;
    return window[name];
};

const getLiveKitClient = () => {
    for (let i = 0; i < globalLiveKitNames.length; i++) {
        const client = getGlobal(globalLiveKitNames[i]);
        if (client && client.Room) return client;
    }
    return null;
};

const resolveConfigFromWindow = async params => {
    if (typeof window === 'undefined') return null;
    const config = window.DashimaLiveKitConfig;
    if (!config) return null;
    if (typeof config === 'function') {
        const resolvedConfig = await config(params);
        return resolvedConfig;
    }
    return config;
};

const resolveConfigFromEndpoint = async params => {
    if (typeof fetch !== 'function') return null;

    const response = await fetch(TOKEN_ENDPOINT, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            learningContext: params.learningContext,
            role: 'student_screen_publisher'
        })
    });

    if (!response.ok) return null;
    return response.json();
};

const resolveLiveKitConfig = async params => {
    const windowConfig = await resolveConfigFromWindow(params);
    if (windowConfig) return windowConfig;

    try {
        return await resolveConfigFromEndpoint(params);
    } catch (error) {
        return null;
    }
};

const attachPreview = (publication, previewElement) => {
    if (!publication || !previewElement) return;

    const track = publication.videoTrack || publication.track;
    if (!track) return;

    if (track.mediaStreamTrack && typeof MediaStream !== 'undefined') {
        previewElement.srcObject = new MediaStream([track.mediaStreamTrack]);
        return;
    }

    if (typeof track.attach === 'function') {
        track.attach(previewElement);
    }
};

const detachPreview = (room, previewElement) => {
    if (!previewElement) return;
    previewElement.srcObject = null;

    const participant = room && room.localParticipant;
    if (!participant || typeof participant.getTrackPublications !== 'function') return;

    participant.getTrackPublications().forEach(publication => {
        const track = publication && (publication.videoTrack || publication.track);
        if (track && typeof track.detach === 'function') {
            track.detach(previewElement);
        }
    });
};

const getScreenPublication = (room, LiveKitClient) => {
    const participant = room && room.localParticipant;
    if (!participant) return null;

    const screenSource = LiveKitClient.Track &&
        LiveKitClient.Track.Source &&
        LiveKitClient.Track.Source.ScreenShare;

    if (screenSource && typeof participant.getTrackPublication === 'function') {
        const publication = participant.getTrackPublication(screenSource);
        if (publication) return publication;
    }

    if (typeof participant.getTrackPublications !== 'function') return null;

    return participant.getTrackPublications().find(publication => {
        if (!publication) return false;
        if (screenSource && publication.source === screenSource) return true;
        return String(publication.source || publication.trackName || '').indexOf('screen') !== -1;
    }) || null;
};

const bindRoomEvents = (room, LiveKitClient, onStatus) => {
    if (!room || typeof room.on !== 'function' || !LiveKitClient.RoomEvent) return;

    const events = LiveKitClient.RoomEvent;
    if (events.Reconnecting) room.on(events.Reconnecting, () => onStatus('LiveKit 重连中'));
    if (events.Reconnected) room.on(events.Reconnected, () => onStatus('LiveKit 已重连'));
    if (events.Disconnected) room.on(events.Disconnected, () => onStatus('LiveKit 已断开'));
};

let activeRoom = null;
let activeCustomMonitor = null;
let activePreviewElement = null;

const DashimaLiveKitScreenMonitor = {
    async start (params) {
        const options = params || {};
        const onStatus = typeof options.onStatus === 'function' ? options.onStatus : () => {};
        const previewElement = options.previewElement;

        if (typeof window !== 'undefined') {
            const customMonitor = window.DashimaLiveKitMonitor || window.DashimaScreenMonitor;
            if (customMonitor && typeof customMonitor.start === 'function') {
                activeCustomMonitor = customMonitor;
                const result = await customMonitor.start({
                    learningContext: options.learningContext,
                    previewElement,
                    onStatus,
                    provider: 'livekit'
                });
                return {
                    mode: 'livekit_adapter',
                    status: result && result.status ? result.status : 'LiveKit 老师端模块已接入',
                    detail: result
                };
            }
        }

        const LiveKitClient = getLiveKitClient();
        const config = await resolveLiveKitConfig(options);
        if (!LiveKitClient || !config || !config.wsUrl || !config.token) {
            throw makeError(
                'LiveKit 未配置：需要加载 livekit-client，并由后端提供 wsUrl 和学生端 token。',
                'DASHIMA_LIVEKIT_NOT_CONFIGURED'
            );
        }

        await this.stop();
        onStatus('正在连接 LiveKit 房间');

        const room = new LiveKitClient.Room(config.roomOptions || {});
        bindRoomEvents(room, LiveKitClient, onStatus);

        await room.connect(config.wsUrl, config.token, config.connectOptions || {});
        activeRoom = room;
        activePreviewElement = previewElement;

        onStatus('正在发布屏幕流');
        await room.localParticipant.setScreenShareEnabled(
            true,
            config.screenShareOptions || {audio: false}
        );

        attachPreview(getScreenPublication(room, LiveKitClient), previewElement);

        return {
            mode: 'livekit',
            status: 'LiveKit 屏幕流已发布',
            roomName: room.name || config.roomName || '',
            participantIdentity: room.localParticipant && room.localParticipant.identity
        };
    },

    async stop () {
        const customMonitor = activeCustomMonitor;
        activeCustomMonitor = null;
        if (customMonitor && typeof customMonitor.stop === 'function') {
            await customMonitor.stop();
        }

        const room = activeRoom;
        activeRoom = null;
        if (room) {
            if (room.localParticipant &&
                    typeof room.localParticipant.setScreenShareEnabled === 'function') {
                await room.localParticipant.setScreenShareEnabled(false);
            }
            detachPreview(room, activePreviewElement);
            room.disconnect();
            activePreviewElement = null;
        }
    }
};

if (typeof window !== 'undefined') {
    window.DashimaLiveKitScreenMonitor = DashimaLiveKitScreenMonitor;
}

export default DashimaLiveKitScreenMonitor;
