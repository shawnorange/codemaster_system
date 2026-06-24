/* eslint-env jest */
import {
    buildCodemasterArtifacts,
    buildCodemasterSubmissionFormData,
    parseCodemasterLaunchParams
} from '../../../src/lib/dashima-codemaster-submission';

describe('dashima codemaster submission helpers', () => {
    test('parses a codemaster launch url with callback details', () => {
        const launch = parseCodemasterLaunchParams(
            '?source=codemaster&submission_id=12&token=abc&callback_url=https%3A%2F%2Fcodemaster.local%2Fapi&return_url=https%3A%2F%2Fcodemaster.local%2Fexam&question_no=3'
        );

        expect(launch.enabled).toBe(true);
        expect(launch.submissionId).toBe('12');
        expect(launch.token).toBe('abc');
        expect(launch.callbackUrl).toBe('https://codemaster.local/api');
        expect(launch.returnUrl).toBe('https://codemaster.local/exam');
        expect(launch.questionNo).toBe('3');
        expect(launch.autosaveIntervalMs).toBe(60000);
    });

    test('marks launch disabled without a callback url or token', () => {
        const launch = parseCodemasterLaunchParams('?source=codemaster&submission_id=12');

        expect(launch.enabled).toBe(false);
        expect(launch.submissionId).toBe('12');
    });

    test('builds compact artifact metadata from a learning snapshot', () => {
        const artifacts = buildCodemasterArtifacts({
            sessionId: 'session-1',
            updatedAt: '2026-06-24T12:00:00.000Z',
            recentEvents: [{type: 'blockly_create'}, {type: 'green_flag'}],
            profile: {
                programmingStyle: '边做边试',
                learningStyle: '视觉反馈优先',
                confidence: 80
            },
            project: {
                blockCount: 9,
                spriteCount: 2
            }
        });

        expect(artifacts).toEqual({
            learning_event_count: 2,
            learning_session_id: 'session-1',
            learning_updated_at: '2026-06-24T12:00:00.000Z',
            block_count: 9,
            sprite_count: 2,
            programming_style: '边做边试',
            learning_style: '视觉反馈优先',
            profile_confidence: 80
        });
    });

    test('builds multipart form data for project save', () => {
        const projectBlob = new Blob(['fake sb3'], {type: 'application/octet-stream'});
        const formData = buildCodemasterSubmissionFormData({
            token: 'abc',
            status: 'submitted',
            projectTitle: '第 3 题作品',
            projectBlob,
            projectFileName: 'scratch.sb3',
            snapshot: {
                recentEvents: [{type: 'blockly_create'}],
                profile: {programmingStyle: '稳步搭建型'},
                project: {blockCount: 1}
            }
        });

        expect(formData.get('token')).toBe('abc');
        expect(formData.get('status')).toBe('submitted');
        expect(formData.get('project_title')).toBe('第 3 题作品');
        expect(JSON.parse(formData.get('learning_events'))).toEqual([{type: 'blockly_create'}]);
        expect(JSON.parse(formData.get('artifacts')).learning_event_count).toBe(1);
        expect(formData.get('project_file').name).toBe('scratch.sb3');
    });
});
