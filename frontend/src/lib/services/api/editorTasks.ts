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

export interface TimeRange {
	start_s: number;
	end_s: number;
}

export interface RunCandidate {
	patch: { item_id: string };
	url: string;
	seed: number | null;
	generation_file_id?: string | null;
	clip?: boolean;
	range?: TimeRange | null;
	added?: { at_s: number; seconds: number } | null;
	duration_s?: number | null;
}

export interface EditorRun {
	range?: (TimeRange & { whole: boolean }) | null;
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
	from_mode: string | null;
	mode_label: string;
	mode_icon?: string | null;
	label: string | null;
	short_description: string | null;
	order: number;
	region_strategy: 'crop' | 'mask' | 'whole' | string;
	quantity_field?: string | null;
	max_range_s?: number | null;
	via: string | null;
	qualifies: boolean;
	reason: OfferReason | null;
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
	setup: SetupRef;
	health: DefaultHealth;
}

export interface EditorTaskInfo {
	id: string;
	label: string;
	icon: string;
	region: TaskRequirement;
	prompt: TaskRequirement;
	editors?: string[];
	range?: TaskRequirement;
	result?: 'replace' | 'append';
	audio?: 'keep' | 'result';
	resizes?: boolean;
	internal?: boolean;
	matte?: boolean;
	grow?: number;
	fallback: { task: string; prompt: string } | null;
	offers: TaskOffer[];
	default: TaskDefault | null;
}

export interface StartRunBody {
	edit: { id: string; step_id: string | null };
	input: { item_id?: string; generation_file_id?: string };
	steps: MediaToolStep[];
	region: { item_id: string } | null;
	range?: TimeRange | null;
	prompt: string | null;
	setup: { preset_id: string; mode: string; form_data: Record<string, unknown> };
	candidates: number;
	seed: number | null;
	options?: Record<string, unknown>;
}

export type EditorTaskFailure =
	| { kind: 'task_not_offered' }
	| { kind: 'prompt_required' }
	| { kind: 'region_required' }
	| { kind: 'region_empty' }
	| { kind: 'range_invalid'; message: string | null }
	| { kind: 'setup_incomplete' }
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
		if (code === 'range_invalid' || code === 'range_outside_clip' || code === 'range_required' || code === 'range_too_long') return { kind: 'range_invalid', message };
		if (code === 'form_validation_failed') return { kind: 'setup_incomplete' };
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
		case 'range_invalid':
			return failure.message ?? 'Mark the part of the clip again.';
		case 'setup_incomplete':
			return 'This setup is missing a model. Open Settings and choose it.';
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

