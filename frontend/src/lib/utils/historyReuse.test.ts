import { describe, it, expect } from 'vitest';
import {
	buildHistoryReuseTabData,
	buildImportBundleTabData,
	buildActiveTabReuseUpdate,
	buildReuseTabTitle,
	resolveReusePresetLabel
} from './historyReuse';
import type { GenerationHistoryItem, ImportBundleReuse } from '$lib/types/history';

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

describe('buildHistoryReuseTabData', () => {
	it('restores preset/mode/formData/prompt_state', () => {
		const generation = makeGeneration();
		const { tabData } = buildHistoryReuseTabData(generation);

		expect(tabData.selectedPreset).toBe('sdxl/example/base');
		expect(tabData.selectedMode).toBe('txt2img');
		expect(tabData.formData).toMatchObject({ seed: 42, steps: 20 });
		expect((tabData as any).prompt).toBe('a cat');
	});

	it('defaults mode to txt2img when missing', () => {
		const generation = makeGeneration({ mode: undefined });
		const { tabData } = buildHistoryReuseTabData(generation);
		expect(tabData.selectedMode).toBe('txt2img');
	});

	it('restores selectedVariant when form_name is present', () => {
		const generation = makeGeneration({ form_name: 'quality' });
		const { tabData } = buildHistoryReuseTabData(generation);
		expect(tabData.selectedVariant).toBe('quality');
	});

	it('sets selectedVariant to null when form_name is absent/null', () => {
		const withoutFormName = buildHistoryReuseTabData(makeGeneration({ form_name: undefined }));
		expect(withoutFormName.tabData.selectedVariant).toBeNull();

		const withNullFormName = buildHistoryReuseTabData(makeGeneration({ form_name: null }));
		expect(withNullFormName.tabData.selectedVariant).toBeNull();
	});

	it('restores a positive seed onto both the tab and formData', () => {
		const generation = makeGeneration({ seed: 12345, form_data: { seed: 12345, steps: 30 } });
		const { tabData } = buildHistoryReuseTabData(generation);

		expect(tabData.seed).toBe(12345);
		expect((tabData.formData as Record<string, unknown>).seed).toBe(12345);
	});

	it('round-trips a -1 (randomize) seed as -1, not coerced to something else', () => {
		const generation = makeGeneration({ seed: -1, form_data: { seed: -1, steps: 30 } });
		const { tabData } = buildHistoryReuseTabData(generation);

		expect(tabData.seed).toBe(-1);
		expect((tabData.formData as Record<string, unknown>).seed).toBe(-1);
	});

	it('leaves seed and formData.seed untouched when generation.seed is null/absent', () => {
		const generation = makeGeneration({ seed: null, form_data: { steps: 30 } });
		const { tabData } = buildHistoryReuseTabData(generation);

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
		const { tabData } = buildImportBundleTabData(makeImportReuse());

		expect(tabData.selectedPreset).toBe('sdxl/example/base');
		expect(tabData.selectedMode).toBe('txt2img');
		expect(tabData.formData).toMatchObject({ seed: 42, steps: 20 });
		expect((tabData as any).prompt).toBe('a cat');
	});

	it('restores selectedVariant when form_name is present', () => {
		const { tabData } = buildImportBundleTabData(makeImportReuse({ form_name: 'quality' }));
		expect(tabData.selectedVariant).toBe('quality');
	});
});

describe('buildActiveTabReuseUpdate', () => {
	it('mirrors buildHistoryReuseTabData\'s preset/mode/formData/prompt/seed fields', () => {
		const generation = makeGeneration({ seed: 12345, form_data: { seed: 12345, steps: 30 } });
		const fresh = buildHistoryReuseTabData(generation);
		const { tabData } = buildActiveTabReuseUpdate(generation, { selectedPreset: null });

		expect(tabData.selectedPreset).toBe(fresh.tabData.selectedPreset);
		expect(tabData.selectedMode).toBe(fresh.tabData.selectedMode);
		expect(tabData.selectedVariant).toBe(fresh.tabData.selectedVariant);
		expect(tabData.formData).toEqual(fresh.tabData.formData);
		expect(tabData.seed).toBe(fresh.tabData.seed);
		expect((tabData as any).prompt).toBe((fresh.tabData as any).prompt);
	});

	it('flags presetChanged when the generation preset differs from the tab', () => {
		const generation = makeGeneration({ preset_id: 'sdxl/example/base' });
		const { presetChanged } = buildActiveTabReuseUpdate(generation, {
			selectedPreset: 'flux/other/variant'
		});
		expect(presetChanged).toBe(true);
	});

	it('does not flag presetChanged when the tab is already on that preset', () => {
		const generation = makeGeneration({ preset_id: 'sdxl/example/base' });
		const { presetChanged } = buildActiveTabReuseUpdate(generation, {
			selectedPreset: 'sdxl/example/base'
		});
		expect(presetChanged).toBe(false);
	});

	it('resets cross-preset tab state (session, source prompt, per-mode cache, segment collapse)', () => {
		const generation = makeGeneration();
		const { tabData } = buildActiveTabReuseUpdate(generation, { selectedPreset: null });

		expect(tabData.selectedSessionId).toBeNull();
		expect(tabData.savedSessionSignature).toBeNull();
		expect(tabData.sessionBaselineAwaitingFormNormalization).toBe(false);
		expect(tabData.sourcePromptId).toBeNull();
		expect(tabData.positiveSegmentsCollapsed).toBeUndefined();
		expect(tabData.negativeSegmentsCollapsed).toBeUndefined();
		expect(tabData.modeStateByMode).toEqual({});
	});
});

