import { describe, it, expect, vi } from 'vitest';
import { get } from 'svelte/store';
import {
	buildSegmentInput,
	buildSegmentsPayload,
	buildPromptsArray,
	buildVariablesPayload,
	resolveSegmentText,
	startGeneration,
	mapGenerationFiles
} from './generationOrchestrator';
import type { Segment, ChipData } from '$lib/types/segments';
import type { Tab } from '$lib/types/tabs';
import { tabsStore, activeTab } from '$lib/stores/tabs';

function chip(
	partial: Partial<ChipData> & {
		categoryPath: string;
		valueId: string;
		value: string;
	}
): ChipData {
	return {
		id: partial.valueId,
		label: partial.value,
		allValues: [],
		shuffle: false,
		autoRegen: false,
		...partial
	} as ChipData;
}

function segment(partial: Partial<Segment> & { id: string }): Segment {
	return { content: '', type: 'content', ...partial };
}

// Minimal Tab factory — only the fields buildSegmentsPayload reads matter.
function makeTab(partial: Partial<Tab>): Tab {
	return {
		id: 't1',
		name: 'Tab',
		selectedPreset: 'p',
		selectedMode: 'txt2img',
		prompt: '',
		negativePrompt: '',
		formData: {},
		generation: {} as any,
		workbenchMaxHeight: '',
		leftPanelWidth: 0,
		...partial
	} as Tab;
}

describe('resolveSegmentText', () => {
	it('returns raw content when there are no chips', () => {
		expect(resolveSegmentText(segment({ id: 's', content: 'a plain prompt' }))).toBe('a plain prompt');
	});

	it('resolves chip placeholders to their values', () => {
		const seg = segment({
			id: 's',
			content: 'a #color b',
			chips: {
				c1: chip({ categoryPath: 'color', valueId: 'v1', value: 'red' })
			}
		});
		expect(resolveSegmentText(seg)).toBe('a red b');
	});
});

describe('buildSegmentInput', () => {
	it('maps a plain content segment', () => {
		const out = buildSegmentInput(segment({ id: 's', content: 'hello' }), 'positive', 0, 0);
		expect(out).toEqual({
			channel: 'positive',
			prompt_index: 0,
			segment_index: 0,
			segment_type: 'content',
			text: 'hello',
			is_disabled: false,
			name: null,
			color: null,
			description: null,
			phrasebooks: []
		});
	});

	it('marks break segments', () => {
		const out = buildSegmentInput(segment({ id: 's', type: 'break', content: 'BREAK' }), 'positive', 0, 2);
		expect(out.segment_type).toBe('break');
		expect(out.segment_index).toBe(2);
	});

	it('persists disabled state independently from collapsed UI state', () => {
		expect(buildSegmentInput(segment({ id: 's', isDisabled: true }), 'positive', 0, 0).is_disabled).toBe(true);
		expect(buildSegmentInput(segment({ id: 's', enabled: false }), 'positive', 0, 0).is_disabled).toBe(true);
		expect(buildSegmentInput(segment({ id: 's', isCollapsed: true }), 'positive', 0, 0).is_disabled).toBe(false);
	});

	it('copies detached segment metadata', () => {
		const out = buildSegmentInput(
			segment({
				id: 's',
				content: 'portrait',
				name: 'Subject',
				color: '#ff00aa',
				description: 'Primary subject details'
			}),
			'positive',
			0,
			0
		);

		expect(out.name).toBe('Subject');
		expect(out.color).toBe('#ff00aa');
		expect(out.description).toBe('Primary subject details');
	});

	it('maps chips to the phrasebook contract shape', () => {
		const seg = segment({
			id: 's',
			content: '#palette.color dress',
			chips: {
				c1: chip({
					categoryPath: 'palette.color',
					valueId: 'val-9',
					value: 'crimson'
				})
			}
		});
		const out = buildSegmentInput(seg, 'negative', 1, 3);
		expect(out.channel).toBe('negative');
		expect(out.text).toBe('crimson dress');
		expect(out.phrasebooks).toEqual([
			{
				phrasebook_value_id: 'val-9',
				category_path: 'palette.color',
				value: 'crimson'
			}
		]);
	});
});

