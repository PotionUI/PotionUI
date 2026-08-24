import { describe, it, expect } from 'vitest';
import {
	buildModeSwitchPatch,
	captureModeState,
	emptyModeState,
	mergeCachedModesIntoSessionData,
	modeStateFromSessionData,
	seedModeStateFromSessionData
} from './modeState';
import type { Tab } from '$lib/types/tabs';
import type { ModeBasedSessionData } from '$lib/types/api';

function makeTab(overrides: Partial<Tab> = {}): Tab {
	return {
		id: 't1',
		name: 'Generation 1',
		selectedPreset: 'native/Wan/default',
		selectedMode: 'txt2img',
		prompt: '',
		negativePrompt: '',
		promptSegments: [],
		negativePromptSegments: [],
		formData: {},
		generation: {
			isGenerating: false,
			currentGeneration: null,
			currentProgress: null,
			pipeTimers: {},
			startedAt: null,
			totalTime: null,
			lastDurationMs: null,
			batchImages: [],
			batchVideos: [],
			batchAudios: [],
			artifacts: [],
			workbenchIndex: 0,
			workbenchTotal: 0,
			queue: [],
			submittedPromptTemplate: null
		},
		workbenchMaxHeight: '600',
		leftPanelWidth: 380,
		layoutMode: 'two',
		promptPanelWidth: 420,
		...overrides
	};
}

describe('captureModeState', () => {
	it('defaults missing segment arrays and formData to empty', () => {
		const tab = makeTab({ promptSegments: undefined, negativePromptSegments: undefined, formData: undefined as any });
		expect(captureModeState(tab)).toEqual({
			prompt: '',
			negativePrompt: '',
			promptSegments: [],
			negativePromptSegments: [],
			promptTabs: undefined,
			activePromptTab: undefined,
			formData: {}
		});
	});
});

describe('buildModeSwitchPatch', () => {
	it('starts a never-visited mode empty, including formData', () => {
		const tab = makeTab({
			selectedMode: 'txt2img',
			prompt: 'a cat',
			promptSegments: [{ id: 's1', content: 'a cat' } as any],
			formData: { steps: 30, seed: 123 }
		});

		const patch = buildModeSwitchPatch(tab, 'txt2img', 'img2img');

		expect(patch.prompt).toBe('');
		expect(patch.promptSegments).toEqual([]);
		expect(patch.formData).toEqual({});
		expect(patch.modeStateByMode?.txt2img).toEqual(captureModeState(tab));
	});

	it('restores a previously visited mode on return, including formData', () => {
		let tab = makeTab({
			selectedMode: 'txt2img',
			prompt: 'a cat',
			promptSegments: [{ id: 's1', content: 'a cat' } as any],
			formData: { steps: 30 }
		});

		// txt2img -> img2img: txt2img's content is snapshotted, img2img starts empty
		let patch = buildModeSwitchPatch(tab, 'txt2img', 'img2img');
		tab = { ...tab, ...patch, selectedMode: 'img2img' };
		expect(tab.prompt).toBe('');
		expect(tab.formData).toEqual({});

		// Edit img2img's own prompt and form
		tab = { ...tab, prompt: 'a dog', promptSegments: [{ id: 's2', content: 'a dog' } as any], formData: { steps: 8 } };

		// img2img -> txt2img: img2img snapshotted, txt2img restored from cache
		patch = buildModeSwitchPatch(tab, 'img2img', 'txt2img');
		tab = { ...tab, ...patch, selectedMode: 'txt2img' };

		expect(tab.prompt).toBe('a cat');
		expect(tab.promptSegments).toEqual([{ id: 's1', content: 'a cat' }]);
		expect(tab.formData).toEqual({ steps: 30 });
		expect(tab.modeStateByMode?.img2img.formData).toEqual({ steps: 8 });
	});

	it('is a no-op when the mode does not actually change', () => {
		const tab = makeTab({ selectedMode: 'txt2img', prompt: 'a cat', formData: { steps: 30 } });
		const patch = buildModeSwitchPatch(tab, 'txt2img', 'txt2img');
		expect(patch).toEqual({});
	});

	it('does not snapshot when there is no prior mode (first selection)', () => {
		const tab = makeTab({ selectedMode: null as unknown as string, prompt: '' });
		const patch = buildModeSwitchPatch(tab, null, 'txt2img');
		expect(patch.modeStateByMode).toEqual({});
		expect(patch.prompt).toBe('');
		expect(patch.formData).toEqual({});
	});

	it('carries multi-prompt tab state along with the switch', () => {
		const tab = makeTab({
			selectedMode: 'txt2img',
			promptTabs: [{ prompt: 'a', negativePrompt: '', promptSegments: [], negativePromptSegments: [] }],
			activePromptTab: 0
		});
		const patch = buildModeSwitchPatch(tab, 'txt2img', 'img2img');
		expect(patch.promptTabs).toBeUndefined();
		expect(patch.modeStateByMode?.txt2img.promptTabs).toEqual(tab.promptTabs);
		expect(patch.modeStateByMode?.txt2img.activePromptTab).toBe(0);
	});
});

