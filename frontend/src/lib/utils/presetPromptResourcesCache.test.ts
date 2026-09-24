import { beforeEach, describe, expect, it, vi } from 'vitest';

const getPresetFormSchema = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getPresetFormSchema: (...args: unknown[]) => getPresetFormSchema(...args)
	}
}));

const { getPresetPromptResources, invalidatePresetPromptResourcesCache } = await import(
	'./presetPromptResourcesCache'
);

beforeEach(() => {
	getPresetFormSchema.mockReset();
	invalidatePresetPromptResourcesCache();
});

describe('getPresetPromptResources', () => {
	it('returns empty specs when presetId or mode is missing', async () => {
		expect(await getPresetPromptResources(null, 'txt2img')).toEqual({ specs: [], fieldLabels: {} });
		expect(await getPresetPromptResources('preset', null)).toEqual({ specs: [], fieldLabels: {} });
		expect(getPresetFormSchema).not.toHaveBeenCalled();
	});

	it('extracts prompt_resources and field labels from the form response', async () => {
		getPresetFormSchema.mockResolvedValue({
			success: true,
			data: {
				preset_id: 'preset',
				form_schema: { properties: { references: { title: 'References' } } },
				prompt_resources: [{ field: 'references', kind: 'image', token: '<Picture @>' }]
			}
		});

		const result = await getPresetPromptResources('preset', 'refs');
		expect(result.specs).toEqual([{ field: 'references', kind: 'image', token: '<Picture @>' }]);
		expect(result.fieldLabels).toEqual({ references: 'References' });
	});

	it('deduplicates in-flight requests by preset, mode, and form name', async () => {
		let resolve!: (value: unknown) => void;
		getPresetFormSchema.mockReturnValue(new Promise((done) => (resolve = done)));

		const first = getPresetPromptResources('preset', 'txt2img');
		const second = getPresetPromptResources('preset', 'txt2img');
		expect(getPresetFormSchema).toHaveBeenCalledTimes(1);

		resolve({ success: true, data: { form_schema: {}, prompt_resources: [] } });
		expect(await first).toEqual(await second);
	});

	it('returns empty specs (not a rejection) on a failed request, and does not cache the failure', async () => {
		getPresetFormSchema.mockRejectedValueOnce(new Error('network'));
		getPresetFormSchema.mockResolvedValueOnce({
			success: true,
			data: { form_schema: {}, prompt_resources: [{ field: 'a', kind: 'video', token: '<Video @>' }] }
		});

		await expect(getPresetPromptResources('preset', 'vid')).resolves.toEqual({ specs: [], fieldLabels: {} });
		const second = await getPresetPromptResources('preset', 'vid');
		expect(second.specs).toEqual([{ field: 'a', kind: 'video', token: '<Video @>' }]);
		expect(getPresetFormSchema).toHaveBeenCalledTimes(2);
	});

	it('returns empty specs when the response is unsuccessful', async () => {
		getPresetFormSchema.mockResolvedValue({ success: false });
		expect(await getPresetPromptResources('preset', 'txt2img')).toEqual({ specs: [], fieldLabels: {} });
	});
});