describe('buildSegmentsPayload', () => {
	it('flattens single-prompt positive and negative channels in order', () => {
		const tab = makeTab({
			promptSegments: [segment({ id: 'p0', content: 'first' }), segment({ id: 'p1', content: 'second' })],
			negativePromptSegments: [segment({ id: 'n0', content: 'bad' })]
		});
		const out = buildSegmentsPayload(tab, 1);
		expect(out).toHaveLength(3);
		expect(out.map((s) => [s.channel, s.prompt_index, s.segment_index, s.text])).toEqual([
			['positive', 0, 0, 'first'],
			['positive', 0, 1, 'second'],
			['negative', 0, 0, 'bad']
		]);
	});

	it('emits per-prompt-tab segments with prompt_index in multi-prompt mode', () => {
		const tab = makeTab({
			promptTabs: [
				{
					promptSegments: [segment({ id: 'a', content: 'p0-pos' })],
					negativePromptSegments: [segment({ id: 'b', content: 'p0-neg' })],
					prompt: '',
					negativePrompt: ''
				},
				{
					promptSegments: [segment({ id: 'c', content: 'p1-pos' })],
					negativePromptSegments: [],
					prompt: '',
					negativePrompt: ''
				}
			]
		});
		const out = buildSegmentsPayload(tab, 2);
		expect(out).toHaveLength(3);
		expect(out.map((s) => [s.channel, s.prompt_index, s.text])).toEqual([
			['positive', 0, 'p0-pos'],
			['negative', 0, 'p0-neg'],
			['positive', 1, 'p1-pos']
		]);
	});

	it('returns an empty array when there are no segments', () => {
		expect(buildSegmentsPayload(makeTab({}), 1)).toEqual([]);
	});
});

describe('buildPromptsArray', () => {
	it('reports shuffled chips from either multi-prompt channel', () => {
		const shuffled = chip({
			categoryPath: 'color',
			valueId: 'red',
			value: 'red'
		});
		shuffled.shuffle = true;
		shuffled.allValues = [
			{ id: 'red', label: 'Red', value: 'red' },
			{ id: 'blue', label: 'Blue', value: 'blue' }
		];
		const tab = makeTab({
			promptTabs: [
				{
					prompt: '',
					negativePrompt: '',
					promptSegments: [
						segment({
							id: 'positive',
							content: '#color',
							chips: { color: shuffled }
						})
					],
					negativePromptSegments: []
				}
			]
		});

		expect(buildPromptsArray(tab, 2).hasShuffled).toBe(true);
	});

	it('defaults to a comma join, and threads a paragraph join into the submitted positive prompt', () => {
		const tab = makeTab({
			promptSegments: [
				segment({ id: 'verse', content: '[Verse]\nrain on the window' }),
				segment({ id: 'chorus', content: '[Chorus]\nnowhere to go' })
			],
			negativePromptSegments: []
		});

		expect(buildPromptsArray(tab, 1).prompts[0].positive).toBe(
			'[Verse]\nrain on the window, [Chorus]\nnowhere to go'
		);
		expect(buildPromptsArray(tab, 1, 'paragraph').prompts[0].positive).toBe(
			'[Verse]\nrain on the window\n\n[Chorus]\nnowhere to go'
		);
	});
});

