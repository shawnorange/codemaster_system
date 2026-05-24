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
    var activityUrl = root.dataset.activityUrl || "";
    var statusEl = root.querySelector("[data-live-status]");
    var recordingStatusEl = root.querySelector("[data-recording-status]");
    var recordingHistoryList = root.querySelector("[data-recording-history-list]");
    var activitySummaryEl = root.querySelector("[data-activity-summary]");
    var participantList = root.querySelector("[data-participant-list]");
    var mainStage = root.querySelector("[data-main-stage]");
    var mainVideo = root.querySelector("[data-main-video]");
    var mainEmptyLabel = root.querySelector("[data-main-empty-label]");
    var mainEmptyText = root.querySelector("[data-main-empty-text]");
    var shareButton = root.querySelector("[data-share-screen]");
    var stopShareButton = root.querySelector("[data-stop-share]");
    var viewNormalButton = root.querySelector("[data-view-normal]");
    var fullscreenButton = root.querySelector("[data-stage-fullscreen]");
    var activityDialog = root.querySelector("[data-activity-dialog]");
    var activityForm = root.querySelector("[data-activity-form]");
    var activityDialogStatus = root.querySelector("[data-activity-dialog-status]");
    var activitySubmitButton = root.querySelector("[data-activity-submit]");
    var activityTypeSelect = root.querySelector("[data-activity-type]");
    var correctAnswerSelect = root.querySelector("[data-correct-answer]");
    var promptFileInput = root.querySelector("[data-prompt-file]");
    var choiceOptions = root.querySelector("[data-choice-options]");
    var taskDrawer = root.querySelector("[data-task-drawer]");
    var taskDrawerBody = root.querySelector("[data-task-drawer-body]");
    var taskDrawerToggle = root.querySelector("[data-task-drawer-toggle]");
    var taskDrawerClose = root.querySelector("[data-task-drawer-close]");
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
    var screenRecorder = null;
    var screenRecordingStream = null;
    var screenRecordingChunks = [];
    var activeScreenRecordingId = null;
    var screenRecordingSegmentTimer = null;
    var screenRecordingStopMode = "";
    var screenRecordingFinalStopRequested = false;
    var screenRecordingSegmentIndex = 0;
    var participantStreams = new Map();
    var remoteVideoPublications = new Map();
    var participantByIdentity = new Map();
    var spotlightParticipantId = null;
    var teacherIdentity = null;
    var taskDrawerCollapsed = false;
    var SCREEN_RECORDING_SEGMENT_MS = 10 * 60 * 1000;

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
            active: "进行中",
            completed: "已完成",
            failed: "失败"
        };
        return labels[status] || "未开始";
    }

    function recordingTypeLabel(recordingType) {
        return recordingType === "screen" ? "录屏" : "录音";
    }

    function recordingListFromSnapshot() {
        var recordings = Array.isArray(snapshot.recordings) ? snapshot.recordings.slice() : [];
        if (snapshot.recording && !recordings.some(function (recording) {
            return String(recording.id) === String(snapshot.recording.id);
        })) {
            recordings.unshift(snapshot.recording);
        }
        return recordings.filter(Boolean);
    }

    function latestRecordingByType(recordingType) {
        var recordings = recordingListFromSnapshot();
        for (var i = 0; i < recordings.length; i += 1) {
            if ((recordings[i].recording_type || "") === recordingType) {
                return recordings[i];
            }
        }
        return null;
    }

    function upsertRecording(recording) {
        if (!recording || !recording.id) {
            return;
        }
        var recordings = recordingListFromSnapshot();
        var updated = false;
        recordings = recordings.map(function (item) {
            if (String(item.id) === String(recording.id)) {
                updated = true;
                return Object.assign({}, item, recording);
            }
            return item;
        });
        if (!updated) {
            recordings.unshift(recording);
        }
        snapshot.recordings = recordings;
        snapshot.recording = recordings[0] || recording;
        upsertRecordingHistory(recording);
    }

    function recordingFileSizeLabel(fileSize) {
        var size = Number(fileSize || 0);
        if (!size || Number.isNaN(size)) {
            return "";
        }
        if (size >= 1024 * 1024) {
            return Math.round(size / 1024 / 1024) + " MB";
        }
        if (size >= 1024) {
            return Math.round(size / 1024) + " KB";
        }
        return size + " B";
    }

    function recordingHistorySortValue(recording) {
        var value = recording && (recording.ended_at || recording.started_at || "");
        var date = value ? new Date(value) : null;
        if (!date || Number.isNaN(date.getTime())) {
            return Date.now();
        }
        return date.getTime();
    }

    function buildRecordingHistoryLabel(recording) {
        var labelTime = formatDateTime(recording.ended_at || recording.started_at) || "刚刚";
        var label = labelTime + " · " + recordingTypeLabel(recording.recording_type);
        label += " · " + recordingStatusLabel(recording.status);
        var sizeLabel = recordingFileSizeLabel(recording.file_size);
        if (sizeLabel) {
            label += " · " + sizeLabel;
        }
        return label;
    }

    function sortRecordingHistory() {
        if (!recordingHistoryList) {
            return;
        }
        Array.from(recordingHistoryList.children)
            .sort(function (a, b) {
                return Number(b.dataset.recordingSortAt || 0) - Number(a.dataset.recordingSortAt || 0);
            })
            .forEach(function (item) {
                recordingHistoryList.appendChild(item);
            });
    }

    function upsertRecordingHistory(recording) {
        if (!recordingHistoryList || !recording || !recording.id) {
            return;
        }
        var empty = recordingHistoryList.querySelector("span");
        if (empty) {
            empty.remove();
        }

        var selector = '[data-recording-history-id="' + String(recording.id) + '"]';
        var existing = recordingHistoryList.querySelector(selector);
        var shouldLink = Boolean(recording.download_url && recording.status === "completed");
        var item = existing;
        if (!item || (shouldLink && item.tagName !== "A") || (!shouldLink && item.tagName === "A")) {
            item = document.createElement(shouldLink ? "a" : "div");
            if (existing) {
                existing.replaceWith(item);
            } else {
                recordingHistoryList.prepend(item);
            }
        }
        item.className = "live-classroom-recording-history__item is-" + (recording.status || "unknown");
        item.dataset.recordingHistoryId = String(recording.id);
        item.dataset.recordingSortAt = String(recordingHistorySortValue(recording));
        item.textContent = buildRecordingHistoryLabel(recording);
        if (shouldLink) {
            item.href = recording.download_url;
        }
        sortRecordingHistory();
    }

    function formatDateTime(value) {
        if (!value) {
            return "";
        }
        var date = new Date(value);
        if (Number.isNaN(date.getTime())) {
            return "";
        }
        return date.toLocaleString("zh-CN", { hour12: false });
    }

    function appendRecordingRow(recordingType, transientRecording) {
        var recording = transientRecording && transientRecording.recording_type === recordingType
            ? transientRecording
            : latestRecordingByType(recordingType);
        var row = document.createElement("div");
        row.className = "live-classroom-recording__row";

        var label = document.createElement("strong");
        label.textContent = recordingTypeLabel(recordingType);
        row.appendChild(label);

        row.appendChild(document.createTextNode("：" + recordingStatusLabel(recording && recording.status)));

        if (recording && recording.download_url) {
            row.appendChild(document.createTextNode(" · "));
            var link = document.createElement("a");
            link.href = recording.download_url;
            link.textContent = "下载文件";
            row.appendChild(link);
        }
        if (recording && recording.expires_at && recording.is_download_available) {
            var expires = formatDateTime(recording.expires_at);
            if (expires) {
                row.appendChild(document.createTextNode(" · " + expires + " 失效"));
            }
        }
        if (recording && recording.error_message) {
            var error = document.createElement("div");
            error.className = "live-classroom-recording__error";
            error.textContent = recording.error_message;
            row.appendChild(error);
        }
        recordingStatusEl.appendChild(row);
    }

    function setRecordingStatus(recording, recordingType) {
        if (!recordingStatusEl) {
            return;
        }
        var transientRecording = null;
        if (recording && recording.id) {
            upsertRecording(recording);
        } else if (recording && recordingType) {
            transientRecording = Object.assign({ recording_type: recordingType }, recording);
        }
        recordingStatusEl.innerHTML = "";
        appendRecordingRow("screen", transientRecording);
        if (
            latestRecordingByType("audio")
            || (transientRecording && transientRecording.recording_type === "audio")
        ) {
            appendRecordingRow("audio", transientRecording);
        }
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

    function screenCaptureVideoConstraints() {
        return {
            displaySurface: "monitor",
            width: { ideal: 1280, max: 1280 },
            height: { ideal: 720, max: 720 },
            frameRate: { ideal: 4, max: 5 }
        };
    }

    function screenShareVideoConstraints() {
        return {
            displaySurface: "monitor",
            width: { ideal: 1920, max: 2560 },
            height: { ideal: 1080, max: 1440 },
            frameRate: { ideal: 10, max: 12 }
        };
    }

    function screenSharePublishOptions(LiveKit, source) {
        return {
            source: source,
            simulcast: false,
            degradationPreference: "maintain-resolution",
            videoEncoding: {
                maxBitrate: 3500000,
                maxFramerate: 12
            },
            screenShareEncoding: {
                maxBitrate: 3500000,
                maxFramerate: 12
            }
        };
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

    async function readJsonResponse(response, fallbackMessage) {
        var text = await response.text();
        if (!text) {
            return {};
        }
        try {
            return JSON.parse(text);
        } catch (error) {
            throw new Error(fallbackMessage || "服务器返回了非 JSON 响应，请查看后端日志。");
        }
    }

    function activityTypeLabel(activityType) {
        if (activityType === "true_false") {
            return "判断题";
        }
        if (activityType === "single_choice") {
            return "选择题";
        }
        return "课堂任务";
    }

    function activityOptions(activity) {
        return activity && activity.options && typeof activity.options === "object" ? activity.options : {};
    }

    function answerLabel(activity, answer) {
        var options = activityOptions(activity);
        if (!answer) {
            return "";
        }
        if (Object.prototype.hasOwnProperty.call(options, answer)) {
            return answer + "：" + options[answer];
        }
        return answer;
    }

    function activityHistoryGroupLabel(activity) {
        var value = activity && activity.published_at ? activity.published_at : "";
        var date = value ? new Date(value) : null;
        if (!date || Number.isNaN(date.getTime())) {
            return "未分组";
        }
        return date.toLocaleDateString("zh-CN", {
            year: "numeric",
            month: "2-digit",
            day: "2-digit"
        });
    }

    function activityHistoryTimeLabel(activity) {
        var value = activity && activity.published_at ? activity.published_at : "";
        var date = value ? new Date(value) : null;
        if (!date || Number.isNaN(date.getTime())) {
            return "";
        }
        return date.toLocaleTimeString("zh-CN", {
            hour12: false,
            hour: "2-digit",
            minute: "2-digit"
        });
    }

    function setActivityState(activity, summary, history, latestResponse, autoOpenDrawer) {
        snapshot.current_activity = activity || null;
        if (summary !== undefined) {
            snapshot.activity_summary = summary || null;
        }
        if (Array.isArray(history)) {
            snapshot.activity_history = history;
        }
        if (latestResponse !== undefined) {
            snapshot.latest_activity_response = latestResponse || null;
        }
        renderActivitySummary();
        renderTaskDrawer(Boolean(autoOpenDrawer));
    }

    function renderActivitySummary() {
        if (!activitySummaryEl) {
            return;
        }
        var activity = snapshot.current_activity;
        var summary = snapshot.activity_summary;
        activitySummaryEl.innerHTML = "";
        var title = document.createElement("div");
        title.className = "live-classroom-task-summary__title";
        title.textContent = activity ? "当前任务：" + activity.title : "当前没有课堂任务";
        activitySummaryEl.appendChild(title);
        if (!activity || !summary) {
            return;
        }
        var meta = document.createElement("div");
        meta.className = "live-classroom-task-summary__meta";
        meta.textContent = activityTypeLabel(activity.activity_type) + " · 已提交 " + (summary.submitted_count || 0) + "/" + (summary.total_students || 0);
        activitySummaryEl.appendChild(meta);

        var counts = document.createElement("div");
        counts.className = "live-classroom-task-summary__counts";
        var options = activityOptions(activity);
        Object.keys(options).forEach(function (key) {
            var item = document.createElement("span");
            item.textContent = key + " " + (summary.answer_counts && summary.answer_counts[key] ? summary.answer_counts[key] : 0);
            counts.appendChild(item);
        });
        activitySummaryEl.appendChild(counts);

        var students = document.createElement("div");
        students.className = "live-classroom-task-summary__students";
        (summary.students || []).forEach(function (student) {
            var row = document.createElement("div");
            row.className = "live-classroom-task-summary__student";
            var answer = student.latest_answer ? answerLabel(activity, student.latest_answer) : "未提交";
            row.textContent = student.student_name + " · " + answer;
            students.appendChild(row);
        });
        activitySummaryEl.appendChild(students);
    }

    function renderTaskDrawer(autoOpen) {
        if (!taskDrawer || !taskDrawerBody) {
            return;
        }
        var activity = snapshot.current_activity;
        var history = Array.isArray(snapshot.activity_history) ? snapshot.activity_history : [];
        if (!activity && !history.length) {
            taskDrawer.hidden = true;
            if (taskDrawerToggle) {
                taskDrawerToggle.hidden = true;
            }
            return;
        }
        if (taskDrawerToggle) {
            taskDrawerToggle.hidden = false;
        }
        if (autoOpen) {
            taskDrawerCollapsed = false;
        }
        taskDrawer.hidden = taskDrawerCollapsed;
        taskDrawerBody.innerHTML = "";

        if (activity) {
            var current = document.createElement("section");
            current.className = "live-classroom-task-current";
            var eyebrow = document.createElement("div");
            eyebrow.className = "live-classroom-task-current__eyebrow";
            eyebrow.textContent = activityTypeLabel(activity.activity_type);
            var title = document.createElement("h3");
            title.textContent = activity.title;
            var prompt = document.createElement("div");
            prompt.className = "live-classroom-task-current__prompt";
            prompt.textContent = activity.prompt_text || "";
            current.appendChild(eyebrow);
            current.appendChild(title);
            current.appendChild(prompt);

            var form = document.createElement("form");
            form.className = "live-classroom-task-answer";
            var options = activityOptions(activity);
            Object.keys(options).forEach(function (key) {
                var label = document.createElement("label");
                var input = document.createElement("input");
                input.type = "radio";
                input.name = "answer";
                input.value = key;
                label.appendChild(input);
                label.appendChild(document.createTextNode(key + "：" + options[key]));
                form.appendChild(label);
            });
            var latest = snapshot.latest_activity_response;
            if (latest && String(latest.activity_id) === String(activity.id)) {
                var latestLabel = document.createElement("div");
                latestLabel.className = "live-classroom-task-answer__latest";
                latestLabel.textContent = "已提交：" + answerLabel(activity, latest.answer) + " · 第 " + latest.attempt_no + " 次";
                form.appendChild(latestLabel);
            }
            var submit = document.createElement("button");
            submit.className = "login-button live-classroom-primary-button";
            submit.type = "submit";
            submit.textContent = "提交答案";
            form.appendChild(submit);
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                submitActivityAnswer(activity, form);
            });
            current.appendChild(form);
            taskDrawerBody.appendChild(current);
        } else {
            var empty = document.createElement("div");
            empty.className = "live-classroom-task-empty";
            empty.textContent = "暂无课堂任务";
            taskDrawerBody.appendChild(empty);
        }

        var pastActivities = history.filter(function (item) {
            return !activity || String(item.id) !== String(activity.id);
        });
        if (pastActivities.length) {
            var details = document.createElement("details");
            details.className = "live-classroom-task-history";
            var summary = document.createElement("summary");
            summary.textContent = "历史任务 " + pastActivities.length;
            details.appendChild(summary);

            var grouped = new Map();
            pastActivities.forEach(function (item) {
                var groupLabel = activityHistoryGroupLabel(item);
                if (!grouped.has(groupLabel)) {
                    grouped.set(groupLabel, []);
                }
                grouped.get(groupLabel).push(item);
            });
            grouped.forEach(function (items, groupLabel) {
                var group = document.createElement("details");
                group.className = "live-classroom-task-history__group";
                var groupSummary = document.createElement("summary");
                groupSummary.textContent = groupLabel + " · " + items.length + " 题";
                group.appendChild(groupSummary);
                items.forEach(function (item) {
                    var row = document.createElement("details");
                    row.className = "live-classroom-task-history__item";
                    var rowSummary = document.createElement("summary");
                    var time = activityHistoryTimeLabel(item);
                    rowSummary.textContent = (time ? time + " · " : "") + activityTypeLabel(item.activity_type) + " · " + item.title;
                    row.appendChild(rowSummary);

                    var prompt = document.createElement("div");
                    prompt.className = "live-classroom-task-history__prompt";
                    prompt.textContent = item.prompt_text || "";
                    row.appendChild(prompt);

                    var latestResponse = item.latest_response || null;
                    var answer = document.createElement("div");
                    answer.className = "live-classroom-task-history__answer";
                    answer.textContent = latestResponse
                        ? "你的选择：" + answerLabel(item, latestResponse.answer) + (latestResponse.attempt_no ? " · 第 " + latestResponse.attempt_no + " 次" : "")
                        : "你未提交这道题";
                    row.appendChild(answer);

                    var options = document.createElement("div");
                    options.className = "live-classroom-task-history__options";
                    Object.keys(activityOptions(item)).forEach(function (key) {
                        var option = document.createElement("div");
                        option.className = "live-classroom-task-history__option";
                        if (latestResponse && String(latestResponse.answer) === String(key)) {
                            option.className += " is-selected";
                        }
                        option.textContent = key + "：" + activityOptions(item)[key];
                        options.appendChild(option);
                    });
                    row.appendChild(options);
                    group.appendChild(row);
                });
                details.appendChild(group);
            });
            taskDrawerBody.appendChild(details);
        }
    }

    function updateHistoryResponse(response) {
        if (!response || !response.activity_id || !Array.isArray(snapshot.activity_history)) {
            return;
        }
        snapshot.activity_history.forEach(function (item) {
            if (String(item.id) === String(response.activity_id)) {
                item.latest_response = response;
            }
        });
    }

    async function refreshActivityState() {
        if (!activityUrl) {
            return;
        }
        try {
            var response = await csrfFetch(activityUrl, { method: "GET", headers: {} });
            var payload = await readJsonResponse(response, "课堂任务接口返回异常。");
            if (!response.ok) {
                throw new Error(payload.error || "课堂任务加载失败。");
            }
            setActivityState(payload.activity || null, payload.summary, payload.activity_history || [], payload.latest_response, false);
        } catch (error) {
            setStatus(error.message || "课堂任务加载失败。");
        }
    }

    async function submitActivityAnswer(activity, form) {
        var checked = form.querySelector('input[name="answer"]:checked');
        if (!checked) {
            setStatus("请先选择答案。");
            return;
        }
        try {
            var response = await csrfFetch(activityUrl + "/" + activity.id + "/responses", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ answer: checked.value })
            });
            var payload = await readJsonResponse(response, "提交答案接口返回异常。");
            if (!response.ok) {
                throw new Error(payload.error || "提交答案失败。");
            }
            snapshot.latest_activity_response = payload.response || null;
            updateHistoryResponse(snapshot.latest_activity_response);
            renderTaskDrawer(false);
            setStatus("答案已提交。");
        } catch (error) {
            setStatus(error.message || "提交答案失败。");
        }
    }

    function setActivityDialogOpen(open) {
        if (activityDialog) {
            activityDialog.hidden = !open;
        }
        if (open) {
            setActivityDialogStatus("");
        }
    }

    function setActivityDialogStatus(message, isError) {
        if (!activityDialogStatus) {
            return;
        }
        activityDialogStatus.hidden = !message;
        activityDialogStatus.textContent = message || "";
        activityDialogStatus.classList.toggle("is-error", Boolean(isError));
    }

    function updateActivityFormForType() {
        if (!activityTypeSelect || !correctAnswerSelect) {
            return;
        }
        var type = activityTypeSelect.value;
        if (choiceOptions) {
            choiceOptions.hidden = type === "true_false";
        }
        correctAnswerSelect.innerHTML = "";
        var empty = document.createElement("option");
        empty.value = "";
        empty.textContent = "暂不设置";
        correctAnswerSelect.appendChild(empty);
        if (type === "true_false") {
            [
                ["true", "正确"],
                ["false", "错误"]
            ].forEach(function (item) {
                var option = document.createElement("option");
                option.value = item[0];
                option.textContent = item[1];
                correctAnswerSelect.appendChild(option);
            });
            return;
        }
        ["A", "B", "C", "D"].forEach(function (key) {
            var option = document.createElement("option");
            option.value = key;
            option.textContent = key;
            correctAnswerSelect.appendChild(option);
        });
    }

    async function publishActivityFromForm(event) {
        event.preventDefault();
        if (!activityForm || !activityUrl) {
            return;
        }
        var formData = new FormData(activityForm);
        var activityType = String(formData.get("activity_type") || "single_choice");
        var options = {};
        if (activityType === "true_false") {
            options = { true: "正确", false: "错误" };
        } else {
            activityForm.querySelectorAll("[data-option-key]").forEach(function (input) {
                var key = input.dataset.optionKey || "";
                var value = String(input.value || "").trim();
                if (key && value) {
                    options[key] = value;
                }
            });
        }
        var title = formData.get("title") || "";
        var promptText = String(formData.get("prompt_text") || "").trim();
        var promptFile = promptFileInput && promptFileInput.files && promptFileInput.files.length ? promptFileInput.files[0] : null;
        if (!promptText && !promptFile) {
            setActivityDialogStatus("请粘贴题面或上传文本文件。", true);
            setStatus("请粘贴题面或上传文本文件。");
            return;
        }
        if (activityType !== "true_false" && Object.keys(options).length < 2) {
            setActivityDialogStatus("选择题至少需要填写 2 个选项。上传文件只负责识别题面文本，选项需要在 A/B/C/D 中填写。", true);
            setStatus("选择题至少需要填写 2 个选项。");
            return;
        }
        setActivityDialogStatus(promptFile ? "正在上传并识别题面文件..." : "正在发布课堂任务...", false);
        if (activitySubmitButton) {
            activitySubmitButton.disabled = true;
        }
        try {
            var requestOptions;
            if (promptFile) {
                var uploadData = new FormData();
                uploadData.append("activity_type", activityType);
                uploadData.append("title", title);
                uploadData.append("prompt_text", promptText);
                uploadData.append("options", JSON.stringify(options));
                uploadData.append("correct_answer", formData.get("correct_answer") || "");
                uploadData.append("prompt_file", promptFile);
                requestOptions = {
                    method: "POST",
                    headers: {},
                    body: uploadData
                };
            } else {
                requestOptions = {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                    activity_type: activityType,
                    title: title,
                    prompt_text: promptText,
                    options: options,
                    correct_answer: formData.get("correct_answer") || ""
                })
                };
            }
            var response = await csrfFetch(activityUrl, requestOptions);
            var payload = await readJsonResponse(response, "布置课堂任务接口返回异常。");
            if (!response.ok) {
                throw new Error(payload.error || "布置课堂任务失败。");
            }
            setActivityState(payload.activity || null, payload.summary, payload.activity_history || [], null, false);
            activityForm.reset();
            updateActivityFormForType();
            setActivityDialogStatus("");
            setActivityDialogOpen(false);
            setStatus("课堂任务已发布。");
        } catch (error) {
            setActivityDialogStatus(error.message || "布置课堂任务失败。", true);
            setStatus(error.message || "布置课堂任务失败。");
        } finally {
            if (activitySubmitButton) {
                activitySubmitButton.disabled = false;
            }
        }
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

    function isSpotlightIdentity(identity) {
        var participant = participantByIdentity.get(identity);
        return Boolean(
            participant
            && participant.role === "student"
            && String(participant.id) === String(spotlightParticipantId)
        );
    }

    function shouldSubscribeToRemote(identity) {
        var participant = participantByIdentity.get(identity);
        if (role === "teacher") {
            return Boolean(isSpotlightIdentity(identity) && participant && participant.screen_state === "sharing");
        }
        return Boolean(role === "student" && participant && participant.role === "teacher");
    }

    function setPublicationSubscribed(publication, subscribed) {
        if (publication && typeof publication.setSubscribed === "function") {
            publication.setSubscribed(Boolean(subscribed));
        }
    }

    function requestHighQualityVideo(publication) {
        var LiveKit = window.LivekitClient || window.LiveKitClient || {};
        if (publication && typeof publication.setVideoQuality === "function" && LiveKit.VideoQuality) {
            publication.setVideoQuality(LiveKit.VideoQuality.HIGH);
        }
        if (publication && typeof publication.setVideoDimensions === "function") {
            publication.setVideoDimensions({ width: 1920, height: 1080 });
        }
        if (publication && typeof publication.setVideoFPS === "function") {
            publication.setVideoFPS(12);
        }
    }

    function isVideoPublication(publication) {
        if (!publication) {
            return false;
        }
        return !publication.kind
            || publication.kind === "video"
            || publication.kind === "kind_video"
            || publication.trackKind === "video";
    }

    function rememberRemoteVideoPublication(identity, publication) {
        if (!identity || !isVideoPublication(publication)) {
            return;
        }
        remoteVideoPublications.set(identity, publication);
    }

    function rememberRemoteParticipantPublications(participant) {
        if (!participant || !participant.identity) {
            return;
        }
        var identity = participant.identity;
        if (participant.videoTrackPublications && typeof participant.videoTrackPublications.forEach === "function") {
            participant.videoTrackPublications.forEach(function (publication) {
                rememberRemoteVideoPublication(identity, publication);
            });
            return;
        }
        if (participant.trackPublications && typeof participant.trackPublications.forEach === "function") {
            participant.trackPublications.forEach(function (publication) {
                rememberRemoteVideoPublication(identity, publication);
            });
        }
    }

    function syncRemoteSubscriptions() {
        if (!room) {
            return;
        }
        if (room.remoteParticipants && typeof room.remoteParticipants.forEach === "function") {
            room.remoteParticipants.forEach(function (participant) {
                rememberRemoteParticipantPublications(participant);
            });
        }
        remoteVideoPublications.forEach(function (publication, identity) {
            var shouldSubscribe = shouldSubscribeToRemote(identity);
            setPublicationSubscribed(publication, shouldSubscribe);
            if (shouldSubscribe) {
                requestHighQualityVideo(publication);
            }
            if (!shouldSubscribe) {
                participantStreams.delete(identity);
            }
        });
    }

    function ensureRemoteVideoSubscription(identity) {
        var publication = remoteVideoPublications.get(identity);
        if (!publication) {
            syncRemoteSubscriptions();
            publication = remoteVideoPublications.get(identity);
        }
        if (publication) {
            setPublicationSubscribed(publication, shouldSubscribeToRemote(identity));
            if (shouldSubscribeToRemote(identity)) {
                requestHighQualityVideo(publication);
            }
        }
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
            card.classList.add("is-" + (participant.screen_state || "none"));
            if (String(participant.id) === String(spotlightParticipantId)) {
                card.classList.add("is-spotlight");
            }

            var infoRow = document.createElement("div");
            infoRow.className = "live-classroom-participant__info";

            var name = document.createElement("div");
            name.className = "live-classroom-participant__name";
            name.textContent = participantLabel(participant);

            var meta = document.createElement("div");
            meta.className = "live-classroom-participant__meta";
            meta.textContent = screenStateLabel(participant.screen_state);

            var stateDot = document.createElement("span");
            stateDot.className = "live-classroom-participant__dot";
            stateDot.setAttribute("aria-hidden", "true");

            var actions = document.createElement("div");
            actions.className = "live-classroom-participant__actions";
            var spotlightButton = document.createElement("button");
            spotlightButton.type = "button";
            spotlightButton.textContent = String(participant.id) === String(spotlightParticipantId) ? "投屏中" : "投屏";
            spotlightButton.disabled = participant.screen_state !== "sharing";
            spotlightButton.addEventListener("click", function () {
                spotlightParticipantId = participant.id;
                sendWs("teacher_view", {
                    view_mode: "spotlight_student",
                    spotlight_participant_id: participant.id
                });
                syncRemoteSubscriptions();
                if (!setMainStream(participant.livekit_identity)) {
                    setStatus("正在接收该学生的高清投屏...");
                }
                renderParticipants(students);
            });
            actions.appendChild(spotlightButton);

            card.addEventListener("contextmenu", function (event) {
                event.preventDefault();
                spotlightButton.click();
            });

            infoRow.appendChild(stateDot);
            infoRow.appendChild(name);
            infoRow.appendChild(meta);
            infoRow.appendChild(actions);
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
        if (!stream) {
            ensureRemoteVideoSubscription(identity);
            stream = participantStreams.get(identity);
        }
        showStreamOnMain(stream);
        if (!stream) {
            var participant = participantByIdentity.get(identity);
            if (participant && participant.role === "teacher") {
                setMainEmpty("正在接收老师屏幕", "老师共享屏幕后会显示在这里。");
            } else {
                setMainEmpty("正在接收投屏", "学生已共享屏幕，正在建立高清画面。");
            }
        }
        return Boolean(stream);
    }

    function showDefaultMainStream() {
        if (isLocalScreenSharing()) {
            setMainEmpty(localSharingLabel(), localSharingText());
            showStreamOnMain(null);
            return;
        }
        if (role === "student" && teacherIdentity) {
            setMainStream(teacherIdentity);
            return;
        }
        setMainEmpty(role === "teacher" ? "主屏幕" : "课堂主屏幕", role === "teacher" ? "点击上方“共享老师屏幕”，或从右侧选择学生投屏。" : "老师共享屏幕后会显示在这里。");
        showStreamOnMain(null);
    }

    function openStageFullscreen() {
        if (!mainStage) {
            return;
        }
        var requestFullscreen = mainStage.requestFullscreen
            || mainStage.webkitRequestFullscreen
            || mainStage.msRequestFullscreen;
        if (!requestFullscreen) {
            setStatus("当前浏览器不支持全屏展示。");
            return;
        }
        var result = requestFullscreen.call(mainStage);
        if (result && typeof result.catch === "function") {
            result.catch(function () {
                setStatus("浏览器未允许全屏展示。");
            });
        }
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
        spotlightParticipantId = session.view_mode === "spotlight_student" ? session.spotlight_participant_id : null;
        updateParticipantMaps(participants);
        syncRemoteSubscriptions();
        setRecordingStatus(snapshot.recording);
        renderActivitySummary();
        renderTaskDrawer(false);
        renderParticipants(participants);
        if (session.view_mode === "normal") {
            showDefaultMainStream();
        }
        if (session.view_mode === "spotlight_student" && spotlightParticipantId) {
            var spotlight = participants.find(function (participant) {
                return String(participant.id) === String(spotlightParticipantId);
            });
            if (spotlight) {
                if (spotlight.screen_state === "sharing") {
                    setMainStream(spotlight.livekit_identity);
                } else {
                    showStreamOnMain(null);
                    setMainEmpty("学生未共享", "该学生当前没有正在共享的屏幕。");
                }
            }
        }
    }

    async function getToken() {
        var response = await csrfFetch(tokenUrl, { method: "POST", headers: {} });
        var payload = await readJsonResponse(response, "LiveKit token 接口返回异常。");
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
        room = new LiveKit.Room({
            adaptiveStream: true,
            dynacast: true
        });
        if (LiveKit.RoomEvent.TrackPublished) {
            room.on(LiveKit.RoomEvent.TrackPublished, function (publication, participant) {
                rememberRemoteVideoPublication(participant.identity, publication);
                var shouldSubscribe = shouldSubscribeToRemote(participant.identity);
                setPublicationSubscribed(publication, shouldSubscribe);
                if (shouldSubscribe) {
                    requestHighQualityVideo(publication);
                }
            });
        }
        room.on(LiveKit.RoomEvent.TrackSubscribed, function (track, publication, participant) {
            if (!track || track.kind !== "video") {
                return;
            }
            if (!shouldSubscribeToRemote(participant.identity)) {
                setPublicationSubscribed(publication, false);
                return;
            }
            rememberRemoteVideoPublication(participant.identity, publication);
            requestHighQualityVideo(publication);
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
        syncRemoteSubscriptions();
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
                video: screenShareVideoConstraints(),
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
            await currentRoom.localParticipant.publishTrack(
                videoTrack,
                screenSharePublishOptions(LiveKit, screenShareSource)
            );
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

    function chooseVideoMimeType() {
        var types = [
            "video/webm;codecs=vp9,opus",
            "video/webm;codecs=vp8,opus",
            "video/webm;codecs=vp9",
            "video/webm;codecs=vp8",
            "video/webm",
            "video/mp4"
        ];
        for (var i = 0; i < types.length; i += 1) {
            if (window.MediaRecorder && MediaRecorder.isTypeSupported(types[i])) {
                return types[i];
            }
        }
        return "";
    }

    function videoExtension(mimeType) {
        if ((mimeType || "").indexOf("mp4") !== -1) {
            return "mp4";
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
                    setRecordingStatus();
                });
            });
            var response = await csrfFetch(recordingUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "start_audio" })
            });
            var payload = await readJsonResponse(response, "录音启动接口返回异常。");
            if (!response.ok) {
                throw new Error(payload.error || "录音启动失败。");
            }
            upsertRecording(payload.recording || null);
            activeAudioRecordingId = payload.recording ? payload.recording.id : null;
            setRecordingStatus(payload.recording || null);
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
            setRecordingStatus();
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
        setRecordingStatus({ status: "stopping" }, "audio");
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
        var payload = await readJsonResponse(response, "录音上传接口返回异常。");
        if (!response.ok) {
            throw new Error(payload.error || "录音文件上传失败。");
        }
        activeAudioRecordingId = null;
        upsertRecording(payload.recording || null);
        setRecordingStatus(payload.recording || null);
        setStatus("录音已停止，文件已生成。");
    }

    function stopBrowserAudioRecording() {
        if (!audioRecorder || audioRecorder.state !== "recording") {
            setStatus("当前没有正在进行的浏览器录音。");
            setRecordingStatus({
                status: "failed",
                error_message: "当前页面没有正在进行的录音；如果刷新过页面，浏览器里的临时录音数据已经丢失。请重新开始录音。",
            }, "audio");
            return;
        }
        setRecordingStatus({ status: "stopping" }, "audio");
        setStatus("正在停止录音并生成文件...");
        audioRecorder.stop();
    }

    async function requestRecordingAction(action) {
        var response = await csrfFetch(recordingUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: action })
        });
        var payload = await readJsonResponse(response, "录制接口返回异常。");
        if (!response.ok) {
            throw new Error(payload.error || "录制操作失败。");
        }
        upsertRecording(payload.recording || null);
        setRecordingStatus(payload.recording || null);
        return payload.recording || null;
    }

    function clearScreenRecordingSegmentTimer() {
        if (screenRecordingSegmentTimer) {
            window.clearTimeout(screenRecordingSegmentTimer);
            screenRecordingSegmentTimer = null;
        }
    }

    function cleanupScreenRecordingStream() {
        clearScreenRecordingSegmentTimer();
        if (screenRecordingStream) {
            screenRecordingStream.getTracks().forEach(function (track) {
                track.stop();
            });
        }
        screenRecordingStream = null;
        screenRecorder = null;
        screenRecordingChunks = [];
        activeScreenRecordingId = null;
        screenRecordingStopMode = "";
        screenRecordingFinalStopRequested = false;
        screenRecordingSegmentIndex = 0;
    }

    function isScreenRecordingStreamActive() {
        return Boolean(
            screenRecordingStream
            && screenRecordingStream.getVideoTracks().some(function (track) {
                return track.readyState === "live";
            })
        );
    }

    async function uploadBrowserScreenRecordingSegment(recordingId, chunks, mimeType, stopMode) {
        if (!recordingUrl || !recordingId) {
            return null;
        }
        setRecordingStatus({ status: "stopping" }, "screen");
        if (!chunks.length) {
            throw new Error("没有录到屏幕数据。");
        }
        var blob = new Blob(chunks, { type: mimeType || "video/webm" });
        var formData = new FormData();
        formData.append("action", "upload_screen");
        formData.append("recording_id", String(recordingId));
        formData.append("screen", blob, "classroom-screen." + videoExtension(blob.type || mimeType || ""));
        var response = await csrfFetch(recordingUrl, {
            method: "POST",
            headers: {},
            body: formData
        });
        var payload = await readJsonResponse(response, "录屏上传接口返回异常。");
        if (!response.ok) {
            throw new Error(payload.error || "录屏文件上传失败。");
        }
        upsertRecording(payload.recording || null);
        setRecordingStatus(payload.recording || null);
        if (stopMode === "rotate") {
            setStatus("录屏片段已保存，正在继续录屏。");
        } else {
            setStatus("录屏已停止，文件已生成。");
        }
        return payload.recording || null;
    }

    function stopCurrentScreenRecordingSegment(stopMode) {
        if (!screenRecorder || screenRecorder.state !== "recording") {
            return false;
        }
        clearScreenRecordingSegmentTimer();
        screenRecordingStopMode = stopMode;
        screenRecorder.stop();
        return true;
    }

    function scheduleScreenRecordingSegmentRotation() {
        clearScreenRecordingSegmentTimer();
        screenRecordingSegmentTimer = window.setTimeout(function () {
            if (!screenRecorder || screenRecorder.state !== "recording") {
                return;
            }
            setRecordingStatus({ status: "stopping" }, "screen");
            setStatus("正在保存本段录屏，录屏会自动继续。");
            stopCurrentScreenRecordingSegment("rotate");
        }, SCREEN_RECORDING_SEGMENT_MS);
    }

    async function startScreenRecordingSegment() {
        if (!screenRecordingStream || !isScreenRecordingStreamActive()) {
            throw new Error("录屏画面已停止，无法继续录制。");
        }
        var recording = await requestRecordingAction("start_screen");
        var recordingId = recording ? recording.id : null;
        if (!recordingId) {
            throw new Error("录屏记录创建失败。");
        }
        activeScreenRecordingId = recordingId;
        screenRecordingSegmentIndex += 1;
        screenRecordingChunks = [];
        var mimeType = chooseVideoMimeType();
        var recorder = mimeType ? new MediaRecorder(screenRecordingStream, { mimeType: mimeType }) : new MediaRecorder(screenRecordingStream);
        recorder.addEventListener("dataavailable", function (event) {
            if (event.data && event.data.size > 0) {
                screenRecordingChunks.push(event.data);
            }
        });
        recorder.addEventListener("stop", function () {
            var chunks = screenRecordingChunks.slice();
            var segmentRecordingId = recordingId;
            var segmentMimeType = recorder.mimeType || mimeType;
            var stopMode = screenRecordingFinalStopRequested ? "final" : (screenRecordingStopMode || "final");
            screenRecordingChunks = [];
            screenRecorder = null;
            activeScreenRecordingId = null;
            screenRecordingStopMode = "";
            uploadBrowserScreenRecordingSegment(segmentRecordingId, chunks, segmentMimeType, stopMode).then(function () {
                if (stopMode === "rotate" && !screenRecordingFinalStopRequested && isScreenRecordingStreamActive()) {
                    startScreenRecordingSegment().catch(function (error) {
                        cleanupScreenRecordingStream();
                        setRecordingStatus({
                            status: "failed",
                            error_message: error && error.message ? error.message : "录屏续录失败。",
                        }, "screen");
                        setStatus(error && error.message ? error.message : "录屏续录失败。");
                    });
                    return;
                }
                cleanupScreenRecordingStream();
            }).catch(function (error) {
                cleanupScreenRecordingStream();
                setStatus(error && error.message ? error.message : "录屏文件上传失败。");
                setRecordingStatus({
                    status: "failed",
                    error_message: error && error.message ? error.message : "录屏文件上传失败。",
                }, "screen");
            });
        });
        screenRecorder = recorder;
        recorder.start(1000);
        scheduleScreenRecordingSegmentRotation();
        setStatus(
            screenRecordingSegmentIndex === 1
                ? "正在录屏。系统每 10 分钟自动保存一个文件，录制整个屏幕和老师麦克风。"
                : "正在继续录屏，第 " + screenRecordingSegmentIndex + " 段已开始。"
        );
    }

    async function startScreenRecording() {
        if (!recordingUrl) {
            setStatus("录屏接口未配置。");
            return;
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia || !window.MediaRecorder) {
            setStatus("当前浏览器不支持课堂录屏。");
            return;
        }
        if (screenRecorder && screenRecorder.state === "recording") {
            setStatus("录屏已经在进行中。");
            return;
        }
        var displayStream = null;
        var microphoneStream = null;
        var recordingStream = null;
        try {
            setRecordingStatus({ status: "starting" }, "screen");
            setStatus("请选择要录制的整个屏幕，并允许麦克风权限。");
            displayStream = await navigator.mediaDevices.getDisplayMedia({
                video: screenCaptureVideoConstraints(),
                audio: false
            });
            var videoTrack = displayStream.getVideoTracks()[0];
            var displaySurface = videoTrack && videoTrack.getSettings ? videoTrack.getSettings().displaySurface : "";
            if (!videoTrack) {
                throw new Error("没有选择可录制的屏幕。");
            }
            if (displaySurface && displaySurface !== "monitor") {
                throw new Error("录屏请选择整个屏幕，窗口或浏览器标签页会产生递归画面，不能用于课堂录制。");
            }
            microphoneStream = await navigator.mediaDevices.getUserMedia({
                audio: true,
                video: false
            });
            var microphoneTrack = microphoneStream.getAudioTracks()[0];
            if (!microphoneTrack) {
                throw new Error("没有获取到麦克风音频。");
            }
            recordingStream = new MediaStream([videoTrack, microphoneTrack]);
            videoTrack.addEventListener("ended", function () {
                if (screenRecorder && screenRecorder.state === "recording") {
                    screenRecordingFinalStopRequested = true;
                    setRecordingStatus({ status: "stopping" }, "screen");
                    setStatus("屏幕录制已从浏览器停止，正在生成最后一个文件...");
                    stopCurrentScreenRecordingSegment("final");
                }
            });
            screenRecordingStream = recordingStream;
            screenRecordingFinalStopRequested = false;
            screenRecordingSegmentIndex = 0;
            await startScreenRecordingSegment();
        } catch (error) {
            [displayStream, microphoneStream, recordingStream].forEach(function (stream) {
                if (!stream) {
                    return;
                }
                stream.getTracks().forEach(function (track) {
                    track.stop();
                });
            });
            cleanupScreenRecordingStream();
            setRecordingStatus({
                status: "failed",
                error_message: error && error.message ? error.message : "录屏启动失败。",
            }, "screen");
            setStatus(error && error.message ? error.message : "录屏启动失败。");
        }
    }

    async function stopScreenRecording() {
        if (!recordingUrl) {
            setStatus("录屏接口未配置。");
            return;
        }
        if (!screenRecorder || screenRecorder.state !== "recording") {
            if (screenRecordingStream) {
                screenRecordingFinalStopRequested = true;
                setRecordingStatus({ status: "stopping" }, "screen");
                setStatus("正在保存当前录屏片段，保存完成后会停止录屏。");
                return;
            }
            setStatus("当前没有正在进行的浏览器录屏。");
            setRecordingStatus({
                status: "failed",
                error_message: "当前页面没有正在进行的录屏；如果刷新过页面，浏览器里的临时录屏数据已经丢失。请重新开始录屏。",
            }, "screen");
            return;
        }
        screenRecordingFinalStopRequested = true;
        setRecordingStatus({ status: "stopping" }, "screen");
        setStatus("正在停止录屏并生成当前片段文件...");
        stopCurrentScreenRecordingSegment("final");
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
                upsertRecording(recording || null);
                setRecordingStatus(recording);
                setStatus(recording ? recordingTypeLabel(recording.recording_type) + "状态：" + recordingStatusLabel(recording.status) : "录制状态已更新。");
            } else if (message.event === "activity_published") {
                setActivityState(
                    message.payload && message.payload.activity ? message.payload.activity : null,
                    message.payload ? message.payload.summary : null,
                    message.payload && Array.isArray(message.payload.activity_history) ? message.payload.activity_history : [],
                    null,
                    role === "student"
                );
                setStatus("老师发布了新的课堂任务。");
                if (role === "teacher" || role === "student") {
                    refreshActivityState();
                }
            } else if (message.event === "activity_summary_updated") {
                if (
                    snapshot.current_activity
                    && message.payload
                    && String(message.payload.activity_id) === String(snapshot.current_activity.id)
                ) {
                    snapshot.activity_summary = message.payload.summary || null;
                    renderActivitySummary();
                }
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
    if (fullscreenButton) {
        fullscreenButton.addEventListener("click", openStageFullscreen);
    }
    document.querySelectorAll("[data-recording-action]").forEach(function (button) {
        button.addEventListener("click", function (event) {
            event.preventDefault();
            var action = button.dataset.recordingAction || "";
            if (action === "start_audio" || action === "start") {
                startBrowserAudioRecording();
            } else if (action === "stop_audio") {
                stopBrowserAudioRecording();
            } else if (action === "start_screen") {
                startScreenRecording();
            } else if (action === "stop_screen" || action === "stop") {
                stopScreenRecording();
            }
        });
    });
    root.querySelectorAll("[data-open-activity-dialog]").forEach(function (button) {
        button.addEventListener("click", function () {
            updateActivityFormForType();
            setActivityDialogOpen(true);
        });
    });
    root.querySelectorAll("[data-close-activity-dialog]").forEach(function (button) {
        button.addEventListener("click", function () {
            setActivityDialogOpen(false);
        });
    });
    if (activityTypeSelect) {
        activityTypeSelect.addEventListener("change", updateActivityFormForType);
        updateActivityFormForType();
    }
    if (activityForm) {
        activityForm.addEventListener("submit", publishActivityFromForm);
    }
    if (taskDrawerToggle) {
        taskDrawerToggle.addEventListener("click", function () {
            taskDrawerCollapsed = false;
            renderTaskDrawer(false);
        });
    }
    if (taskDrawerClose) {
        taskDrawerClose.addEventListener("click", function () {
            taskDrawerCollapsed = true;
            renderTaskDrawer(false);
        });
    }

    applySnapshot(snapshot);
    refreshActivityState();
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
