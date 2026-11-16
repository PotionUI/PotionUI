import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';

export type EditTool = 'image';
export type EditSourceKind = 'history' | 'library' | 'upload';
export type EditStateDoc = Record<string, unknown>;

export interface EditSource {
	kind: EditSourceKind;
	id: string;
}

export interface EditSummary {
	id: string;
	tool: EditTool;
	name: string | null;
	source: EditSource;
	thumbnail_upload_id: string | null;
	revision: number;
	created_at: string | null;
	updated_at: string | null;
}

export interface EditRecord extends EditSummary {
	state: EditStateDoc;
}

export interface EditList {
	edits: EditSummary[];
	total: number;
	limit: number;
	offset: number;
}

export interface CreateEditBody {
	tool?: EditTool;
	name?: string | null;
	source: EditSource;
	state: EditStateDoc;
	thumbnail_upload_id?: string | null;
}

export interface SaveEditBody {
	revision: number;
	state: EditStateDoc;
	thumbnail_upload_id?: string | null;
}

export interface ListEditsParams {
	tool?: EditTool;
	search?: string;
	limit?: number;
	offset?: number;
}

export const EDIT_STATE_LIMIT_BYTES = 256 * 1024;

export type EditFailure =
	| { kind: 'conflict'; revision: number | null }
	| { kind: 'not_found' }
	| { kind: 'too_large' }
	| { kind: 'network' }
	| { kind: 'server'; status: number }
	| { kind: 'rejected'; status: number; code: string | null };

interface ErrorBody {
	error?: unknown;
	revision?: unknown;
}

const RETRYABLE_STATUSES = new Set([401, 403, 408, 425, 429]);

export function describeEditFailure(error: unknown): EditFailure {
	const response = (error as { response?: { status?: unknown; data?: unknown } } | null)?.response;
	if (!response || typeof response.status !== 'number') return { kind: 'network' };
	const status = response.status;
	const data = response.data as { detail?: unknown } | undefined;
	const raw = data && typeof data === 'object' && 'detail' in data ? data.detail : data;
	const body: ErrorBody = raw && typeof raw === 'object' ? (raw as ErrorBody) : {};
	const code = typeof body.error === 'string' ? body.error : null;
	if (status === 409 && code === 'edit_revision_conflict') {
		return { kind: 'conflict', revision: typeof body.revision === 'number' ? body.revision : null };
	}
	if (status === 404) return { kind: 'not_found' };
	if (status === 413) return { kind: 'too_large' };
	if (status >= 500 || RETRYABLE_STATUSES.has(status)) return { kind: 'server', status };
	return { kind: 'rejected', status, code };
}

export function isEditConflict(error: unknown): boolean {
	return describeEditFailure(error).kind === 'conflict';
}

export function isEditMissing(error: unknown): boolean {
	return describeEditFailure(error).kind === 'not_found';
}

export function isEditTooLarge(error: unknown): boolean {
	return describeEditFailure(error).kind === 'too_large';
}

export function createEditsApi(client: AxiosInstance) {
	const path = (id: string) => `/api/edits/${encodeURIComponent(id)}`;

	return {
		async listEdits(params: ListEditsParams = {}): Promise<APIResponse<EditList>> {
			const response = await client.get('/api/edits', { params });
			return response.data;
		},

		async getEdit(id: string): Promise<APIResponse<{ edit: EditRecord }>> {
			const response = await client.get(path(id));
			return response.data;
		},

		async createEdit(body: CreateEditBody): Promise<APIResponse<{ edit: EditRecord }>> {
			const response = await client.post('/api/edits', { tool: 'image', ...body });
			return response.data;
		},

		async saveEdit(id: string, body: SaveEditBody): Promise<APIResponse<{ edit: EditSummary }>> {
			const response = await client.put(path(id), body);
			return response.data;
		},

		async renameEdit(id: string, name: string | null): Promise<APIResponse<{ edit: EditSummary }>> {
			const response = await client.patch(path(id), { name });
			return response.data;
		},

		async duplicateEdit(id: string): Promise<APIResponse<{ edit: EditRecord }>> {
			const response = await client.post(`${path(id)}/duplicate`);
			return response.data;
		},

		async deleteEdit(id: string): Promise<void> {
			await client.delete(path(id));
		}
	};
}

export type EditsApi = ReturnType<typeof createEditsApi>;