describe('buildVariablesPayload', () => {
	it('omits variables entirely when the tab has none', () => {
		expect(buildVariablesPayload(makeTab({})).variables).toBeUndefined();
	});

	it('omits variables when the map is present but empty', () => {
		expect(buildVariablesPayload(makeTab({ variables: {} })).variables).toBeUndefined();
	});

	it('passes through a non-empty text variables map, with no roll reported', () => {
		const result = buildVariablesPayload(makeTab({ variables: { mood: '{noir|sunlit}' } }));
		expect(result.variables).toEqual({ mood: '{noir|sunlit}' });
		expect(result.rolls).toEqual({});
	});

	// A choice variable has a mode (shuffle | pin | per-image).
	it('serializes a per-image-mode choice variable to {a|b|c} for the wire', () => {
		const result = buildVariablesPayload(
			makeTab({
				variables: { palette: { type: 'choice', options: ['warm', 'cool', ''], mode: 'per-image', pinnedIndex: null } }
			})
		);
		expect(result.variables).toEqual({ palette: '{warm|cool}' });
		expect(result.rolls).toEqual({});
	});

	it('serializes a pinned choice variable to just its option text', () => {
		const result = buildVariablesPayload(
			makeTab({
				variables: { palette: { type: 'choice', options: ['warm', 'cool'], mode: 'pin', pinnedIndex: 1 } }
			})
		);
		expect(result.variables).toEqual({ palette: 'cool' });
		expect(result.rolls).toEqual({});
	});

	it('serializes a typed text variable to its plain value', () => {
		const result = buildVariablesPayload(makeTab({ variables: { era: { type: 'text', value: 'victorian' } } }));
		expect(result.variables).toEqual({ era: 'victorian' });
	});

	// shuffle is the default mode — rolls ONCE per call
	// (a call only ever happens at submit time, so this IS "once per Generate
	// click"), sends the plain rolled value (no braces), and reports the roll
	// for the caller to persist as run state so usage chips can re-render.
	it('rolls a shuffle-mode choice variable and reports it, sending the plain value with no braces', () => {
		const result = buildVariablesPayload(
			makeTab({ variables: { mood: { type: 'choice', options: ['noir', 'sunlit'], mode: 'shuffle', pinnedIndex: null } } }),
			{ random: () => 0.9, now: () => 999 }
		);
		expect(result.variables).toEqual({ mood: 'sunlit' });
		expect(result.rolls).toEqual({ mood: { optionIndex: 1, value: 'sunlit', rolledAt: 999 } });
	});

	// Regression for the live-testing bug: the Variable Manager modal writes to
	// tabsStore via `tabsStore.updateTab(tab.id, { variables })`, and the actual
	// Generate button in generate/+page.svelte reads the tab back out through
	// the `activeTab` derived store (`$: currentTab = $activeTab`), NOT a
	// snapshot captured earlier. This exercises that exact real store, not a
	// hand-built Tab object, to pin "a variable saved in the manager is present
	// in the submitted request payload" end to end.
	it('reflects a variable saved through tabsStore.updateTab by the time the tab is read back for submission', () => {
		tabsStore.reset();
		const tab = get(activeTab);

		// Mirrors VariableManagerModal's `change` handler in PromptSection.svelte.
		tabsStore.updateTab(tab.id, { variables: { mood: '{noir|sunlit}' } });

		// Mirrors generate/+page.svelte's `$: currentTab = $activeTab` read at
		// submit time — a fresh read of the store, not the `tab` captured above.
		const currentTab = get(activeTab);
		expect(buildVariablesPayload(currentTab).variables).toEqual({ mood: '{noir|sunlit}' });
	});

	it('does not leak a variable saved on a different tab', () => {
		tabsStore.reset();
		const firstTabId = get(activeTab).id;
		tabsStore.addTab();
		const secondTab = get(activeTab);
		expect(secondTab.id).not.toBe(firstTabId);

		tabsStore.updateTab(firstTabId, { variables: { mood: '{noir|sunlit}' } });

		expect(buildVariablesPayload(get(activeTab)).variables).toBeUndefined();
	});

	// The exact "Generate-click updates lastRoll and the payload carries the
	// rolled plain value" contract: whatever value ends up in `.variables` for
	// a shuffle-mode variable must be EXACTLY the value recorded in `.rolls`
	// for that same name — the caller persists `.rolls` as `Tab.variableRolls`
	// and chips re-render from it, so any mismatch here would mean the chip
	// shows a different value than what the generation actually used.
	it('the roll persisted for the caller always matches the value actually sent', () => {
		const tab = makeTab({
			variables: {
				mood: { type: 'choice', options: ['noir', 'sunlit', 'pastel'], mode: 'shuffle', pinnedIndex: null }
			}
		});
		const result = buildVariablesPayload(tab, { random: () => 0.5 });
		expect(result.variables?.mood).toBe(result.rolls.mood.value);
	});
});

describe('startGeneration', () => {
	it('includes the tab variables map in the generation request', async () => {
		const startGenerationRequest = vi.fn().mockResolvedValue({
			success: true,
			data: { generation_id: 'generation-1', status: { status: 'running' } }
		});
		const tab = makeTab({
			prompt: 'portrait',
			variables: { mood: '{noir|sunlit}' }
		});

		await startGeneration(
			{ tab, activeTabId: tab.id, numPrompts: 1 },
			{
				api: {
					startGeneration: startGenerationRequest,
					cancelGeneration: vi.fn(),
					getGenerationStatus: vi.fn(),
					getGenerationById: vi.fn()
				}
			}
		);

		expect(startGenerationRequest).toHaveBeenCalledWith(
			expect.objectContaining({ variables: { mood: '{noir|sunlit}' } })
		);
	});

	it('includes selected auto-collections in the generation request', async () => {
		const startGenerationRequest = vi.fn().mockResolvedValue({
			success: true,
			data: { generation_id: 'generation-1', status: { status: 'running' } }
		});
		const tab = makeTab({
			prompt: 'portrait',
			autoCollectionIds: ['collection-1', 'collection-2']
		});

		await startGeneration(
			{ tab, activeTabId: tab.id, numPrompts: 1 },
			{
				api: {
					startGeneration: startGenerationRequest,
					cancelGeneration: vi.fn(),
					getGenerationStatus: vi.fn(),
					getGenerationById: vi.fn()
				}
			}
		);

		expect(startGenerationRequest).toHaveBeenCalledWith(
			expect.objectContaining({ collection_ids: ['collection-1', 'collection-2'] })
		);
	});
});

