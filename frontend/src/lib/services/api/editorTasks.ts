import type { AxiosInstance } from 'axios';
import type { MediaToolStep } from '$lib/media-tools/types';

export type RunStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
export type TaskRequirement = 'required' | 'optional' | 'none';
export type OfferReason = 'models_missing' | 'not_assigned' | 'plugin_disabled' | 'requirements_failed' | string;

export interface NormRect {
	x: number;
	y: number;
	width: number;
	height: number;
}

export interface RunCandidate {
	patch: { item_id: string };
	url: string;
	seed: number | null;
	generation_file_id?: string | null;
}

export interface EditorRun {
	id: string;
	task: string;
	status: RunStatus;
	edit_id: string | null;
	step_id: string | null;
	generation_id: string | null;
	progress: number;
	error: string | null;
	soft: boolean;
	rect: NormRect | null;
	candidates: RunCandidate[];
}

export interface TaskOffer {
	preset_id: string;
	preset_name: string;
	mode: string;
	mode_label: string;
	label: string | null;
	variant: string | null;
	region_strategy: 'crop' | 'mask' | 'whole' | string;
	via: string | null;
	qualifies: boolean;
	reason: OfferReason | null;
	owned: string[];
}

export type SetupRef = { kind: 'session'; id: string } | { kind: 'formula'; id: string } | { kind: 'defaults' };

export type DefaultReason =
	| 'preset_missing'
	| 'preset_unassigned'
	| 'mode_missing'
	| 'offer_withdrawn'
	| 'session_deleted'
	| 'session_mismatch'
	| 'formula_deleted'
	| 'formula_mismatch'
	| 'plugin_disabled'
	| 'requirements_failed';

export interface DefaultHealth {
	ok: boolean;
	reason: DefaultReason | null;
	name: string | null;
	mode_label: string | null;
}

export interface TaskDefault {
	preset_id: string;
	mode: string;
	variant: string | null;
	setup: SetupRef;
	health: DefaultHealth;
}

export interface EditorTaskInfo {
	id: string;
	label: string;
	icon: string;
	region: TaskRequirement;
	prompt: TaskRequirement;
	fallback: { task: string; prompt: string } | null;
	offers: TaskOffer[];
	default: TaskDefault | null;
}

export interface StartRunBody {
	edit: { id: string; step_id: string | null };
	input: { item_id?: string; generation_file_id?: string };
	steps: MediaToolStep[];
	region: { item_id: string } | null;
	prompt: string | null;
	setup: { preset_id: string; mode: string; variant: string | null; form_data: Record<string, unknown> };
	candidates: number;
	seed: number | null;
}

export type EditorTaskFailure =
	| { kind: 'task_not_offered' }
	| { kind: 'prompt_required' }
	| { kind: 'region_required' }
	| { kind: 'region_empty' }
	| { kind: 'limit'; message: string | null }
	| { kind: 'cancel_failed' }
	| { kind: 'not_found'; code: string | null }
	| { kind: 'network' }
	| { kind: 'server'; status: number }
	| { kind: 'rejected'; status: number; code: string | null; message: string | null };

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
	return typeof value === 'string' && value ? value : null;
}

export function describeEditorTaskFailure(error: unknown): EditorTaskFailure {
	const response = (error as { response?: { status?: unknown; data?: unknown } } | null)?.response;
	if (!response || typeof response.status !== 'number') return { kind: 'network' };
	const status = response.status;
	const data = isRecord(response.data) ? response.data : {};
	const raw = 'detail' in data ? data.detail : data;
	const body = isRecord(raw) ? raw : {};
	const code = str(body.error) ?? str(data.error);
	const message = str(body.message) ?? str(data.message) ?? (typeof raw === 'string' ? raw : null);
	if (status === 404) return { kind: 'not_found', code };
	if (status === 422) {
		if (code === 'task_not_offered') return { kind: 'task_not_offered' };
		if (code === 'prompt_required') return { kind: 'prompt_required' };
		if (code === 'region_required') return { kind: 'region_required' };
		if (code === 'region_empty') return { kind: 'region_empty' };
	}
	if (status === 403) return { kind: 'limit', message };
	if (status === 400 && code === 'cancel_failed') return { kind: 'cancel_failed' };
	if (status >= 500) return { kind: 'server', status };
	return { kind: 'rejected', status, code, message };
}

