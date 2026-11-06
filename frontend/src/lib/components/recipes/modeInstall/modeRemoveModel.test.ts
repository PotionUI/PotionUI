import { describe, it, expect } from 'vitest';
import type { ModeRemovalFile, ModeRemovalPreview } from '$lib/services/api/recipeModes';
import { groupKept, removalDeleteIds, removeConfirmLabel } from './modeRemoveModel';

function file(overrides: Partial<ModeRemovalFile>): ModeRemovalFile {
	return { model_id: 'm', label: 'Main model', filename: 'f.safetensors', size_bytes: 1, ...overrides };
}

describe('groupKept', () => {
	it('merges files kept for the same reason and sums their size', () => {
		const groups = groupKept([
			file({ model_id: '1', label: 'Main model', reason_code: 'shared', reason: 'Used by Video and 9 other modes', size_bytes: 20 }),
			file({ model_id: '2', label: 'Text encoder', reason_code: 'shared', reason: 'Used by Video and 9 other modes', size_bytes: 9 }),
			file({ model_id: '3', label: 'Detail LoRA', reason_code: 'shared', reason: 'Also used by Restore', size_bytes: 4 })
		]);
		expect(groups).toHaveLength(2);
		expect(groups[0]).toMatchObject({ title: 'Main model and text encoder', reason: 'Used by Video and 9 other modes', bytes: 29 });
		expect(groups[1].title).toBe('Detail LoRA');
	});

	it('names a file the user added outside the models folder by its filename', () => {
		const groups = groupKept([
			file({ model_id: '9', label: 'Upscaler', filename: 'custom.safetensors', reason_code: 'outside_models_folder', reason: 'You added this yourself' })
		]);
		expect(groups[0].title).toBe('custom.safetensors');
	});

	it('keeps two outside files apart', () => {
		const groups = groupKept([
			file({ model_id: '1', filename: 'a.safetensors', reason_code: 'outside_models_folder', reason: 'x' }),
			file({ model_id: '2', filename: 'b.safetensors', reason_code: 'outside_models_folder', reason: 'x' })
		]);
		expect(groups.map((group) => group.title)).toEqual(['a.safetensors', 'b.safetensors']);
	});
});

describe('removal confirm', () => {
	const preview = (count: number): ModeRemovalPreview => ({
		preset_id: 'p',
		mode: 'upscale',
		label: 'Upscale',
		target: 'native',
		delete: Array.from({ length: count }, (_, index) => file({ model_id: `d${index}` })),
		keep: [file({ model_id: 'kept-1', reason_code: 'shared', reason: 'Used by Video' })],
		frees_bytes: 1,
		clears_defaults: []
	});

	it('labels the button with the number of files', () => {
		expect(removeConfirmLabel(null)).toBe('Nothing to remove');
		expect(removeConfirmLabel(preview(0))).toBe('Nothing to remove');
		expect(removeConfirmLabel(preview(1))).toBe('Remove 1 file');
		expect(removeConfirmLabel(preview(3))).toBe('Remove 3 files');
	});

	it('sends exactly the previewed deletable ids', () => {
		expect(removalDeleteIds(preview(2))).toEqual(['d0', 'd1']);
	});
});
