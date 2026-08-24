import { describe, it, expect } from 'vitest';
import { buildHistoryReuseTabData, buildImportBundleTabData } from './historyReuse';
import type { GenerationHistoryItem, ImportBundleReuse } from '$lib/types/history';
import type { Backend } from '$lib/services/admin-api';

function makeGeneration(overrides: Partial<GenerationHistoryItem> = {}): GenerationHistoryItem {
	return {
		id: 'gen-1',
		preset_id: 'sdxl/example/base',
		preset_name: 'Example',
		mode: 'txt2img',
		prompt_state: { prompt: 'a cat' },
		form_data: { seed: 42, steps: 20 },
		status: 'completed',
		progress: 100,
		created_at: '2026-07-01T00:00:00Z',
		updated_at: '2026-07-01T00:00:00Z',
		files: [],
		rating: 0,
		is_favorite: false,
		...overrides
	};
}

function makeBackend(id: string): Backend {
	return {
		id,
		name: id,
		engine: 'native',
		enabled: true,
		is_default: false,
		priority: 0,
		timeout_seconds: 60
	};
}

describe('buildHistoryReuseTabData', () => {
	it('restores preset/mode/formData/prompt_state', () => {
		const generation = makeGeneration();
		const { tabData } = buildHistoryReuseTabData(generation, []);

		expect(tabData.selectedPreset).toBe('sdxl/example/base');
		expect(tabData.selectedMode).toBe('txt2img');
		expect(tabData.formData).toMatchObject({ seed: 42, steps: 20 });
		expect((tabData as any).prompt).toBe('a cat');
	});

	it('defaults mode to txt2img when missing', () => {
		const generation = makeGeneration({ mode: undefined });
		const { tabData } = buildHistoryReuseTabData(generation, []);
		expect(tabData.selectedMode).toBe('txt2img');
	});

	it('restores selectedVariant when form_name is present', () => {
		const generation = makeGeneration({ form_name: 'quality' });
		const { tabData } = buildHistoryReuseTabData(generation, []);
		expect(tabData.selectedVariant).toBe('quality');
	});

	it('sets selectedVariant to null when form_name is absent/null', () => {
		const withoutFormName = buildHistoryReuseTabData(makeGeneration({ form_name: undefined }), []);
		expect(withoutFormName.tabData.selectedVariant).toBeNull();

		const withNullFormName = buildHistoryReuseTabData(makeGeneration({ form_name: null }), []);
		expect(withNullFormName.tabData.selectedVariant).toBeNull();
	});

	it('restores selectedBackendId when the backend is present in the available list', () => {
		const generation = makeGeneration({ backend_id: 'backend-1' });
		const { tabData, backendUnavailable } = buildHistoryReuseTabData(generation, [
			makeBackend('backend-1'),
			makeBackend('backend-2')
		]);

		expect(tabData.selectedBackendId).toBe('backend-1');
		expect(backendUnavailable).toBe(false);
	});

	it('does not restore selectedBackendId and signals backendUnavailable when the backend id is gone', () => {
		const generation = makeGeneration({ backend_id: 'backend-deleted' });
		const { tabData, backendUnavailable } = buildHistoryReuseTabData(generation, [
			makeBackend('backend-1')
		]);

		expect(tabData.selectedBackendId).toBeUndefined();
		expect(backendUnavailable).toBe(true);
	});

	it('does not restore selectedBackendId and does not signal unavailable when backend_id is null/absent', () => {
		const nullBackend = buildHistoryReuseTabData(makeGeneration({ backend_id: null }), [
			makeBackend('backend-1')
		]);
		expect(nullBackend.tabData.selectedBackendId).toBeUndefined();
		expect(nullBackend.backendUnavailable).toBe(false);

		const missingBackend = buildHistoryReuseTabData(makeGeneration({ backend_id: undefined }), [
			makeBackend('backend-1')
		]);
		expect(missingBackend.tabData.selectedBackendId).toBeUndefined();
		expect(missingBackend.backendUnavailable).toBe(false);
	});

	it('restores a positive seed onto both the tab and formData', () => {
		const generation = makeGeneration({ seed: 12345, form_data: { seed: 12345, steps: 30 } });
		const { tabData } = buildHistoryReuseTabData(generation, []);

		expect(tabData.seed).toBe(12345);
		expect((tabData.formData as Record<string, unknown>).seed).toBe(12345);
	});

	it('round-trips a -1 (randomize) seed as -1, not coerced to something else', () => {
		const generation = makeGeneration({ seed: -1, form_data: { seed: -1, steps: 30 } });
		const { tabData } = buildHistoryReuseTabData(generation, []);

		expect(tabData.seed).toBe(-1);
		expect((tabData.formData as Record<string, unknown>).seed).toBe(-1);
	});

	it('leaves seed and formData.seed untouched when generation.seed is null/absent', () => {
		const generation = makeGeneration({ seed: null, form_data: { steps: 30 } });
		const { tabData } = buildHistoryReuseTabData(generation, []);

		expect(tabData.seed).toBeUndefined();
		expect((tabData.formData as Record<string, unknown>).seed).toBeUndefined();
	});
});

function makeImportReuse(overrides: Partial<ImportBundleReuse> = {}): ImportBundleReuse {
	return {
		preset_id: 'sdxl/example/base',
		mode: 'txt2img',
		form_name: null,
		form_data: { seed: 42, steps: 20 },
		prompt_state: { prompt: 'a cat' },
		...overrides
	};
}

describe('buildImportBundleTabData', () => {
	it('restores preset/mode/formData/prompt_state from the import payload', () => {
		const { tabData, backendUnavailable } = buildImportBundleTabData(makeImportReuse());

		expect(tabData.selectedPreset).toBe('sdxl/example/base');
		expect(tabData.selectedMode).toBe('txt2img');
		expect(tabData.formData).toMatchObject({ seed: 42, steps: 20 });
		expect((tabData as any).prompt).toBe('a cat');
		expect(backendUnavailable).toBe(false);
	});

	it('restores selectedVariant when form_name is present', () => {
		const { tabData } = buildImportBundleTabData(makeImportReuse({ form_name: 'quality' }));
		expect(tabData.selectedVariant).toBe('quality');
	});

	it('never sets selectedBackendId — an import never carries a backend_id', () => {
		const { tabData, backendUnavailable } = buildImportBundleTabData(makeImportReuse());
		expect(tabData.selectedBackendId).toBeUndefined();
		expect(backendUnavailable).toBe(false);
	});
});