describe('emptyModeState', () => {
	it('matches the shape a fresh tab starts with', () => {
		expect(emptyModeState()).toEqual({
			prompt: '',
			negativePrompt: '',
			promptSegments: [],
			negativePromptSegments: [],
			formData: {}
		});
	});
});

describe('modeStateFromSessionData', () => {
	it('converts a session mode payload into cache shape', () => {
		expect(
			modeStateFromSessionData({
				prompt: 'a cat',
				negativePrompt: 'blurry',
				promptSegments: [{ id: 's1', content: 'a cat' } as any],
				negativePromptSegments: [],
				formData: { steps: 30 }
			})
		).toEqual({
			prompt: 'a cat',
			negativePrompt: 'blurry',
			promptSegments: [{ id: 's1', content: 'a cat' }],
			negativePromptSegments: [],
			promptTabs: undefined,
			activePromptTab: undefined,
			formData: { steps: 30 }
		});
	});

	it('falls back to the pre-rename segments/negativeSegments keys', () => {
		const state = modeStateFromSessionData({
			segments: [{ id: 's1', content: 'legacy' } as any],
			negativeSegments: [{ id: 'n1', content: 'legacy neg' } as any]
		});
		expect(state.promptSegments).toEqual([{ id: 's1', content: 'legacy' }]);
		expect(state.negativePromptSegments).toEqual([{ id: 'n1', content: 'legacy neg' }]);
	});

	it('defaults an empty payload', () => {
		expect(modeStateFromSessionData({})).toEqual(emptyModeState());
	});
});

describe('seedModeStateFromSessionData', () => {
	it('converts every mode except the active one', () => {
		const sessionData: ModeBasedSessionData = {
			txt2img: { prompt: 'a cat', formData: { steps: 30 } },
			img2img: { prompt: 'a dog', formData: { steps: 8 } }
		};

		const seeded = seedModeStateFromSessionData(sessionData, 'txt2img');

		expect(Object.keys(seeded)).toEqual(['img2img']);
		expect(seeded.img2img).toEqual(modeStateFromSessionData(sessionData.img2img));
	});

	it('seeds every mode when there is no active mode yet', () => {
		const sessionData: ModeBasedSessionData = {
			txt2img: { prompt: 'a cat' },
			img2img: { prompt: 'a dog' }
		};
		const seeded = seedModeStateFromSessionData(sessionData, null);
		expect(Object.keys(seeded).sort()).toEqual(['img2img', 'txt2img']);
	});
});

