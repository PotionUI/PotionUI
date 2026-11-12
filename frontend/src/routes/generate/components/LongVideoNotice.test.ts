import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import LongVideoNotice from './LongVideoNotice.svelte';

const longState = {
	prompt: 'x',
	negativePrompt: '',
	promptSegments: [],
	negativePromptSegments: [],
	formData: { total_seconds: 30, piece_seconds: 10, piece_prompts: '' }
};

const html = (props: Record<string, unknown>) => render(LongVideoNotice, { props: props as any }).body;
const modes = ['txt2video', 'img2video', 'first_last', 'video'];

describe('LongVideoNotice', () => {
	it('offers the one-click action when the tab holds a Long video setup', () => {
		const out = html({ tab: { id: 't1', name: 'Boats', selectedPreset: 'h3', modeStateByMode: { long: longState } }, modeNames: modes });
		expect(out).toContain('Open in Video Director');
		expect(out).toContain('Long video setup');
	});

	it('renders nothing when there is no Long video setup', () => {
		const out = html({ tab: { id: 't1', name: 'Boats', selectedPreset: 'h3', modeStateByMode: {} }, modeNames: modes });
		expect(out).not.toContain('Open in Video Director');
	});

	it('renders nothing while the preset still offers Long video', () => {
		const out = html({
			tab: { id: 't1', name: 'Boats', selectedPreset: 'h3', modeStateByMode: { long: longState } },
			modeNames: [...modes, 'long']
		});
		expect(out).not.toContain('Open in Video Director');
	});
});
