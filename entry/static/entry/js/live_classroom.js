(function () {
    "use strict";

    var root = document.querySelector("[data-live-classroom-root]");
    if (!root) {
        return;
    }

    var role = root.dataset.role || "";
    var sessionId = root.dataset.sessionId || "";
    var tokenUrl = root.dataset.tokenUrl || "";
    var recordingUrl = root.dataset.recordingUrl || "";
    var statusEl = root.querySelector("[data-live-status]");
    var recordingStatusEl = root.querySelector("[data-recording-status]");
    var participantList = root.querySelector("[data-participant-list]");
    var mainStage = root.querySelector("[data-main-stage]");
    var mainVideo = root.querySelector("[data-main-video]");
    var mainEmptyLabel = root.querySelector("[data-main-empty-label]");
    var mainEmptyText = root.querySelector("[data-main-empty-text]");
    var shareButton = root.querySelector("[data-share-screen]");
    var stopShareButton = root.querySelector("[data-stop-share]");
    var viewNormalButton = root.querySelector("[data-view-normal]");
    var snapshotScript = document.getElementById("live-classroom-snapshot");
    var snapshot = {};
    var ws = null;
    var room = null;
    var localScreenTracks = [];
    var localScreenStream = null;
    var audioRecorder = null;
    var audioRecordingStream = null;
    var audioRecordingChunks = [];
    var activeAudioRecordingId = null;
    var participantStreams = new Map();
    var participantByIdentity = new Map();
    var spotlightParticipantId = null;
    var teacherIdentity = null;

    try {
        snapshot = snapshotScript ? JSON.parse(snapshotScript.textContent || "{}") : {};
    } catch (error) {
        snapshot = {};
    }

    function setStatus(message) {
        if (statusEl) {
            statusEl.textContent = message;
        }
    }

    function isLocalScreenSharing() {
        return Boolean(localScreenStream);
    }

    function localSharingLabel() {
        return role === "teacher" ? "老师屏幕共享中" : "学生屏幕共享中";
    }

    function localSharingText() {
        if (role === "teacher") {
            return "本端正在共享屏幕，已隐藏远端预览，避免屏幕套屏幕。学生端正在接收老师屏幕。";
        }
        return "本端正在共享屏幕，已隐藏老师预览，避免屏幕套屏幕。老师端正在接收学生屏幕。";
    }

    function setMainEmpty(label, text) {
        if (mainEmptyLabel && label) {
            mainEmptyLabel.textContent = label;
        }
        if (mainEmptyText && text) {
            mainEmptyText.textContent = text;
        }
    }

    function recordingStatusLabel(status) {
        var labels = {
            starting: "正在启动",
            stopping: "正在停止",
            active: "正在录音",
            completed: "已完成",
            failed: "失败"
        };
        return labels[status] || "未开始";
    }

    function setRecordingStatus(recording) {
        if (!recordingStatusEl) {
            return;
        }
        if (!recording) {
            recordingStatusEl.textContent = "录音状态：未开始";
            return;
        }
        var message = "录音状态：" + recordingStatusLabel(recording.status);
        if (recording.file_url) {
            message += "\n文件：" + recording.file_url;
        }
        if (recording.error_message) {
            message += "\n" + recording.error_message;
        }
        recordingStatusEl.textContent = message;
    }

    function getCookie(name) {
        var parts = document.cookie ? document.cookie.split(";") : [];
        for (var i = 0; i < parts.length; i += 1) {
            var item = parts[i].trim();
            if (item.indexOf(name + "=") === 0) {
                return decodeURIComponent(item.slice(name.length + 1));
            }
        }
        return "";
    }

    function buildWsUrl(path) {
        var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        return protocol + "//" + window.location.host + path;
    }

    function sendWs(event, payload) {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            setStatus("课堂状态连接尚未就绪。");
            return;
        }
        ws.send(JSON.stringify({ event: event, payload: payload || {} }));
    }

    function csrfFetch(url, options) {
        var headers = options.headers || {};
        headers["X-CSRFToken"] = getCookie("csrftoken");
        options.headers = headers;
        options.credentials = "same-origin";
        return fetch(url, options);
    }

    function updateParticipantMaps(participants) {
        participantByIdentity.clear();
        teacherIdentity = null;
        (participants || []).forEach(function (participant) {
            participantByIdentity.set(participant.livekit_identity, participant);
            if (participant.role === "teacher") {
                teacherIdentity = participant.livekit_identity;
            }
        });
    }

    function participantLabel(participant) {
        return participant.student_name || participant.display_name || participant.livekit_identity || "学生";
    }

    function screenStateLabel(screenState) {
        var labels = {
            pending: "待共享",
            sharing: "共享中",
            stopped: "已停止",
            rejected: "已拒绝",
            none: "未共享"
        };
        return labels[screenState] || screenState || "未共享";
    }

    function mediaStreamFromTrack(track) {
        if (!track) {
            return null;
        }
        if (track.mediaStreamTrack) {
            return new MediaStream([track.mediaStreamTrack]);
        }
        if (typeof track.attach === "function") {
            var element = track.attach();
            if (element && element.srcObject instanceof MediaStream) {
                return element.srcObject;
            }
        }
        return null;
    }

    function renderParticipants(participants) {
        if (!participantList) {
            return;
        }
        var students = (participants || []).filter(function (participant) {
            return participant.role === "student";
        });
        participantList.innerHTML = "";
        if (!students.length) {
            var empty = document.createElement("div");
            empty.className = "live-classroom-status";
            empty.textContent = "还没有学生加入。";
            participantList.appendChild(empty);
            return;
        }
        students.forEach(function (participant) {
            var card = document.createElement("article");
            card.className = "live-classroom-participant";
            card.dataset.participantId = String(participant.id);
            if (String(participant.id) === String(spotlightParticipantId)) {
                card.classList.add("is-spotlight");
            }

            var videoShell = document.createElement("div");
            videoShell.className = "live-classroom-participant__video";
            var stream = participantStreams.get(participant.livekit_identity);
            if (stream && !isLocalScreenSharing()) {
                var video = document.createElement("video");
                video.autoplay = true;
                video.playsInline = true;
                video.muted = true;
                video.srcObject = stream;
                videoShell.appendChild(video);
            } else {
                var placeholder = document.createElement("div");
                placeholder.className = "live-classroom-participant__placeholder";
                if (isLocalScreenSharing()) {
                    placeholder.textContent = "本端共享中，已隐藏预览";
                } else {
                    placeholder.textContent = participant.screen_state === "sharing" ? "正在等待画面..." : "未共享屏幕";
                }
                videoShell.appendChild(placeholder);
            }

            var infoRow = document.createElement("div");
            infoRow.className = "live-classroom-participant__info";

            var name = document.createElement("div");
            name.className = "live-classroom-participant__name";
            name.textContent = participantLabel(participant);

            var meta = document.createElement("div");
            meta.className = "live-classroom-participant__meta";
            meta.textContent = screenStateLabel(participant.screen_state);

            var actions = document.createElement("div");
            actions.className = "live-classroom-participant__actions";
            var spotlightButton = document.createElement("button");
            spotlightButton.type = "button";
            spotlightButton.textContent = "投屏";
            spotlightButton.addEventListener("click", function () {
                spotlightParticipantId = participant.id;
                sendWs("teacher_view", {
                    view_mode: "spotlight_student",
                    spotlight_participant_id: participant.id
                });
                if (!setMainStream(participant.livekit_identity)) {
                    setStatus("该学生还没有发布屏幕共享。");
                }
                renderParticipants(students);
            });
            actions.appendChild(spotlightButton);

            card.addEventListener("contextmenu", function (event) {
                event.preventDefault();
                spotlightButton.click();
            });

            infoRow.appendChild(name);
            infoRow.appendChild(meta);
            infoRow.appendChild(actions);
            card.appendChild(videoShell);
            card.appendChild(infoRow);
            participantList.appendChild(card);
        });
    }

    function showStreamOnMain(stream) {
        if (!mainStage || !mainVideo) {
            return;
        }
        if (!stream) {
            mainVideo.srcObject = null;
            mainStage.classList.remove("is-playing");
            return;
        }
        mainVideo.srcObject = stream;
        mainStage.classList.add("is-playing");
        mainVideo.play().catch(function () {});
    }

    function setMainStream(identity) {
        if (isLocalScreenSharing()) {
            var hasHiddenStream = participantStreams.has(identity);
            setMainEmpty(localSharingLabel(), localSharingText());
            showStreamOnMain(null);
            return hasHiddenStream;
        }
        var stream = participantStreams.get(identity);
        showStreamOnMain(stream);
        return Boolean(stream);
    }

    function showDefaultMainStream() {
        if (isLocalScreenSharing()) {
            setMainEmpty(localSharingLabel(), localSharingText());
            showStreamOnMain(null);
            return;
        }
        if (teacherIdentity) {
            setMainStream(teacherIdentity);
            return;
        }
        showStreamOnMain(null);
    }

    function upsertParticipant(participant) {
        if (!participant || !participant.id) {
            return;
        }
        var participants = snapshot.participants || [];
        var updated = false;
        participants = participants.map(function (item) {
            if (String(item.id) === String(participant.id)) {
                updated = true;
                return Object.assign({}, item, participant);
            }
            return item;
        });
        if (!updated) {
            participants.push(participant);
        }
        snapshot.participants = participants;
        applySnapshot(snapshot);
    }

    function applySnapshot(nextSnapshot) {
        snapshot = nextSnapshot || snapshot || {};
        var session = snapshot.session || {};
        var participants = snapshot.participants || [];
        spotlightParticipantId = session.spotlight_participant_id || spotlightParticipantId;
        updateParticipantMaps(participants);
        setRecordingStatus(snapshot.recording);
        renderParticipants(participants);
        if (session.view_mode === "normal") {
            showDefaultMainStream();
        }
        if (session.view_mode === "spotlight_student" && spotlightParticipantId) {
            var spotlight = participants.find(function (participant) {
                return String(participant.id) === String(spotlightParticipantId);
            });
            if (spotlight) {
                setMainStream(spotlight.livekit_identity);
            }
        }
    }

    async function getToken() {
        var response = await csrfFetch(tokenUrl, { method: "POST", headers: {} });
        var payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.error || "LiveKit token 获取失败。");
        }
        return payload;
    }

    async function connectLiveKit() {
        if (!tokenUrl || room) {
            return room;
        }
        var LiveKit = window.LivekitClient || window.LiveKitClient;
        if (!LiveKit || !LiveKit.Room) {
            throw new Error("LiveKit 前端 SDK 未加载。");
        }
        var tokenPayload = await getToken();
        room = new LiveKit.Room();
        room.on(LiveKit.RoomEvent.TrackSubscribed, function (track, publication, participant) {
            if (!track || track.kind !== "video") {
                return;
            }
            var stream = mediaStreamFromTrack(track);
            if (!stream) {
                return;
            }
            participantStreams.set(participant.identity, stream);
            var known = participantByIdentity.get(participant.identity);
            if (known && String(known.id) === String(spotlightParticipantId)) {
                setMainStream(participant.identity);
            } else if (known && known.role === "teacher" && (!snapshot.session || snapshot.session.view_mode === "normal")) {
                setMainStream(participant.identity);
            }
            renderParticipants(snapshot.participants || []);
        });
        room.on(LiveKit.RoomEvent.TrackUnsubscribed, function (track, publication, participant) {
            var known = participantByIdentity.get(participant.identity);
            participantStreams.delete(participant.identity);
            if (participant.identity === teacherIdentity || (known && String(known.id) === String(spotlightParticipantId))) {
                showDefaultMainStream();
            }
            renderParticipants(snapshot.participants || []);
        });
        await room.connect(tokenPayload.livekit_url, tokenPayload.token);
        setStatus("已连接 LiveKit 房间。");
        return room;
    }

    function reportScreenState(screenState, displaySurface) {
        if (role !== "student") {
            return;
        }
        sendWs("screen_state", { screen_state: screenState, display_surface: displaySurface || "" });
    }

    async function publishWholeScreen() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
            setStatus("当前浏览器不支持屏幕共享。");
            return;
        }
        var stream = null;
        try {
            stream = await navigator.mediaDevices.getDisplayMedia({
                video: { displaySurface: "monitor" },
                audio: false
            });
            var videoTrack = stream.getVideoTracks()[0];
            var displaySurface = videoTrack && videoTrack.getSettings ? videoTrack.getSettings().displaySurface : "";
            if (displaySurface !== "monitor") {
                stream.getTracks().forEach(function (track) {
                    track.stop();
                });
                reportScreenState("rejected", displaySurface || "unknown");
                setStatus("必须选择整个屏幕，窗口或浏览器标签页不能投屏。");
                return;
            }

            var LiveKit = window.LivekitClient || window.LiveKitClient;
            var currentRoom = await connectLiveKit();
            localScreenStream = stream;
            localScreenTracks = stream.getTracks();
            var screenShareSource = LiveKit.Track && LiveKit.Track.Source ? LiveKit.Track.Source.ScreenShare : "screen_share";
            await currentRoom.localParticipant.publishTrack(videoTrack, {
                source: screenShareSource
            });
            videoTrack.addEventListener("ended", function () {
                localScreenTracks = [];
                localScreenStream = null;
                reportScreenState("stopped", "monitor");
                setMainEmpty(role === "teacher" ? "主屏幕" : "课堂主屏幕", role === "teacher" ? "点击上方“共享老师屏幕”，或从右侧选择学生投屏。" : "老师共享屏幕后会显示在这里。");
                showDefaultMainStream();
                renderParticipants(snapshot.participants || []);
                setStatus("屏幕共享已停止。");
            });
            setMainEmpty(localSharingLabel(), localSharingText());
            showDefaultMainStream();
            renderParticipants(snapshot.participants || []);
            reportScreenState("sharing", "monitor");
            setStatus("正在共享整个屏幕。");
        } catch (error) {
            if (stream) {
                stream.getTracks().forEach(function (track) {
                    track.stop();
                });
            }
            localScreenStream = null;
            reportScreenState("stopped", "");
            setStatus(error && error.message ? error.message : "屏幕共享启动失败。");
        }
    }

    function chooseAudioMimeType() {
        var types = [
            "audio/webm;codecs=opus",
            "audio/webm",
            "audio/mp4",
            "audio/ogg;codecs=opus"
        ];
        for (var i = 0; i < types.length; i += 1) {
            if (window.MediaRecorder && MediaRecorder.isTypeSupported(types[i])) {
                return types[i];
            }
        }
        return "";
    }

    function audioExtension(mimeType) {
        if (mimeType.indexOf("mp4") !== -1) {
            return "m4a";
        }
        if (mimeType.indexOf("ogg") !== -1) {
            return "ogg";
        }
        return "webm";
    }

    async function startBrowserAudioRecording() {
        if (!recordingUrl) {
            setStatus("录音接口未配置。");
            return;
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
            setStatus("当前浏览器不支持课堂录音。");
            return;
        }
        if (audioRecorder && audioRecorder.state === "recording") {
            setStatus("录音已经在进行中。");
            return;
        }
        var stream = null;
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
            var mimeType = chooseAudioMimeType();
            audioRecordingChunks = [];
            audioRecordingStream = stream;
            audioRecorder = mimeType ? new MediaRecorder(stream, { mimeType: mimeType }) : new MediaRecorder(stream);
            audioRecorder.addEventListener("dataavailable", function (event) {
                if (event.data && event.data.size > 0) {
                    audioRecordingChunks.push(event.data);
                }
            });
            audioRecorder.addEventListener("stop", function () {
                uploadBrowserAudioRecording(audioRecorder.mimeType || mimeType).catch(function (error) {
                    setStatus(error && error.message ? error.message : "录音文件上传失败。");
                    setRecordingStatus(snapshot.recording || null);
                });
            });
            var response = await csrfFetch(recordingUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "start_audio" })
            });
            var payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || "录音启动失败。");
            }
            snapshot.recording = payload.recording || null;
            activeAudioRecordingId = snapshot.recording ? snapshot.recording.id : null;
            setRecordingStatus(snapshot.recording);
            audioRecorder.start();
            setStatus("正在录音。浏览器正在采集老师麦克风。");
        } catch (error) {
            if (stream) {
                stream.getTracks().forEach(function (track) {
                    track.stop();
                });
            }
            audioRecordingStream = null;
            audioRecorder = null;
            activeAudioRecordingId = null;
            setStatus(error && error.message ? error.message : "录音启动失败。");
            setRecordingStatus(snapshot.recording || null);
        }
    }

    async function uploadBrowserAudioRecording(mimeType) {
        if (!recordingUrl || !activeAudioRecordingId) {
            return;
        }
        var chunks = audioRecordingChunks.slice();
        audioRecordingChunks = [];
        if (audioRecordingStream) {
            audioRecordingStream.getTracks().forEach(function (track) {
                track.stop();
            });
        }
        audioRecordingStream = null;
        audioRecorder = null;
        setRecordingStatus({ status: "stopping" });
        if (!chunks.length) {
            throw new Error("没有录到音频数据。");
        }
        var blob = new Blob(chunks, { type: mimeType || "audio/webm" });
        var formData = new FormData();
        formData.append("action", "upload_audio");
        formData.append("recording_id", String(activeAudioRecordingId));
        formData.append("audio", blob, "classroom-audio." + audioExtension(blob.type || mimeType || ""));
        var response = await csrfFetch(recordingUrl, {
            method: "POST",
            headers: {},
            body: formData
        });
        var payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.error || "录音文件上传失败。");
        }
        activeAudioRecordingId = null;
        snapshot.recording = payload.recording || null;
        setRecordingStatus(snapshot.recording);
        setStatus("录音已停止，文件已生成。");
    }

    function stopBrowserAudioRecording() {
        if (!audioRecorder || audioRecorder.state !== "recording") {
            setStatus("当前没有正在进行的浏览器录音。");
            return;
        }
        setRecordingStatus({ status: "stopping" });
        setStatus("正在停止录音并生成文件...");
        audioRecorder.stop();
    }

    function stopShare() {
        localScreenTracks.forEach(function (track) {
            track.stop();
        });
        localScreenTracks = [];
        localScreenStream = null;
        reportScreenState("stopped", "monitor");
        setMainEmpty(role === "teacher" ? "主屏幕" : "课堂主屏幕", role === "teacher" ? "点击上方“共享老师屏幕”，或从右侧选择学生投屏。" : "老师共享屏幕后会显示在这里。");
        showDefaultMainStream();
        renderParticipants(snapshot.participants || []);
        setStatus("屏幕共享已停止。");
    }

    function connectSessionSocket() {
        if (!sessionId) {
            return;
        }
        ws = new WebSocket(buildWsUrl("/ws/classroom/sessions/" + sessionId + "/"));
        ws.addEventListener("open", function () {
            setStatus("课堂状态已连接。");
        });
        ws.addEventListener("message", function (event) {
            var message = {};
            try {
                message = JSON.parse(event.data || "{}");
            } catch (error) {
                message = {};
            }
            if (message.event === "session_snapshot" || message.event === "session_state") {
                applySnapshot(message.payload);
            } else if (message.event === "participant_joined" || message.event === "screen_state_changed") {
                upsertParticipant(message.payload || {});
            } else if (message.event === "participant_left") {
                upsertParticipant(Object.assign({}, message.payload || {}, { connection_state: "left" }));
            } else if (message.event === "teacher_view_changed") {
                snapshot.session = Object.assign(snapshot.session || {}, message.payload || {});
                applySnapshot(snapshot);
            } else if (message.event === "session_ended") {
                setStatus("课堂已结束。");
                window.location.replace(role === "teacher" ? "/teacher/live-classroom" : "/student/live-classroom");
            } else if (message.event === "recording_changed") {
                var recording = message.payload && message.payload.recording;
                snapshot.recording = recording;
                setRecordingStatus(recording);
                setStatus(recording ? "录音状态：" + recordingStatusLabel(recording.status) : "录音状态已更新。");
            } else if (message.event === "error") {
                setStatus(message.payload && message.payload.error ? message.payload.error : "课堂状态同步失败。");
            }
        });
        ws.addEventListener("close", function () {
            setStatus("课堂状态连接已断开。");
        });
    }

    function connectLobbySocket() {
        ws = new WebSocket(buildWsUrl("/ws/classroom/lobby/"));
        ws.addEventListener("message", function (event) {
            var message = {};
            try {
                message = JSON.parse(event.data || "{}");
            } catch (error) {
                message = {};
            }
            if (message.event === "session_started" || message.event === "session_ended") {
                window.location.reload();
            }
        });
        ws.addEventListener("open", function () {
            setStatus("正在等待老师开课...");
        });
        ws.addEventListener("close", function () {
            setStatus("等待连接已断开，请刷新页面。");
        });
    }

    if (viewNormalButton) {
        viewNormalButton.addEventListener("click", function () {
            spotlightParticipantId = null;
            sendWs("teacher_view", { view_mode: "normal" });
            applySnapshot(Object.assign({}, snapshot, {
                session: Object.assign({}, snapshot.session || {}, { view_mode: "normal", spotlight_participant_id: null })
            }));
        });
    }

    if (shareButton) {
        shareButton.addEventListener("click", publishWholeScreen);
    }
    if (stopShareButton) {
        stopShareButton.addEventListener("click", stopShare);
    }
    document.querySelectorAll("[data-recording-action]").forEach(function (button) {
        button.addEventListener("click", function (event) {
            event.preventDefault();
            if ((button.dataset.recordingAction || "") === "start") {
                startBrowserAudioRecording();
            } else {
                stopBrowserAudioRecording();
            }
        });
    });

    applySnapshot(snapshot);
    if (role === "student-lobby") {
        connectLobbySocket();
    } else {
        connectSessionSocket();
        if ((role === "teacher" || role === "student") && tokenUrl) {
            connectLiveKit().catch(function (error) {
                setStatus(error.message || "LiveKit 连接失败。");
            });
        }
    }
}());