describe('resolveReusePresetLabel', () => {
	it('prefers the given preset_name over any resolver lookup', () => {
		const label = resolveReusePresetLabel(
			{ preset_id: '4TK1KBQZ2XMB8ME0PTMXS1YJQP', preset_name: 'Krea-2' },
			() => 'Some Other Name'
		);
		expect(label).toBe('Krea-2');
	});

	it('falls back to the resolver when preset_name is absent', () => {
		const label = resolveReusePresetLabel(
			{ preset_id: '4TK1KBQZ2XMB8ME0PTMXS1YJQP' },
			(id) => (id === '4TK1KBQZ2XMB8ME0PTMXS1YJQP' ? 'Krea-2' : null)
		);
		expect(label).toBe('Krea-2');
	});

	it('falls back to "Unknown preset", never the raw id', () => {
		const label = resolveReusePresetLabel({ preset_id: '4TK1KBQZ2XMB8ME0PTMXS1YJQP' }, () => null);
		expect(label).toBe('Unknown preset');
		expect(label).not.toContain('4TK1KBQZ2XMB8ME0PTMXS1YJQP');
	});

	it('falls back to "Unknown preset" with no preset_id at all', () => {
		expect(resolveReusePresetLabel({}, () => null)).toBe('Unknown preset');
	});
});

describe('buildReuseTabTitle', () => {
	it('builds "<prefix>: <name>" from a resolved preset name', () => {
		const title = buildReuseTabTitle('Reused', { preset_id: 'p1', preset_name: 'Krea-2' }, () => null);
		expect(title).toBe('Reused: Krea-2');
	});

	it('never surfaces the raw ULID when the name cannot be resolved', () => {
		const title = buildReuseTabTitle('Reused', { preset_id: '4TK1KBQZ2XMB8ME0PTMXS1YJQP' }, () => null);
		expect(title).toBe('Reused: Unknown preset');
	});
});

describe('reusing a Long video generation', () => {
	const longRow = () =>
		makeGeneration({
			preset_id: 'h3',
			mode: 'long',
			form_name: 'custom',
			prompt_state: { prompt: 'a paper boat' },
			seed: 99,
			form_data: {
				seed: 99,
				total_seconds: 30,
				piece_seconds: 10,
				piece_prompts: 'drifting\n',
				start_image: '/storage/boat.png',
				soundtrack: '/storage/rain.mp3',
				speed_profile: 'quality',
				drift_correction: 0.5,
				resolution: '832x480'
			}
		});

	it('opens it as Video Director shots on the Video Director mode', () => {
		const { tabData, converted } = buildHistoryReuseTabData(longRow());

		expect(tabData.selectedPreset).toBe('h3');
		expect(tabData.selectedMode).toBe('video');
		expect(tabData.selectedVariant).toBeNull();
		const chain = (tabData.videoDirector as any).chain;
		expect(chain.segments.map((s: any) => s.prompt)).toEqual(['drifting', 'a paper boat', 'a paper boat', 'a paper boat']);
		expect(chain.segments[0].keyframe).toEqual({ path: '/storage/boat.png', type: 'image' });
		expect(chain.audio[0].media).toEqual({ path: '/storage/rain.mp3', type: 'audio' });
		expect(converted?.from).toBe('long');
		expect(converted?.to).toBe('video');
	});

	it('hands the shared settings to the Director form and drops the Long-only ones', () => {
		const { tabData } = buildHistoryReuseTabData(longRow());

		expect(tabData.formData).toEqual({
			seed: 99,
			speed_profile: 'quality',
			drift_correction: 0.5,
			resolution: '832x480'
		});
		expect(tabData.seed).toBe(99);
	});

	it('also converts it when reused in place on the open tab', () => {
		const result = buildActiveTabReuseUpdate(longRow(), { selectedPreset: 'h3' });

		expect(result.tabData.selectedMode).toBe('video');
		expect(result.converted?.notes.length).toBeGreaterThan(0);
	});

	it('converts an imported bundle the same way', () => {
		const { tabData, converted } = buildImportBundleTabData({
			preset_id: 'h3',
			mode: 'long',
			form_data: { total_seconds: 12, piece_seconds: 5, piece_prompts: '' },
			prompt_state: { prompt: 'x' }
		} as unknown as ImportBundleReuse);

		expect(tabData.selectedMode).toBe('video');
		expect(converted?.from).toBe('long');
	});

	it('leaves a row that merely shares the mode name but not the setup alone', () => {
		const { tabData, converted } = buildHistoryReuseTabData(
			makeGeneration({ mode: 'long', form_data: { seed: 1, steps: 8 } })
		);

		expect(tabData.selectedMode).toBe('long');
		expect(converted).toBeUndefined();
	});

	it('does not touch an ordinary row', () => {
		expect(buildHistoryReuseTabData(makeGeneration()).converted).toBeUndefined();
	});
});