export function editorTaskFailureMessage(failure: EditorTaskFailure): string {
	switch (failure.kind) {
		case 'task_not_offered':
			return 'This setup cannot do that any more. Pick another.';
		case 'prompt_required':
			return 'Describe the change first.';
		case 'region_required':
		case 'region_empty':
			return 'Mark the area first.';
		case 'limit':
			return failure.message ?? 'Your plan does not allow another generation right now.';
		case 'cancel_failed':
			return 'It could not be cancelled. Try again.';
		case 'not_found':
			return 'This edit could not be found. Reopen the picture and try again.';
		case 'network':
			return 'The connection was lost. Try again.';
		case 'server':
			return 'Something went wrong on the server. Try again.';
		case 'rejected':
			return failure.message ?? 'The request was not accepted.';
	}
}

function rectOf(raw: unknown): NormRect | null {
	if (!isRecord(raw)) return null;
	const { x, y, width, height } = raw;
	if (![x, y, width, height].every((value) => typeof value === 'number' && Number.isFinite(value))) return null;
	return { x: x as number, y: y as number, width: width as number, height: height as number };
}

const STATUSES: readonly RunStatus[] = ['queued', 'running', 'done', 'failed', 'cancelled'];

export function normalizeRun(raw: unknown): EditorRun {
	const run = isRecord(raw) ? raw : {};
	const status = STATUSES.includes(run.status as RunStatus) ? (run.status as RunStatus) : 'failed';
	const candidates = (Array.isArray(run.candidates) ? run.candidates : []).flatMap((entry): RunCandidate[] => {
		if (!isRecord(entry)) return [];
		const patch = isRecord(entry.patch) ? str(entry.patch.item_id) : null;
		const url = str(entry.url);
		if (!patch || !url) return [];
		return [{ patch: { item_id: patch }, url, seed: typeof entry.seed === 'number' ? entry.seed : null, generation_file_id: str(entry.generation_file_id) }];
	});
	const progress = typeof run.progress === 'number' && Number.isFinite(run.progress) ? Math.max(0, Math.min(1, run.progress > 1 ? run.progress / 100 : run.progress)) : 0;
	return {
		id: str(run.id) ?? '',
		task: str(run.task) ?? '',
		status,
		edit_id: str(run.edit_id),
		step_id: str(run.step_id),
		generation_id: str(run.generation_id),
		progress,
		error: str(run.error),
		soft: run.soft === true,
		rect: rectOf(run.rect),
		candidates
	};
}

function requirement(value: unknown, fallback: TaskRequirement): TaskRequirement {
	return value === 'required' || value === 'optional' || value === 'none' ? value : fallback;
}

function offerOf(raw: unknown): TaskOffer[] {
	if (!isRecord(raw)) return [];
	const presetId = str(raw.preset_id);
	const mode = str(raw.mode);
	if (!presetId || !mode) return [];
	return [
		{
			preset_id: presetId,
			preset_name: str(raw.preset_name) ?? presetId,
			mode,
			mode_label: str(raw.mode_label) ?? mode,
			label: str(raw.label),
			variant: str(raw.variant),
			region_strategy: str(raw.region_strategy) ?? 'crop',
			via: str(raw.via),
			qualifies: raw.qualifies !== false,
			reason: str(raw.reason),
			owned: (Array.isArray(raw.owned) ? raw.owned : []).filter((field): field is string => typeof field === 'string')
		}
	];
}

function setupRefOf(raw: unknown): SetupRef {
	if (isRecord(raw)) {
		const id = str(raw.id);
		if (raw.kind === 'session' && id) return { kind: 'session', id };
		if (raw.kind === 'formula' && id) return { kind: 'formula', id };
	}
	return { kind: 'defaults' };
}

