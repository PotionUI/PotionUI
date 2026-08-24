import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const generatePreviews = vi.fn();
const storageGetJSON = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		listPresets: vi.fn(),
		getSessionsForPreset: vi.fn(),
		getPresetModes: vi.fn(),
		getToken: vi.fn(() => null),
		// See previewGeneration.reset.test.ts — createGenerationSocket() touches authStore on import.
		setOnAuthExpired: vi.fn(),
		generatePreviews: (...args: unknown[]) => generatePreviews(...args)
	}
}));

vi.mock('$lib/utils/storage', () => ({
	storage: {
		get: vi.fn(),
		set: vi.fn(),
		remove: vi.fn(),
		getJSON: (...args: unknown[]) => storageGetJSON(...args),
		setJSON: vi.fn()
	}
}));

import { api } from '$lib/services/api/index';
import { previewGenerationStore } from './previewGeneration';
import { createBlankEditorSegment, flattenRichSegments } from '$lib/utils/richSegments';

describe('previewGenerationStore.handleGeneratePreviews — segments flatten to prompt_template', () => {
	beforeEach(() => {
		previewGenerationStore.reset();
		generatePreviews.mockReset();
		previewGenerationStore.setSelectedSessionId('session-a');
		previewGenerationStore.setSelectedMode('t2i');
	});

	it('rejects without calling the API when the flattened segments have no << value >> placeholder', async () => {
		previewGenerationStore.setPromptSegments([{ ...createBlankEditorSegment(), content: 'a photo of a cat' }]);

		await previewGenerationStore.handleGeneratePreviews('cat-1', new Set(['v1']));

		expect(generatePreviews).not.toHaveBeenCalled();
		expect(get(previewGenerationStore).previewGenerationStatus).toContain('<< value >>');
	});

	it('flattens multiple enabled segments into the prompt_template sent to the API', async () => {
		generatePreviews.mockResolvedValue({ success: true, data: { started: 0, generations: [] } });
		previewGenerationStore.setPromptSegments([
			{ ...createBlankEditorSegment(), content: 'a photo of' },
			{ ...createBlankEditorSegment(), content: '<< value >>' }
		]);

		await previewGenerationStore.handleGeneratePreviews('cat-1', new Set(['v1']));

		expect(generatePreviews).toHaveBeenCalledWith(
			'cat-1',
			expect.objectContaining({ prompt_template: 'a photo of, << value >>' })
		);
	});

	it('ignores disabled segments when flattening', async () => {
		generatePreviews.mockResolvedValue({ success: true, data: { started: 0, generations: [] } });
		previewGenerationStore.setPromptSegments([
			{ ...createBlankEditorSegment(), content: 'a photo of << value >>' },
			{ ...createBlankEditorSegment(), content: 'ignored text', enabled: false }
		]);

		await previewGenerationStore.handleGeneratePreviews('cat-1', new Set(['v1']));

		expect(generatePreviews).toHaveBeenCalledWith(
			'cat-1',
			expect.objectContaining({ prompt_template: 'a photo of << value >>' })
		);
	});
});

describe('previewGenerationStore.loadPresets — legacy {{ value }} localStorage migration', () => {
	beforeEach(() => {
		previewGenerationStore.reset();
		storageGetJSON.mockReset();
		vi.mocked(api.listPresets).mockReset();
	});

	it('rewrites a stored {{ value }} placeholder in restored segment content to << value >>', async () => {
		storageGetJSON.mockReturnValue({
			presetId: 'preset-a',
			sessionId: '',
			mode: '',
			promptSegments: [{ type: 'content', content: 'a photo of {{ value }}', chips: {}, enabled: true }],
			negativePrompt: '',
			useFixedSeed: false,
			fixedSeed: 42
		});
		vi.mocked(api.listPresets).mockResolvedValue({
			success: true,
			data: [{ id: 'preset-a', name: 'Preset A' }]
		} as never);
		vi.mocked(api.getSessionsForPreset).mockResolvedValue({ success: true, data: [] } as never);
		vi.mocked(api.getPresetModes).mockResolvedValue({
			success: true,
			data: { modes: [], default_mode: '' }
		} as never);

		await previewGenerationStore.loadPresets();

		const state = get(previewGenerationStore);
		expect(flattenRichSegments(state.promptSegments)).toBe('a photo of << value >>');
	});
});
