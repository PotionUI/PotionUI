import { describe, expect, it } from 'vitest';
import { pendingLongVideo, planLongVideoTab } from './longVideoNotice';
import type { ModeState } from '$lib/types/tabs';

function stored(formData: Record<string, unknown>, prompt = 'a paper boat'): ModeState {
	return { prompt, negativePrompt: '', promptSegments: [], negativePromptSegments: [], formData };
}

const longForm = { total_seconds: 30, piece_seconds: 10, piece_prompts: '', speed_profile: 'turbo', seed: 5 };
const modes = ['txt2video', 'img2video', 'first_last', 'video', 'refs'];

describe('pendingLongVideo', () => {
	it('finds a stored Long video setup on a preset that no longer has the mode', () => {
		const long = stored(longForm);
		expect(pendingLongVideo({ modeStateByMode: { long } }, modes)).toBe(long);
	});

	it('finds nothing when the tab has no Long video setup', () => {
		expect(pendingLongVideo({ modeStateByMode: {} }, modes)).toBeNull();
		expect(pendingLongVideo({ modeStateByMode: undefined }, modes)).toBeNull();
	});

	it('ignores an entry that is not shaped like a Long video setup', () => {
		expect(pendingLongVideo({ modeStateByMode: { long: stored({ seed: 1 }) } }, modes)).toBeNull();
	});

	it('stays quiet while the preset still has the mode', () => {
		expect(pendingLongVideo({ modeStateByMode: { long: stored(longForm) } }, [...modes, 'long'])).toBeNull();
	});

	it('stays quiet when the preset has nowhere to open it', () => {
		expect(pendingLongVideo({ modeStateByMode: { long: stored(longForm) } }, ['txt2img'])).toBeNull();
		expect(pendingLongVideo({ modeStateByMode: { long: stored(longForm) } }, [])).toBeNull();
	});
});

describe('planLongVideoTab', () => {
	it('opens the setup on a new Video Director tab and keeps the stored entry untouched', () => {
		const long = stored({ ...longForm, start_image: '/s/a.png' });
		const before = JSON.stringify(long);
		const tab = { name: 'Boats', selectedPreset: 'h3', modeStateByMode: { long } };

		const plan = planLongVideoTab(tab, long);

		expect(plan.name).toBe('Boats: Video Director');
		expect(plan.tabData.selectedPreset).toBe('h3');
		expect(plan.tabData.selectedMode).toBe('video');
		expect(plan.tabData.formData).toEqual({ speed_profile: 'turbo', seed: 5 });
		expect((plan.tabData.videoDirector as any).chain.segments).toHaveLength(4);
		expect(plan.notes.length).toBeGreaterThan(0);
		expect(JSON.stringify(long)).toBe(before);
		expect(tab.modeStateByMode.long).toBe(long);
	});
});
