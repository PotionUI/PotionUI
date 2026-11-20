import { describe, expect, it, vi } from 'vitest';
import { OUTCOME_COPY, editorOriginOf, openOriginEdit } from './editOrigin';

const ORIGIN = { kind: 'editor', edit_id: 'ed_1', step_id: 's7', task: 'edit_region', run_id: 'er_1' };

describe('editorOriginOf', () => {
	it('reads an editor origin', () => {
		expect(editorOriginOf({ origin: ORIGIN })).toEqual({ editId: 'ed_1', stepId: 's7', task: 'edit_region', runId: 'er_1' });
	});

	it('keeps the edit id when the rest is missing', () => {
		expect(editorOriginOf({ origin: { kind: 'editor', edit_id: 'ed_1' } })).toEqual({ editId: 'ed_1', stepId: null, task: null, runId: null });
	});

	it('is null for no origin, another kind, or an editor origin without an edit', () => {
		expect(editorOriginOf({})).toBeNull();
		expect(editorOriginOf({ origin: null })).toBeNull();
		expect(editorOriginOf({ origin: { kind: 'api', edit_id: 'ed_1' } })).toBeNull();
		expect(editorOriginOf({ origin: { kind: 'editor' } })).toBeNull();
		expect(editorOriginOf({ origin: { kind: 'editor', edit_id: 42 as unknown as string } })).toBeNull();
	});
});

describe('openOriginEdit', () => {
	const origin = editorOriginOf({ origin: ORIGIN })!;

	it('opens the edit and goes to the Generate page', async () => {
		const goToGenerate = vi.fn();
		const continueEdit = vi.fn(async () => ({ kind: 'opened' as const, tabId: 't1' }));
		expect(await openOriginEdit(origin, { continueEdit, goToGenerate })).toEqual({ ok: true });
		expect(continueEdit).toHaveBeenCalledWith('ed_1');
		expect(goToGenerate).toHaveBeenCalledTimes(1);
	});

	it('focuses an edit that is already open', async () => {
		const goToGenerate = vi.fn();
		expect(await openOriginEdit(origin, { continueEdit: async () => ({ kind: 'focused', tabId: 't9' }), goToGenerate })).toEqual({ ok: true });
		expect(goToGenerate).toHaveBeenCalled();
	});

	it('says in plain words when the edit is gone, its picture is gone, or it could not be opened', async () => {
		const goToGenerate = vi.fn();
		for (const kind of ['missing', 'source-missing', 'failed'] as const) {
			const result = await openOriginEdit(origin, { continueEdit: async () => ({ kind }), goToGenerate });
			expect(result).toEqual({ ok: false, kind, message: OUTCOME_COPY[kind] });
		}
		expect(OUTCOME_COPY.missing).toBe('That edit no longer exists.');
		expect(goToGenerate).not.toHaveBeenCalled();
	});
});
