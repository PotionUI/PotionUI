import { describe, it, expect, vi, beforeEach } from 'vitest';

const assemble = vi.hoisted(() => vi.fn());

vi.mock('$lib/generation/requestAssembly', () => ({ assembleDirectorRequest: assemble }));

import { prepareRequest, prepareRequestFromSession } from './prepareRequest';

const videoCaps = { presetModes: ['video'] } as any;
const musicCaps = { presetModes: ['song'] } as any;

function tab(overrides: Record<string, unknown> = {}) {
	return {
		selectedPreset: 'preset-1',
		selectedMode: 'video',
		selectedVariant: null,
		formData: { seed: 3 },
		prompt: 'plain prompt',
		negativePrompt: '',
		promptSegments: [],
		negativePromptSegments: [],
		promptRelay: { global_prompt: 'relay prompt', timeline: { duration: 5, fps: 24, segments: [] } },
		videoDirector: { mode: 'i2v' },
		musicDirector: { mode: 'song' },
		directorRuns: { shot: { status: 'done' } },
		...overrides
	} as any;
}

function prepare(extra: Record<string, unknown>) {
	return prepareRequest({
		tab: tab(),
		tabId: 'tab-1',
		numPrompts: 1,
		segmentJoin: 'space',
		promptRelayActive: false,
		promptlessActive: false,
		videoDirector: null,
		musicDirector: null,
		random: () => 0,
		now: () => 1,
		...extra
	} as any);
}

beforeEach(() => {
	assemble.mockReset();
});

describe('prepareRequest with a Video Director', () => {
	it('returns the assembly failure reason as a director refusal', () => {
		assemble.mockReturnValue({ kind: 'video', ok: false, reason: 'Shot 2 has no prompt yet.' });

		const result = prepare({ videoDirector: { caps: videoCaps, checked: new Set(['s2']), predecessorOutputs: null } });

		expect(result).toEqual({ ok: false, code: 'director', reason: 'Shot 2 has no prompt yet.' });
		expect(assemble).toHaveBeenCalledWith(
			expect.objectContaining({
				videoDirectorActive: true,
				videoDirectorCaps: videoCaps,
				videoDirectorValue: { mode: 'i2v' },
				directorRuns: { shot: { status: 'done' } },
				directorChecked: new Set(['s2']),
				musicDirectorActive: false
			})
		);
	});

	it('uses the assembled prompts, form data and shot ids', () => {
		const directorValue = { mode: 'director' };
		assemble.mockReturnValue({
			kind: 'video',
			ok: true,
			formData: { seed: 3, video_director: { mode: 'i2v' } },
			prompts: [{ positive: 'shot one', negative: '' }],
			primaryShotIds: ['s1'],
			remainingShotIds: ['s2', 's3'],
			directorValue
		});

		const result = prepare({
			videoDirector: { caps: videoCaps, checked: new Set(), predecessorOutputs: null },
			musicDirector: { caps: musicCaps },
			promptRelayActive: true
		});

		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.director).toEqual({ primaryShotIds: ['s1'], remainingShotIds: ['s2', 's3'], value: directorValue });
		expect(result.request.prompts).toEqual([{ positive: 'shot one', negative: '' }]);
		expect(result.request.form_data).toEqual({ seed: 3, video_director: { mode: 'i2v' } });
		expect(assemble).toHaveBeenCalledTimes(1);
	});
});

describe('prepareRequest with a Music Director', () => {
	it('takes prompts and form data from the music assembly', () => {
		assemble.mockReturnValue({ kind: 'music', formData: { seed: 3, music_director: { mode: 'song' } }, prompts: [{ positive: 'a ballad', negative: '' }] });

		const result = prepare({ musicDirector: { caps: musicCaps } });

		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.request.prompts).toEqual([{ positive: 'a ballad', negative: '' }]);
		expect(result.request.form_data).toEqual({ seed: 3, music_director: { mode: 'song' } });
		expect(result.director).toEqual({ primaryShotIds: [], remainingShotIds: [], value: null });
		expect(assemble).toHaveBeenCalledWith(
			expect.objectContaining({ videoDirectorActive: false, musicDirectorActive: true, musicDirectorCaps: musicCaps, musicDirectorValue: { mode: 'song' } })
		);
	});

	it('is checked before Prompt Relay', () => {
		assemble.mockReturnValue({ kind: 'music', formData: { seed: 3 }, prompts: [{ positive: 'a ballad', negative: '' }] });

		const result = prepare({ musicDirector: { caps: musicCaps }, promptRelayActive: true });

		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.request.prompts).toEqual([{ positive: 'a ballad', negative: '' }]);
		expect(result.request.form_data).not.toHaveProperty('timeline');
	});
});

describe('prepareRequestFromSession picks the Director from preset vars', () => {
	it('activates the Video Director for a mode the preset lists', () => {
		assemble.mockReturnValue({ kind: 'video', ok: false, reason: 'stop here' });

		const result = prepareRequestFromSession({
			presetId: 'preset-1',
			mode: 'video',
			session: { prompt: 'x', videoDirector: { mode: 't2v' } },
			presetVars: { video_director: { preset_modes: ['video'], modes: { t2v: {}, i2v: {} } } },
			random: () => 0
		});

		expect(result).toEqual({ ok: false, code: 'director', reason: 'stop here' });
		const input = assemble.mock.calls[0][0];
		expect(input.videoDirectorActive).toBe(true);
		expect(input.videoDirectorCaps.presetModes).toEqual(['video']);
		expect(input.videoDirectorValue).toEqual({ mode: 't2v' });
	});

	it('activates the Music Director for a mode the preset lists', () => {
		assemble.mockReturnValue({ kind: 'music', formData: {}, prompts: [{ positive: 'song', negative: '' }] });

		const result = prepareRequestFromSession({
			presetId: 'preset-1',
			mode: 'song',
			session: { musicDirector: { mode: 'song' } },
			presetVars: { music_director: { preset_modes: ['song'], modes: { song: {} } } },
			random: () => 0
		});

		expect(result.ok).toBe(true);
		const input = assemble.mock.calls[0][0];
		expect(input.musicDirectorActive).toBe(true);
		expect(input.musicDirectorCaps.presetModes).toEqual(['song']);
	});

	it('uses no Director for a mode the preset does not list', () => {
		const result = prepareRequestFromSession({
			presetId: 'preset-1',
			mode: 'txt2img',
			session: { prompt: 'a harbour' },
			presetVars: {
				video_director: { preset_modes: ['video'], modes: { t2v: {} } },
				music_director: { preset_modes: ['song'], modes: { song: {} } }
			},
			random: () => 0
		});

		expect(result.ok).toBe(true);
		expect(assemble).not.toHaveBeenCalled();
	});
});