describe('mergeCachedModesIntoSessionData', () => {
	it('overlays cached modes onto the saved baseline, preserving fields the cache does not track', () => {
		const baseline: ModeBasedSessionData = {
			img2img: { prompt: 'stale dog', formData: { steps: 8 }, seed: 42, selectedBackendId: 'backend-1' }
		};
		const modeStateByMode = {
			img2img: { ...emptyModeState(), prompt: 'a dog', formData: { steps: 12 } }
		};

		const merged = mergeCachedModesIntoSessionData(baseline, modeStateByMode);

		expect(merged.img2img.prompt).toBe('a dog');
		expect(merged.img2img.formData).toEqual({ steps: 12 });
		// Fields the live cache never captured stay exactly as the baseline had them.
		expect(merged.img2img.seed).toBe(42);
		expect(merged.img2img.selectedBackendId).toBe('backend-1');
	});

	it('adds a mode the baseline never had', () => {
		const merged = mergeCachedModesIntoSessionData({}, {
			img2img: { ...emptyModeState(), prompt: 'a dog' }
		});
		expect(merged.img2img.prompt).toBe('a dog');
	});

	it('is a no-op when there is nothing cached', () => {
		const baseline: ModeBasedSessionData = { txt2img: { prompt: 'a cat' } };
		expect(mergeCachedModesIntoSessionData(baseline, undefined)).toEqual(baseline);
	});
});

describe('two-mode session round trip', () => {
	it('a save captures both visited modes, and a load restores both', () => {
		// The user visits txt2img, configures it, switches to img2img (which
		// snapshots txt2img into the cache) and configures that too — never
		// switching back, so txt2img is only ever live in the cache.
		let tab = makeTab({
			selectedMode: 'txt2img',
			prompt: 'a cat',
			promptSegments: [{ id: 's1', content: 'a cat' } as any],
			formData: { steps: 30 }
		});
		const toImg2img = buildModeSwitchPatch(tab, 'txt2img', 'img2img');
		tab = {
			...tab,
			...toImg2img,
			selectedMode: 'img2img',
			prompt: 'a dog',
			promptSegments: [{ id: 's2', content: 'a dog' } as any],
			formData: { denoise: 0.5 }
		};

		// Save: the active mode (img2img) is captured live; txt2img comes from
		// the cache. No prior session, so the baseline is empty.
		const saved: ModeBasedSessionData = {
			...mergeCachedModesIntoSessionData({}, tab.modeStateByMode),
			[tab.selectedMode as string]: {
				prompt: tab.prompt,
				negativePrompt: tab.negativePrompt,
				promptSegments: tab.promptSegments,
				negativePromptSegments: tab.negativePromptSegments,
				formData: tab.formData
			}
		};

		expect(saved.txt2img.prompt).toBe('a cat');
		expect(saved.txt2img.formData).toEqual({ steps: 30 });
		expect(saved.img2img.prompt).toBe('a dog');
		expect(saved.img2img.formData).toEqual({ denoise: 0.5 });

		// Load into a brand new tab on img2img (the mode active at load time):
		// img2img's data goes straight onto the live fields, txt2img seeds the cache.
		const loadedTab = makeTab({ selectedMode: 'img2img' });
		const activeModeData = saved.img2img;
		const restoredTab: Tab = {
			...loadedTab,
			prompt: activeModeData.prompt || '',
			promptSegments: activeModeData.promptSegments || [],
			formData: activeModeData.formData || {},
			modeStateByMode: seedModeStateFromSessionData(saved, 'img2img')
		};

		expect(restoredTab.prompt).toBe('a dog');
		expect(restoredTab.formData).toEqual({ denoise: 0.5 });
		expect(restoredTab.modeStateByMode?.txt2img.prompt).toBe('a cat');
		expect(restoredTab.modeStateByMode?.txt2img.formData).toEqual({ steps: 30 });

		// Switching to txt2img now restores it from the seeded cache.
		const backToTxt2img = buildModeSwitchPatch(restoredTab, 'img2img', 'txt2img');
		expect(backToTxt2img.prompt).toBe('a cat');
		expect(backToTxt2img.formData).toEqual({ steps: 30 });
	});
});
