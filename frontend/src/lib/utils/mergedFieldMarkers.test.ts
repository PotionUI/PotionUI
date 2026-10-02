import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { Tab } from '$lib/types/tabs';
import { PROMPT_STATE_KEYS, tabMergedMarkerUpdates } from './mergedFieldMarkers';

const aliases = { old_clips: 'media_inputs', old_tracks: 'media_inputs' };

describe('tabMergedMarkerUpdates', () => {
	it('rewrites the prompt slice and keeps item keys', () => {
		const tab = {
			id: 't1',
			prompt: 'like @[old_clips:uploads/v.mp4]',
			negativePrompt: '',
			promptSegments: [
				{
					id: 's1',
					content: 'like @[old_clips:uploads/v.mp4] over @[old_tracks:uploads/t.wav]',
					resources: {
						m1: { field: 'old_clips', item_key: 'uploads/v.mp4' },
						m2: { field: 'other', item_key: 'x.png' }
					}
				}
			],
			formData: { notes: '@[old_clips:keep-form-data-out-of-this.mp4]' }
		} as unknown as Tab;

		const updates = tabMergedMarkerUpdates(tab, aliases)!;

		expect(updates.prompt).toBe('like @[media_inputs:uploads/v.mp4]');
		expect(updates.promptSegments?.[0].content).toBe(
			'like @[media_inputs:uploads/v.mp4] over @[media_inputs:uploads/t.wav]'
		);
		expect(updates.promptSegments?.[0].resources).toEqual({
			m1: { field: 'media_inputs', item_key: 'uploads/v.mp4' },
			m2: { field: 'other', item_key: 'x.png' }
		});
		expect('negativePrompt' in updates).toBe(false);
		expect('formData' in updates).toBe(false);
	});

	it('rewrites shot prompts inside a director document', () => {
		const tab = {
			videoDirector: { chain: { segments: [{ id: 'c1', prompt: '@[old_clips:v.mp4] walks', prompt_segments: [] }] } }
		} as unknown as Tab;

		const updates = tabMergedMarkerUpdates(tab, aliases)!;

		expect((updates.videoDirector as any).chain.segments[0].prompt).toBe('@[media_inputs:v.mp4] walks');
	});

	it('returns null when nothing changes, so a reactive caller settles', () => {
		const tab = { prompt: '@[media_inputs:v.mp4] @[other:x.png]', promptSegments: [{ id: 's', content: 'plain' }] } as unknown as Tab;

		expect(tabMergedMarkerUpdates(tab, aliases)).toBeNull();
		expect(tabMergedMarkerUpdates({ prompt: '@[old_clips:v.mp4]' } as unknown as Tab, {})).toBeNull();
	});
});

describe('PROMPT_STATE_KEYS', () => {
	const source = readFileSync(new URL('../types/tabs.ts', import.meta.url), 'utf8');
	const body = source.slice(source.indexOf('export interface Tab {'));
	const tabKeys = [...body.slice(0, body.indexOf('\n}')).matchAll(/^\t([A-Za-z]+)\??:/gm)].map((m) => m[1]);
	const notPromptText = ['promptPanelWidth', 'promptPanelWidthBeforeDirector', 'promptPanelWidthFolded', 'activePromptTab', 'sourcePromptId', 'directorRuns', 'directorRunLinks'];

	it('lists every prompt-bearing key of the tab type', () => {
		const promptBearing = tabKeys.filter((key) => /prompt|director|relay/i.test(key) && !notPromptText.includes(key));

		expect(tabKeys).toContain('promptRelay');
		expect([...PROMPT_STATE_KEYS].sort()).toEqual([...promptBearing].sort());
	});

	it('names only keys the tab type has', () => {
		for (const key of PROMPT_STATE_KEYS) expect(tabKeys).toContain(key);
	});
});
