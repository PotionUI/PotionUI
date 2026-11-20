import type { EditorTaskInfo } from '$lib/services/api/editorTasks';
import { defaultsChoice, qualifying, type SetupChoice } from './setup';

export const SUBJECT_NEEDS_MODEL = 'Finding the subject needs the matting model: set up Remove background in ImageTools.';
export const SUBJECT_FAILED = 'The subject could not be found. Try again.';
export const SUBJECT_NOT_MARKED = 'The subject was found but could not be marked. Try again.';

export type SubjectStatus = 'idle' | 'running' | 'done' | 'failed';

export interface SubjectState {
	status: SubjectStatus;
	progress: number;
	error: string | null;
}

export function idleSubject(): SubjectState {
	return { status: 'idle', progress: 0, error: null };
}

export function subjectChoice(task: Pick<EditorTaskInfo, 'offers' | 'default'> | null | undefined): SetupChoice | null {
	if (!task) return null;
	const remembered = task.default;
	if (remembered?.health.ok) {
		const { preset_id, mode, setup } = remembered;
		return { preset_id, mode, setup };
	}
	const first = qualifying(task.offers)[0];
	return first ? defaultsChoice(first) : null;
}

export function backgroundMarkFrom(rgba: Uint8ClampedArray): Uint8Array {
	const alpha = new Uint8Array(rgba.length >> 2);
	for (let i = 0; i < alpha.length; i += 1) alpha[i] = 255 - rgba[i * 4 + 3];
	return alpha;
}
