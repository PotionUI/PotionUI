import { beforeEach, describe, expect, it, vi } from 'vitest';

const getPresetFormSchema = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getPresetFormSchema: (...args: unknown[]) => getPresetFormSchema(...args)
	}
}));

const { getPresetPromptSyntax, invalidatePresetPromptSyntaxCache } = await import('./presetPromptSyntaxCache');

beforeEach(() => {
	getPresetFormSchema.mockReset();
	invalidatePresetPromptSyntaxCache();
});

describe('getPresetPromptSyntax', () => {
	it('returns an empty list when presetId or mode is missing', async () => {
		expect(await getPresetPromptSyntax(null, 'video')).toEqual([]);
		expect(await getPresetPromptSyntax('preset', null)).toEqual([]);
		expect(getPresetFormSchema).not.toHaveBeenCalled();
	});

	it('extracts prompt_syntax from the form response', async () => {
		getPresetFormSchema.mockResolvedValue({
			success: true,
			data: {
				preset_id: 'preset',
				form_schema: {},
				prompt_syntax: [{ token: '(S1)', kind: 'marker' }]
			}
		});

		const result = await getPresetPromptSyntax('preset', 'video');
		expect(result).toEqual([{ token: '(S1)', kind: 'marker' }]);
	});

	it('deduplicates in-flight requests by preset, mode, and form name', async () => {
		let resolve!: (value: unknown) => void;
		getPresetFormSchema.mockReturnValue(new Promise((done) => (resolve = done)));

		const first = getPresetPromptSyntax('preset', 'video');
		const second = getPresetPromptSyntax('preset', 'video');
		expect(getPresetFormSchema).toHaveBeenCalledTimes(1);

		resolve({ success: true, data: { form_schema: {}, prompt_syntax: [] } });
		expect(await first).toEqual(await second);
	});

	it('returns an empty list (not a rejection) on a failed request, and does not cache the failure', async () => {
		getPresetFormSchema.mockRejectedValueOnce(new Error('network'));
		getPresetFormSchema.mockResolvedValueOnce({
			success: true,
			data: { form_schema: {}, prompt_syntax: [{ token: 'BREAK', kind: 'marker' }] }
		});

		await expect(getPresetPromptSyntax('preset', 'video')).resolves.toEqual([]);
		const second = await getPresetPromptSyntax('preset', 'video');
		expect(second).toEqual([{ token: 'BREAK', kind: 'marker' }]);
		expect(getPresetFormSchema).toHaveBeenCalledTimes(2);
	});

	it('returns an empty list when the response is unsuccessful', async () => {
		getPresetFormSchema.mockResolvedValue({ success: false });
		expect(await getPresetPromptSyntax('preset', 'video')).toEqual([]);
	});
});