describe('mapGenerationFiles', () => {
	// The history API serializes `files` DB rows verbatim: file_type is
	// UPPERCASE and there is no `url` — only `file_path`. These fixtures use
	// that real payload shape; lowercasing them here would make the tests
	// unable to catch the casing bug they exist for.
	const genId = '01TESTGENID';

	it('maps an UPPERCASE MESH row to a mesh gallery item with a served URL', () => {
		const { images, videos, meshes, totalItems } = mapGenerationFiles(
			[
				{
					file_type: 'MESH',
					file_path: `generations/2026-07-27/${genId}/1.glb`,
					is_derived: false
				}
			],
			genId
		);

		expect(images).toEqual([]);
		expect(videos).toEqual([]);
		expect(totalItems).toBe(1);
		expect(meshes).toHaveLength(1);
		expect(meshes[0].url).toBe(`/api/media/generations/${genId}/1.glb`);
		expect(meshes[0].originalUrl).toBe(meshes[0].url);
		expect(meshes[0].file_type).toBe('mesh');
		expect(meshes[0].mesh_name).toBe('1.glb');
		expect(meshes[0].mesh_format).toBe('glb');
	});

	it('prefers a real server-sent mesh_format over the file_path extension, even when they disagree', () => {
		const { meshes } = mapGenerationFiles(
			[
				{
					file_type: 'MESH',
					file_path: `generations/2026-07-27/${genId}/1.glb`,
					mesh_format: 'ply',
					is_derived: false
				}
			],
			genId
		);

		expect(meshes[0].mesh_format).toBe('ply');
	});

	it('derives mesh_format from a non-glb file_path extension when no field is sent', () => {
		const { meshes } = mapGenerationFiles(
			[
				{
					file_type: 'MESH',
					file_path: `generations/2026-07-27/${genId}/1.ply`,
					is_derived: false
				}
			],
			genId
		);

		expect(meshes[0].mesh_format).toBe('ply');
	});

	it('matches file_type case-insensitively for images and videos too', () => {
		const { images, videos, totalItems } = mapGenerationFiles(
			[
				{ file_type: 'IMAGE', file_path: `generations/2026-07-27/${genId}/0.png` },
				{ file_type: 'VIDEO', file_path: `generations/2026-07-27/${genId}/1.mp4` }
			],
			genId
		);

		expect(images).toHaveLength(1);
		expect(images[0].url).toBe(`/api/media/generations/${genId}/0.png`);
		expect(videos).toHaveLength(1);
		expect(videos[0].url).toBe(`/api/media/generations/${genId}/1.mp4`);
		expect(totalItems).toBe(2);
	});

	it('maps an UPPERCASE AUDIO row to an audio gallery item with a served URL', () => {
		const { audios, totalItems } = mapGenerationFiles(
			[
				{
					file_type: 'AUDIO',
					file_path: `generations/2026-07-27/${genId}/0.wav`,
					duration_seconds: 12.5,
					sample_rate: 44100,
					channels: 2,
					track_type: 'speech',
					seed: 42
				}
			],
			genId
		);

		expect(totalItems).toBe(1);
		expect(audios).toHaveLength(1);
		expect(audios[0].url).toBe(`/api/media/generations/${genId}/0.wav`);
		expect(audios[0].originalUrl).toBe(audios[0].url);
		expect(audios[0].file_type).toBe('audio');
		expect(audios[0].duration).toBe(12.5);
		expect(audios[0].sample_rate).toBe(44100);
		expect(audios[0].channels).toBe(2);
		expect(audios[0].track_type).toBe('speech');
		expect(audios[0].seed).toBe(42);
	});

	it('prefers an explicit url and falls back to an index-based name without file_path', () => {
		const { images, meshes } = mapGenerationFiles(
			[
				{ file_type: 'image', url: '/api/custom/0.png' },
				{ file_type: 'mesh' }
			],
			genId
		);

		expect(images[0].url).toBe('/api/custom/0.png');
		expect(meshes[0].url).toBe(`/api/media/generations/${genId}/0.glb`);
	});

	it('returns empty batches for empty or typeless input', () => {
		expect(mapGenerationFiles([], genId).totalItems).toBe(0);
		expect(mapGenerationFiles([{ file_path: 'x.bin' }], genId).totalItems).toBe(0);
	});
});
