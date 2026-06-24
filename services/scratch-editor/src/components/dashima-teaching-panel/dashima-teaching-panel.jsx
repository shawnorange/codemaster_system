/* eslint-disable react/jsx-no-bind, react/jsx-no-literals */

import PropTypes from 'prop-types';
import React from 'react';
import {connect} from 'react-redux';

import {
    buildCodemasterProjectTitle,
    buildCodemasterSubmissionFormData,
    parseCodemasterLaunchParams
} from '../../lib/dashima-codemaster-submission';
import DashimaLearningEventRecorder from '../../lib/dashima-learning-event-recorder';
import DashimaLiveKitScreenMonitor from '../../lib/dashima-livekit-screen-monitor';

import styles from './dashima-teaching-panel.css';

const emptySnapshot = DashimaLearningEventRecorder.getSnapshot();

const profileText = snapshot => {
    const {profile, project} = snapshot;
    return [
        `编程风格：${profile.programmingStyle}`,
        `学习风格：${profile.learningStyle}`,
        `已观察事件：${profile.summary.totalEvents}`,
        `当前积木数：${project.blockCount}`
    ].join('\n');
};

const fallbackAssistantReply = (question, snapshot) => {
    const {profile, project} = snapshot;
    const lines = [
        `我先按现在的编辑过程看：你更像「${profile.programmingStyle}」，学习上偏「${profile.learningStyle}」。`,
        `当前作品大约有 ${project.blockCount} 个积木。`
    ];

    if (profile.summary.runCount === 0 && profile.summary.totalEdits >= 4) {
        lines.push('建议你先点一次绿旗，把已经搭好的结构跑一下，再决定下一步。');
    } else if (profile.summary.changeCount > profile.summary.createCount) {
        lines.push('你正在频繁调参数，可以先固定一个目标：角色要移动、说话、还是触发事件？');
    } else {
        lines.push('下一步可以按“事件触发 → 动作 → 判断条件 → 反馈效果”的顺序搭。');
    }

    if (question.indexOf('结构') !== -1 || question.indexOf('功能') !== -1 || question.indexOf('做') !== -1) {
        lines.push('我可以先帮你拆功能结构：先选一个触发积木，再接主要动作，最后补变量或判断。');
    }

    return lines.join('\n');
};

class DashimaTeachingPanel extends React.Component {
    constructor (props) {
        super(props);
        this.codemasterLaunch = parseCodemasterLaunchParams();
        this.state = {
            activeTab: this.codemasterLaunch.enabled ? 'submission' : 'profile',
            collapsed: false,
            snapshot: emptySnapshot,
            messages: [{
                role: 'assistant',
                text: '我是大师码 AI 编程助手。你可以问我下一步怎么搭、哪里可能有 bug，或者让我把功能拆成积木结构。'
            }],
            question: '',
            isAsking: false,
            monitorStatus: '待启动',
            monitorError: '',
            monitorMode: 'livekit',
            codemasterSaveStatus: this.codemasterLaunch.enabled ? '已连接大师码' : '未连接大师码',
            codemasterError: '',
            isSavingCodemaster: false,
            lastCodemasterSavedAt: '',
            codemasterSubmitted: false,
            codemasterConnectionInvalid: false
        };
        this.videoRef = React.createRef();
        this.handleSnapshot = this.handleSnapshot.bind(this);
        this.handleQuestionChange = this.handleQuestionChange.bind(this);
        this.handleAsk = this.handleAsk.bind(this);
        this.handleClearEvents = this.handleClearEvents.bind(this);
        this.handleStartScreenMonitor = this.handleStartScreenMonitor.bind(this);
        this.handleStopScreenMonitor = this.handleStopScreenMonitor.bind(this);
        this.handleSaveCodemasterDraft = this.handleSaveCodemasterDraft.bind(this);
        this.handleSubmitCodemasterProject = this.handleSubmitCodemasterProject.bind(this);
        this.handleReturnToCodemaster = this.handleReturnToCodemaster.bind(this);
    }

    componentDidMount () {
        this.unsubscribe = DashimaLearningEventRecorder.subscribe(this.handleSnapshot);
        DashimaLearningEventRecorder.recordProjectAction('teaching_panel_opened', {
            source: 'dashima_teaching_panel'
        });
        if (this.codemasterLaunch.enabled) {
            DashimaLearningEventRecorder.recordProjectAction('codemaster_submission_connected', {
                submissionId: this.codemasterLaunch.submissionId,
                questionId: this.codemasterLaunch.questionId,
                sessionId: this.codemasterLaunch.sessionId
            });
            this.codemasterAutosaveTimer = setInterval(() => {
                const canAutosave = !this.state.codemasterSubmitted &&
                    !this.state.isSavingCodemaster &&
                    !this.state.codemasterConnectionInvalid;
                if (canAutosave) {
                    this.saveCodemasterSubmission('draft', {auto: true});
                }
            }, this.codemasterLaunch.autosaveIntervalMs);
        }
    }

