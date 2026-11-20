import { describe, expect, it, vi } from 'vitest';
import { createEditsApi, describeEditFailure, isEditConflict, isEditMissing, isEditTooLarge } from './edits';

function failure(status: number, detail: unknown) {
	return { response: { status, data: { detail } } };
}

describe('describeEditFailure', () => {
	it('reads the current revision out of a revision conflict', () => {
		const error = failure(409, { error: 'edit_revision_conflict', message: 'x', revision: 7 });
		expect(describeEditFailure(error)).toEqual({ kind: 'conflict', revision: 7 });
		expect(isEditConflict(error)).toBe(true);
	});

	it('does not mistake another 409 for a revision conflict', () => {
		expect(describeEditFailure(failure(409, { error: 'other' }))).toEqual({ kind: 'rejected', status: 409, code: 'other' });
	});

	it('recognises a missing and an oversized edit', () => {
		expect(isEditMissing(failure(404, { error: 'edit_not_found' }))).toBe(true);
		expect(isEditTooLarge(failure(413, { error: 'edit_state_too_large' }))).toBe(true);
	});

	it('separates failures worth retrying from refusals', () => {
		expect(describeEditFailure({ message: 'Network Error' })).toEqual({ kind: 'network' });
		expect(describeEditFailure(failure(502, 'bad gateway'))).toEqual({ kind: 'server', status: 502 });
		expect(describeEditFailure(failure(429, {}))).toEqual({ kind: 'server', status: 429 });
		expect(describeEditFailure(failure(422, [{ msg: 'bad' }]))).toEqual({ kind: 'rejected', status: 422, code: null });
	});
});

describe('createEditsApi', () => {
	it('calls the edits routes', async () => {
		const client = {
			get: vi.fn(async () => ({ data: { success: true } })),
			post: vi.fn(async () => ({ data: { success: true } })),
			put: vi.fn(async () => ({ data: { success: true } })),
			patch: vi.fn(async () => ({ data: { success: true } })),
			delete: vi.fn(async () => ({ data: {} }))
		};
		const api = createEditsApi(client as never);
		await api.listEdits({ limit: 5 });
		await api.getEdit('ed_1');
		await api.createEdit({ source: { kind: 'upload', id: 'a' }, state: {} });
		await api.saveEdit('ed_1', { revision: 2, state: {} });
		await api.renameEdit('ed_1', 'Name');
		await api.duplicateEdit('ed_1');
		await api.deleteEdit('ed_1');
		expect(client.get).toHaveBeenCalledWith('/api/edits', { params: { limit: 5 } });
		expect(client.get).toHaveBeenCalledWith('/api/edits/ed_1');
		expect(client.post).toHaveBeenCalledWith('/api/edits', { tool: 'image', source: { kind: 'upload', id: 'a' }, state: {} });
		expect(client.put).toHaveBeenCalledWith('/api/edits/ed_1', { revision: 2, state: {} });
		expect(client.patch).toHaveBeenCalledWith('/api/edits/ed_1', { name: 'Name' });
		expect(client.post).toHaveBeenCalledWith('/api/edits/ed_1/duplicate');
		expect(client.delete).toHaveBeenCalledWith('/api/edits/ed_1');
	});
});