function finite(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function timeRangeOf(raw: unknown): TimeRange | null {
	if (!isRecord(raw)) return null;
	const start = finite(raw.start_s);
	const end = finite(raw.end_s);
	return start !== null && end !== null ? { start_s: start, end_s: end } : null;
}

const STATUSES: readonly RunStatus[] = ['queued', 'running', 'done', 'failed', 'cancelled'];

export function normalizeRun(raw: unknown): EditorRun {
	const run = isRecord(raw) ? raw : {};
	const status = STATUSES.includes(run.status as RunStatus) ? (run.status as RunStatus) : 'failed';
	const candidates = (Array.isArray(run.candidates) ? run.candidates : []).flatMap((entry): RunCandidate[] => {
		if (!isRecord(entry)) return [];
		const clip = isRecord(entry.clip) ? str(entry.clip.item_id) : null;
		const patch = isRecord(entry.patch) ? str(entry.patch.item_id) : clip;
		const url = str(entry.url);
		if (!patch || !url) return [];
		const candidate: RunCandidate = { patch: { item_id: patch }, url, seed: typeof entry.seed === 'number' ? entry.seed : null, generation_file_id: str(entry.generation_file_id) };
		if (clip) {
			candidate.clip = true;
			candidate.range = timeRangeOf(entry.range);
			candidate.added = isRecord(entry.added) && finite(entry.added.at_s) !== null && finite(entry.added.seconds) !== null ? { at_s: entry.added.at_s as number, seconds: entry.added.seconds as number } : null;
			candidate.duration_s = finite(entry.duration_s);
		}
		return [candidate];
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
		range: isRecord(run.range) && timeRangeOf(run.range) ? { ...timeRangeOf(run.range)!, whole: run.range.whole === true } : null,
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
			from_mode: str(raw.from_mode),
			mode_label: str(raw.mode_label) ?? mode,
			mode_icon: str(raw.mode_icon),
			label: str(raw.label),
			short_description: str(raw.short_description),
			order: typeof raw.order === 'number' && Number.isFinite(raw.order) ? raw.order : 0,
			region_strategy: str(raw.region_strategy) ?? 'crop',
			quantity_field: str(raw.quantity_field),
			max_range_s: typeof raw.max_range_s === 'number' && raw.max_range_s > 0 ? raw.max_range_s : null,
			via: str(raw.via),
			qualifies: raw.qualifies !== false,
			reason: str(raw.reason)
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
		setup: setupRefOf(raw.setup),
		health: { ok: health.ok !== false && !reason, reason, name: str(health.name) ?? str(setup.name), mode_label: str(health.mode_label) ?? str(raw.mode_label) }
	};
}

function videoFieldsOf(raw: Record<string, unknown>): Partial<EditorTaskInfo> {
	if (!Array.isArray(raw.editors)) return {};
	return {
		editors: raw.editors.filter((entry): entry is string => typeof entry === 'string'),
		range: requirement(raw.range, 'none'),
		result: raw.result === 'append' ? 'append' : 'replace',
		audio: raw.audio === 'result' ? 'result' : 'keep',
		resizes: raw.resizes === true
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
		...videoFieldsOf(raw),
		...(raw.internal === true ? { internal: true } : {}),
		...(raw.matte === true ? { matte: true } : {}),
		...(typeof raw.grow === 'number' && Number.isFinite(raw.grow) && raw.grow >= 0 ? { grow: raw.grow } : {}),
		fallback,
		offers: (Array.isArray(raw.offers) ? raw.offers : []).flatMap(offerOf),
		default: normalizeDefault(raw.default)
	};
}

function payloadOf(body: unknown): Record<string, unknown> {
	if (!isRecord(body)) return {};
	return isRecord(body.data) ? body.data : body;
}

export interface SaveEditorSessionBody {
	preset_id: string;
	name: string;
	data: Record<string, Record<string, unknown>>;
	mode: string;
	kind: 'editor';
}

export interface UpdateEditorSessionBody {
	name: string;
	data: Record<string, Record<string, unknown>>;
	mode: string;
	base_version: number | null;
	kind: 'editor';
}

export interface EditorSessionEntry {
	id: string;
	name: string;
	created_at?: string | null;
	updated_at: string | null;
	pinned?: boolean;
	version?: number | null;
	data: Record<string, { formData?: Record<string, unknown> }>;
}

export interface SavedEditorSession {
	id: string;
	name: string;
	version: number | null;
	updated_at: string | null;
}

function savedSession(body: unknown): SavedEditorSession {
	const found = isRecord(body) && isRecord(body.data) ? body.data : {};
	return {
		id: str(found.id) ?? '',
		name: str(found.name) ?? '',
		version: typeof found.version === 'number' ? found.version : null,
		updated_at: str(found.updated_at)
	};
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

		async setEditorTaskDefault(taskId: string, body: { preset_id: string; mode: string; setup: SetupRef }): Promise<EditorTaskInfo | null> {
			const response = await client.put(`${task(taskId)}/default`, body);
			const payload = payloadOf(response.data);
			return normalizeTask(isRecord(payload.task) ? payload.task : payload);
		},

		async clearEditorTaskDefault(taskId: string): Promise<void> {
			await client.delete(`${task(taskId)}/default`);
		},

		async saveEditorSession(body: SaveEditorSessionBody): Promise<SavedEditorSession> {
			const response = await client.post('/api/sessions/save', body);
			return savedSession(response.data);
		},

		async updateEditorSession(sessionId: string, body: UpdateEditorSessionBody): Promise<SavedEditorSession> {
			const response = await client.put(`/api/sessions/${encodeURIComponent(sessionId)}`, body);
			return savedSession(response.data);
		},

		async listEditorSessions(presetId: string): Promise<EditorSessionEntry[]> {
			const response = await client.get(`/api/sessions/preset/${encodeURIComponent(presetId)}`, { params: { kind: 'editor' } });
			const list = isRecord(response.data) && Array.isArray(response.data.data) ? response.data.data : [];
			return list.flatMap((entry): EditorSessionEntry[] => {
				if (!isRecord(entry) || !str(entry.id)) return [];
				return [
					{
						id: entry.id as string,
						name: str(entry.name) ?? 'Setup',
						updated_at: str(entry.updated_at),
						version: typeof entry.version === 'number' ? entry.version : null,
						data: isRecord(entry.data) ? (entry.data as Record<string, { formData?: Record<string, unknown> }>) : {}
					}
				];
			});
		}
	};
}