export function normalizeDefault(raw: unknown): TaskDefault | null {
	if (!isRecord(raw)) return null;
	const presetId = str(raw.preset_id);
	const mode = str(raw.mode);
	if (!presetId || !mode) return null;
	const health = isRecord(raw.health) ? raw.health : {};
	const reason = str(health.reason) as DefaultReason | null;
	const setup = isRecord(raw.setup) ? raw.setup : {};
	return {
		preset_id: presetId,
		mode,
		variant: str(raw.variant),
		setup: setupRefOf(raw.setup),
		health: { ok: health.ok !== false && !reason, reason, name: str(health.name) ?? str(setup.name), mode_label: str(health.mode_label) ?? str(raw.mode_label) }
	};
}

export function normalizeTask(raw: unknown): EditorTaskInfo | null {
	if (!isRecord(raw)) return null;
	const id = str(raw.id);
	if (!id) return null;
	const fallback = isRecord(raw.fallback) && str(raw.fallback.task) ? { task: str(raw.fallback.task)!, prompt: str(raw.fallback.prompt) ?? '' } : null;
	return {
		id,
		label: str(raw.label) ?? id,
		icon: str(raw.icon) ?? 'sparkles',
		region: requirement(raw.region, 'optional'),
		prompt: requirement(raw.prompt, 'required'),
		fallback,
		offers: (Array.isArray(raw.offers) ? raw.offers : []).flatMap(offerOf),
		default: normalizeDefault(raw.default)
	};
}

function payloadOf(body: unknown): Record<string, unknown> {
	if (!isRecord(body)) return {};
	return isRecord(body.data) ? body.data : body;
}

export function createEditorTasksApi(client: AxiosInstance) {
	const task = (id: string) => `/api/editor/tasks/${encodeURIComponent(id)}`;
	const run = (id: string) => `/api/editor/runs/${encodeURIComponent(id)}`;

	return {
		async listEditorTasks(editor = 'image'): Promise<EditorTaskInfo[]> {
			const response = await client.get('/api/editor/tasks', { params: { editor } });
			const tasks = payloadOf(response.data).tasks;
			return (Array.isArray(tasks) ? tasks : []).flatMap((entry) => {
				const parsed = normalizeTask(entry);
				return parsed ? [parsed] : [];
			});
		},

		async startEditorRun(taskId: string, body: StartRunBody): Promise<EditorRun> {
			const response = await client.post(`${task(taskId)}/runs`, body);
			return normalizeRun(payloadOf(response.data).run);
		},

		async getEditorRun(runId: string): Promise<EditorRun> {
			const response = await client.get(run(runId));
			return normalizeRun(payloadOf(response.data).run);
		},

		async cancelEditorRun(runId: string): Promise<EditorRun> {
			const response = await client.delete(run(runId));
			return normalizeRun(payloadOf(response.data).run);
		},

		async setEditorTaskDefault(taskId: string, body: { preset_id: string; mode: string; variant: string | null; setup: SetupRef }): Promise<EditorTaskInfo | null> {
			const response = await client.put(`${task(taskId)}/default`, body);
			const payload = payloadOf(response.data);
			return normalizeTask(isRecord(payload.task) ? payload.task : payload);
		},

		async clearEditorTaskDefault(taskId: string): Promise<void> {
			await client.delete(`${task(taskId)}/default`);
		},

		async listEditorSessions(presetId: string): Promise<Array<{ id: string; name: string; updated_at: string | null; data: Record<string, { formData?: Record<string, unknown> }> }>> {
			const response = await client.get(`/api/sessions/preset/${encodeURIComponent(presetId)}`, { params: { kind: 'editor' } });
			const list = isRecord(response.data) && Array.isArray(response.data.data) ? response.data.data : [];
			return list.flatMap((entry) => {
				if (!isRecord(entry) || !str(entry.id)) return [];
				return [
					{
						id: entry.id as string,
						name: str(entry.name) ?? 'Setup',
						updated_at: str(entry.updated_at),
						data: isRecord(entry.data) ? (entry.data as Record<string, { formData?: Record<string, unknown> }>) : {}
					}
				];
			});
		}
	};
}