    componentWillUnmount () {
        if (this.unsubscribe) this.unsubscribe();
        if (this.codemasterAutosaveTimer) clearInterval(this.codemasterAutosaveTimer);
        this.handleStopScreenMonitor({silent: true});
    }

    handleSnapshot (snapshot) {
        this.setState({snapshot});
    }

    handleQuestionChange (event) {
        this.setState({question: event.target.value});
    }

    handleClearEvents () {
        DashimaLearningEventRecorder.clear();
    }

    async buildCodemasterProjectBlob () {
        if (!this.props.saveProjectSb3) {
            throw new Error('作品导出功能还没有准备好');
        }
        const content = await this.props.saveProjectSb3();
        if (content instanceof Blob) return content;
        return new Blob([content], {type: 'application/octet-stream'});
    }

    buildCodemasterAiSummary (snapshot) {
        return {
            profile: snapshot.profile,
            project: snapshot.project,
            latestEvents: Array.isArray(snapshot.recentEvents) ? snapshot.recentEvents.slice(-20) : []
        };
    }

    formatCodemasterError (errorCode, fallbackMessage) {
        if (errorCode === 'invalid_submission_token') {
            return '连接已失效，请回到 codeMaster 模考题目，重新点击“打开 Scratch 编程平台”。';
        }
        if (errorCode === 'method_not_allowed') {
            return '提交方式不正确，请刷新页面后重试。';
        }
        return fallbackMessage || errorCode || '保存失败，请稍后重试。';
    }

