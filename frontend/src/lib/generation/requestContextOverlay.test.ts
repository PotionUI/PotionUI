import { describe, it, expect } from 'vitest';
import { createRequestContextCache, resolveRequestContext } from './requestContext';
import type { DirectorModelOverlay } from '$lib/utils/cloudDirector';

const vars = {
	video_director: {
		preset_modes: ['txt2video', 'img2video'],
		segment_routing: true,
		modes: { t2v: {}, i2v: {}, flf: {}, director: { keyframes: 'anywhere', max_segments: 8 } },
		limits: { default_duration: 5, default_fps: 24, max_duration: 15 },
		preset_mode_overrides: {
			txt2video: { modes: { director: { max_segments: 2 } } }
		}
	}
};

const lite: DirectorModelOverlay = {
	label: 'Fake Lite',
	raw: { modes: { flf: null, director: { keyframes: 'first_only', max_segments: 3 } }, limits: { max_duration: 8 } }
};

describe('request context with a model overlay', () => {
	it('is the preset block when no model is chosen', () => {
		const context = resolveRequestContext(vars, 'img2video');
		expect(context.videoDirectorActive).toBe(true);
		expect(context.videoDirectorCaps?.enabledModes).toEqual(['t2v', 'i2v', 'flf', 'director']);
		expect(context.videoDirectorCaps?.modelLabel).toBeNull();
	});

	it('narrows the editor to the chosen model', () => {
		const caps = resolveRequestContext(vars, 'img2video', lite).videoDirectorCaps;
		expect(caps?.enabledModes).toEqual(['t2v', 'i2v', 'director']);
		expect(caps?.modes.director?.maxSegments).toBe(3);
		expect(caps?.maxDuration).toBe(8);
		expect(caps?.modelLabel).toBe('Fake Lite');
	});

	it('applies the model after the preset mode override', () => {
		const quiet: DirectorModelOverlay = { label: 'Quiet', raw: { modes: { flf: null } } };
		expect(resolveRequestContext(vars, 'txt2video', quiet).videoDirectorCaps?.modes.director?.maxSegments).toBe(2);
		expect(resolveRequestContext(vars, 'img2video', quiet).videoDirectorCaps?.modes.director?.maxSegments).toBe(8);
		expect(resolveRequestContext(vars, 'txt2video', lite).videoDirectorCaps?.modes.director?.maxSegments).toBe(3);
	});

	it('hides the editor for a mode the model cannot do at all', () => {
		const stills: DirectorModelOverlay = { label: 'Stills', raw: { modes: { t2v: null, i2v: null, flf: null, director: null } } };
		const context = resolveRequestContext(vars, 'img2video', stills);
		expect(context.videoDirectorCaps).toBeNull();
		expect(context.videoDirectorActive).toBe(false);
	});

	it('recomputes when the model changes and reuses the result when it does not', () => {
		const contextFor = createRequestContextCache();
		const a = contextFor('p', vars, 'img2video', null);
		expect(contextFor('p', vars, 'img2video', null)).toBe(a);
		const b = contextFor('p', vars, 'img2video', lite);
		expect(b).not.toBe(a);
		expect(contextFor('p', vars, 'img2video', { ...lite, raw: { ...lite.raw } })).toBe(b);
		expect(contextFor('p', vars, 'img2video', null).videoDirectorCaps?.modelLabel).toBeNull();
	});
});
