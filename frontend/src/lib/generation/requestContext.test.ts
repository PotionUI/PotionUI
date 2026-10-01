import { describe, it, expect } from 'vitest';
import { createRequestContextCache, resolveRequestContext } from './requestContext';

describe('resolveRequestContext', () => {
	it('defaults to one prompt, space joins and no special mode', () => {
		expect(resolveRequestContext(undefined, 'txt2img')).toEqual({
			numPrompts: 1,
			segmentJoin: 'space',
			promptRelayActive: false,
			promptlessActive: false,
			videoDirectorCaps: null,
			videoDirectorActive: false,
			musicDirectorCaps: null,
			musicDirectorActive: false
		});
	});

	it('reads every switch from the preset vars for the given mode', () => {
		const vars = {
			num_prompts: 2,
			prompt: { segment_join: 'paragraph' },
			prompt_relay_modes: ['relay'],
			promptless_modes: ['upscale'],
			video_director: { preset_modes: ['video'], modes: { t2v: {} } }
		};

		expect(resolveRequestContext(vars, 'relay')).toMatchObject({ numPrompts: 2, segmentJoin: 'paragraph', promptRelayActive: true, videoDirectorActive: false });
		expect(resolveRequestContext(vars, 'upscale').promptlessActive).toBe(true);
		expect(resolveRequestContext(vars, 'video').videoDirectorActive).toBe(true);
		expect(resolveRequestContext(vars, null).promptRelayActive).toBe(false);
	});
});

describe('createRequestContextCache', () => {
	it('returns the same context while the preset, mode and relevant vars are unchanged', () => {
		const contextFor = createRequestContextCache();
		const vars = { video_director: { preset_modes: ['video'], modes: { t2v: {} } } };

		const first = contextFor('p', vars, 'video');
		const again = contextFor('p', { ...vars, unrelated: 1 }, 'video');
		const otherMode = contextFor('p', vars, 'refs');

		expect(again).toBe(first);
		expect(again.videoDirectorCaps).toBe(first.videoDirectorCaps);
		expect(otherMode).not.toBe(first);
		expect(contextFor('p', { ...vars, num_prompts: 3 }, 'refs').numPrompts).toBe(3);
	});
});