    async saveCodemasterSubmission (status, options) {
        const settings = options || {};
        if (!this.codemasterLaunch.enabled) {
            this.setState({
                codemasterSaveStatus: '未连接大师码',
                codemasterError: '请从大师码模考题目进入 Scratch 编程平台。'
            });
            return;
        }
        if (this.state.isSavingCodemaster) return;

        this.setState({
            isSavingCodemaster: true,
            codemasterError: '',
            codemasterSaveStatus: status === 'submitted' ? '正在提交作品' : (settings.auto ? '正在自动保存' : '正在保存草稿')
        });

        try {
            const snapshot = DashimaLearningEventRecorder.getSnapshot();
            const projectBlob = await this.buildCodemasterProjectBlob();
            const projectTitle = buildCodemasterProjectTitle(this.props.projectTitle, this.codemasterLaunch);
            const formData = buildCodemasterSubmissionFormData({
                token: this.codemasterLaunch.token,
                status,
                projectTitle,
                projectBlob,
                projectFileName: `codemaster-scratch-${this.codemasterLaunch.submissionId}.sb3`,
                snapshot,
                aiSummary: this.buildCodemasterAiSummary(snapshot)
            });
            const response = await fetch(this.codemasterLaunch.callbackUrl, {
                method: 'POST',
                body: formData
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.ok) {
                const error = new Error(this.formatCodemasterError(result.error, `保存失败：${response.status}`));
                error.code = result.error || '';
                throw error;
            }

            const savedAt = new Date().toLocaleString('zh-CN', {hour12: false});
            DashimaLearningEventRecorder.recordProjectAction(
                status === 'submitted' ? 'codemaster_submission_submitted' : 'codemaster_submission_saved',
                {
                    submissionId: this.codemasterLaunch.submissionId,
                    status,
                    auto: Boolean(settings.auto)
                }
            );
            this.setState({
                isSavingCodemaster: false,
                codemasterError: '',
                codemasterSaveStatus: status === 'submitted' ? '已提交到大师码' : '已保存到大师码',
                lastCodemasterSavedAt: savedAt,
                codemasterSubmitted: status === 'submitted' || this.state.codemasterSubmitted
            });
        } catch (error) {
            const connectionInvalid = error.code === 'invalid_submission_token';
            if (connectionInvalid && this.codemasterAutosaveTimer) {
                clearInterval(this.codemasterAutosaveTimer);
                this.codemasterAutosaveTimer = null;
            }
            DashimaLearningEventRecorder.recordProjectAction('codemaster_submission_failed', {
                submissionId: this.codemasterLaunch.submissionId,
                status,
                message: error.message
            });
            this.setState({
                isSavingCodemaster: false,
                codemasterSaveStatus: connectionInvalid ? '连接已失效' : '保存失败',
                codemasterError: error.message,
                codemasterConnectionInvalid: connectionInvalid || this.state.codemasterConnectionInvalid
            });
        }
    }

    handleSaveCodemasterDraft () {
        return this.saveCodemasterSubmission('draft');
    }

    handleSubmitCodemasterProject () {
        return this.saveCodemasterSubmission('submitted');
    }

    handleReturnToCodemaster () {
        if (this.codemasterLaunch.returnUrl && typeof window !== 'undefined') {
            window.location.href = this.codemasterLaunch.returnUrl;
        }
    }

    async handleAsk () {
        const question = this.state.question.trim() || '请根据我的编程过程，给我下一步建议。';
        const snapshot = this.state.snapshot;
        const userMessage = {role: 'user', text: question};

        this.setState(state => ({
            isAsking: true,
            question: '',
            messages: state.messages.concat(userMessage)
        }));

        DashimaLearningEventRecorder.recordProjectAction('ai_assistant_question', {
            question,
            profile: snapshot.profile,
            project: snapshot.project
        });

        let answer = '';
        try {
            if (window.DashimaAIAssistant && typeof window.DashimaAIAssistant.ask === 'function') {
                answer = await window.DashimaAIAssistant.ask({
                    question,
                    learningContext: snapshot
                });
            } else {
                const response = await fetch('/api/dashima/assistant', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        question,
                        learningContext: snapshot
                    })
                });
                if (response.ok) {
                    const json = await response.json();
                    answer = json.message || json.answer || '';
                }
            }
        } catch (error) {
            answer = '';
        }

        if (!answer) {
            answer = fallbackAssistantReply(question, snapshot);
        }

        DashimaLearningEventRecorder.recordProjectAction('ai_assistant_reply', {
            question,
            answer
        });

        this.setState(state => ({
            isAsking: false,
            messages: state.messages.concat({
                role: 'assistant',
                text: answer
            })
        }));
    }

    async handleStartScreenMonitor () {
        this.setState({
            monitorStatus: '正在连接 LiveKit',
            monitorError: '',
            monitorMode: 'livekit'
        });

        DashimaLearningEventRecorder.recordProjectAction('screen_monitor_start_requested', {
            source: 'dashima_teaching_panel',
            provider: 'livekit'
        });

        try {
            const result = await DashimaLiveKitScreenMonitor.start({
                learningContext: this.state.snapshot,
                previewElement: this.videoRef.current,
                onStatus: monitorStatus => this.setState({monitorStatus})
            });

            this.setState({
                monitorStatus: result.status || 'LiveKit 屏幕流已发布',
                monitorMode: result.mode || 'livekit'
            });
            DashimaLearningEventRecorder.recordProjectAction('screen_monitor_started', {
                mode: result.mode || 'livekit',
                roomName: result.roomName,
                participantIdentity: result.participantIdentity
            });
        } catch (error) {
            if (error.code === 'DASHIMA_LIVEKIT_NOT_CONFIGURED') {
                await this.startLocalPreview(error.message);
                return;
            }

            this.setState({
                monitorStatus: '未启动',
                monitorError: error.message
            });
            DashimaLearningEventRecorder.recordProjectAction('screen_monitor_failed', {
                message: error.message
            });
        }
    }

    async startLocalPreview (liveKitMessage) {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
            this.setState({
                monitorStatus: '未启动',
                monitorError: liveKitMessage
            });
            DashimaLearningEventRecorder.recordProjectAction('screen_monitor_failed', {
                message: liveKitMessage,
                provider: 'livekit'
            });
            return;
        }

        this.screenStream = await navigator.mediaDevices.getDisplayMedia({
            video: true,
            audio: false
        });

        if (this.videoRef.current) {
            this.videoRef.current.srcObject = this.screenStream;
        }

        this.setState({
            monitorStatus: '开发预览中',
            monitorError: liveKitMessage,
            monitorMode: 'browser_preview'
        });
        DashimaLearningEventRecorder.recordProjectAction('screen_monitor_started', {
            mode: 'browser_preview',
            provider: 'livekit',
            note: 'LiveKit 未配置时的本地开发预览'
        });
    }

    async handleStopScreenMonitor (options) {
        const settings = options || {};
        await DashimaLiveKitScreenMonitor.stop();
        if (this.screenStream) {
            this.screenStream.getTracks().forEach(track => track.stop());
            this.screenStream = null;
        }
        if (this.videoRef.current) {
            this.videoRef.current.srcObject = null;
        }
        if (!settings.silent) {
            this.setState({
                monitorStatus: '已停止'
            });
            DashimaLearningEventRecorder.recordProjectAction('screen_monitor_stopped', {
                source: 'dashima_teaching_panel',
                provider: 'livekit',
                mode: this.state.monitorMode
            });
        }
    }

    renderProfile () {
        const {snapshot} = this.state;
        const {profile, project} = snapshot;
        return (
            <div>
                <div className={styles.metricGrid}>
                    <div className={styles.metric}>
                        <div className={styles.metricLabel}>编辑事件</div>
                        <div className={styles.metricValue}>{profile.summary.totalEdits}</div>
                    </div>
                    <div className={styles.metric}>
                        <div className={styles.metricLabel}>运行次数</div>
                        <div className={styles.metricValue}>{profile.summary.runCount}</div>
                    </div>
                    <div className={styles.metric}>
                        <div className={styles.metricLabel}>积木数</div>
                        <div className={styles.metricValue}>{project.blockCount}</div>
                    </div>
                    <div className={styles.metric}>
                        <div className={styles.metricLabel}>画像置信度</div>
                        <div className={styles.metricValue}>{profile.confidence}%</div>
                    </div>
                </div>

                <div className={styles.profileCard}>
                    <div className={styles.profileTitle}>编程风格</div>
                    <div className={styles.profileValue}>{profile.programmingStyle}</div>
                </div>
                <div className={styles.profileCard}>
                    <div className={styles.profileTitle}>学习风格</div>
                    <div className={styles.profileValue}>{profile.learningStyle}</div>
                </div>
                <div className={styles.hintCard}>
                    <div className={styles.smallText}>
                        {profile.confidence < 50 ?
                            '继续编辑一会儿，画像会更准。现在只是早期判断。' :
                            '画像来自本次编辑过程，可同步给老师端做课后点评。'}
                    </div>
                </div>
                <div className={styles.buttonRow}>
                    <button
                        className={styles.secondaryButton}
                        onClick={this.handleClearEvents}
                    >
                        清空本次记录
                    </button>
                </div>
            </div>
        );
    }

    renderAssistant () {
        return (
            <div>
                <div className={styles.chatList}>
                    {this.state.messages.map((message, index) => (
                        <div
                            className={`${styles.message} ${
                                message.role === 'user' ? styles.userMessage : styles.assistantMessage
                            }`}
                            key={`${message.role}-${index}`}
                        >
                            {message.text}
                        </div>
                    ))}
                </div>
                <textarea
                    className={styles.textarea}
                    value={this.state.question}
                    placeholder="问我：下一步怎么做？这个功能怎么拆成积木？"
                    onChange={this.handleQuestionChange}
                />
                <div className={styles.buttonRow}>
                    <button
                        className={styles.primaryButton}
                        disabled={this.state.isAsking}
                        onClick={this.handleAsk}
                    >
                        {this.state.isAsking ? '思考中' : '询问助手'}
                    </button>
                </div>
            </div>
        );
    }

    renderMonitor () {
        return (
            <div>
                <div className={styles.monitorCard}>
                    <div className={styles.monitorTitle}>LiveKit 屏幕监控</div>
                    <div className={styles.statusPill}>{this.state.monitorStatus}</div>
                    <div className={styles.smallText}>
                        学生端发布屏幕流到课堂房间；老师端订阅 LiveKit 轨道。可通过
                        <code> window.DashimaLiveKitMonitor </code>
                        或
                        <code> /api/dashima/livekit-token </code>
                        接入现有模块。
                    </div>
                </div>
                {this.state.monitorError ? (
                    <div className={styles.hintCard}>
                        <div className={styles.smallText}>{this.state.monitorError}</div>
                    </div>
                ) : null}
                <video
                    className={styles.screenPreview}
                    ref={this.videoRef}
                    autoPlay
                    muted
                    playsInline
                />
                <div className={styles.buttonRow}>
                    <button
                        className={styles.primaryButton}
                        onClick={this.handleStartScreenMonitor}
                    >
                        开始监控
                    </button>
                    <button
                        className={styles.secondaryButton}
                        onClick={this.handleStopScreenMonitor}
                    >
                        停止
                    </button>
                </div>
            </div>
        );
    }

    renderSubmission () {
        if (!this.codemasterLaunch.enabled) {
            return (
                <div className={styles.submissionCard}>
                    <div className={styles.monitorTitle}>作品提交</div>
                    <div className={styles.statusPill}>未连接大师码</div>
                    <div className={styles.smallText}>
                        请从大师码模考编程题进入，作品会自动关联到当前学生、试卷和题目。
                    </div>
                </div>
            );
        }

        return (
            <div>
                <div className={styles.submissionCard}>
                    <div className={styles.monitorTitle}>作品提交</div>
                    <div className={styles.statusPill}>{this.state.codemasterSaveStatus}</div>
                    <div className={styles.smallText}>
                        第 {this.codemasterLaunch.questionNo || '-'} 题
                        {this.state.lastCodemasterSavedAt ? ` · ${this.state.lastCodemasterSavedAt}` : ''}
                    </div>
                </div>
                {this.state.codemasterError ? (
                    <div className={styles.hintCard}>
                        <div className={styles.errorText}>{this.state.codemasterError}</div>
                    </div>
                ) : null}
                <div className={styles.buttonRow}>
                    <button
                        className={styles.secondaryButton}
                        disabled={this.state.isSavingCodemaster || this.state.codemasterConnectionInvalid}
                        onClick={this.handleSaveCodemasterDraft}
                    >
                        {this.state.isSavingCodemaster ? '保存中' : '保存草稿'}
                    </button>
                    <button
                        className={styles.primaryButton}
                        disabled={this.state.isSavingCodemaster || this.state.codemasterConnectionInvalid}
                        onClick={this.handleSubmitCodemasterProject}
                    >
                        {this.state.isSavingCodemaster ? '提交中' : '提交作品'}
                    </button>
                    {this.codemasterLaunch.returnUrl ? (
                        <button
                            className={styles.secondaryButton}
                            onClick={this.handleReturnToCodemaster}
                        >
                            返回题目
                        </button>
                    ) : null}
                </div>
            </div>
        );
    }

    renderBody () {
        if (this.state.activeTab === 'assistant') return this.renderAssistant();
        if (this.state.activeTab === 'monitor') return this.renderMonitor();
        if (this.state.activeTab === 'submission') return this.renderSubmission();
        return this.renderProfile();
    }

    render () {
        if (this.state.collapsed) {
            return (
                <div className={`${styles.panel} ${styles.collapsed}`}>
                    <div className={styles.header}>
                        <div className={styles.title}>大师码</div>
                        <button
                            className={styles.iconButton}
                            title="展开"
                            onClick={() => this.setState({collapsed: false})}
                        >
                            +
                        </button>
                    </div>
                </div>
            );
        }

        return (
            <div className={styles.panel}>
                <div className={styles.header}>
                    <div>
                        <div className={styles.title}>大师码教学助手</div>
                        <div className={styles.smallText}>{profileText(this.state.snapshot)}</div>
                    </div>
                    <div className={styles.headerActions}>
                        <button
                            className={styles.iconButton}
                            title="收起"
                            onClick={() => this.setState({collapsed: true})}
                        >
                            -
                        </button>
                    </div>
                </div>
                <div className={styles.tabs}>
                    <button
                        className={`${styles.tab} ${
                            this.state.activeTab === 'profile' ? styles.activeTab : ''
                        }`}
                        onClick={() => this.setState({activeTab: 'profile'})}
                    >
                        学习画像
                    </button>
                    <button
                        className={`${styles.tab} ${
                            this.state.activeTab === 'assistant' ? styles.activeTab : ''
                        }`}
                        onClick={() => this.setState({activeTab: 'assistant'})}
                    >
                        AI助手
                    </button>
                    <button
                        className={`${styles.tab} ${
                            this.state.activeTab === 'monitor' ? styles.activeTab : ''
                        }`}
                        onClick={() => this.setState({activeTab: 'monitor'})}
                    >
                        屏幕监控
                    </button>
                    <button
                        className={`${styles.tab} ${
                            this.state.activeTab === 'submission' ? styles.activeTab : ''
                        }`}
                        onClick={() => this.setState({activeTab: 'submission'})}
                    >
                        作品提交
                    </button>
                </div>
                <div className={styles.body}>
                    {this.renderBody()}
                </div>
            </div>
        );
    }
}

DashimaTeachingPanel.propTypes = {
    projectTitle: PropTypes.string,
    saveProjectSb3: PropTypes.func
};

const mapStateToProps = state => {
    const scratchGui = state.scratchGui || {};
    const vm = scratchGui.vm;
    return {
        projectTitle: scratchGui.projectTitle,
        saveProjectSb3: vm && typeof vm.saveProjectSb3 === 'function' ? vm.saveProjectSb3.bind(vm) : null
    };
};

export default connect(mapStateToProps)(DashimaTeachingPanel);
