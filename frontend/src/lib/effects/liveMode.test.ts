import { describe, expect, it } from 'vitest';
import { BudgetMeter, PREVIEW_CAPS, driftMs } from './budget';
import { liveState, type LiveInput } from './liveMode';
import { buildFragment, checkEffectSource } from './glsl';

const plan = (overrides: Partial<NonNullable<LiveInput['plan']>> = {}) => ({ effects: 1, skipped: [], measuring: [], ...overrides });
const base: LiveInput = { webgl2: true, contextLosses: 0, plan: plan(), steppedDown: false, playing: true };

describe('the fallback matrix', () => {
	it('previews live with no Preview 3 s when every effect has a shader', () => {
		expect(liveState(base)).toEqual({ mode: 'live', canvas: true, serverPaused: false, showClip: false, chips: [] });
		expect(liveState({ ...base, playing: false }).canvas).toBe(true);
	});

	it('falls back to the server frame and shows Preview 3 s without WebGL2', () => {
		const state = liveState({ ...base, webgl2: false, playing: false });
		expect(state).toEqual({ mode: 'server', canvas: false, serverPaused: true, showClip: true, chips: [] });
	});

	it('falls back after the context is lost twice, not once', () => {
		expect(liveState({ ...base, contextLosses: 1 }).mode).toBe('live');
		expect(liveState({ ...base, contextLosses: 2 }).mode).toBe('server');
	});

	it('plays the rest live, names the skipped effect and uses the server frame when paused', () => {
		const skipped = [{ index: 1, tool: 'retro-tape.wobble', label: 'Tape wobble', reason: 'no-shader' as const }];
		const playing = liveState({ ...base, plan: plan({ effects: 2, skipped }) });
		expect(playing).toEqual({ mode: 'live', canvas: true, serverPaused: false, showClip: true, chips: ['Tape wobble shows when paused'] });
		const paused = liveState({ ...base, playing: false, plan: plan({ effects: 2, skipped }) });
		expect(paused.serverPaused).toBe(true);
		expect(paused.canvas).toBe(false);
	});

	it('needs no canvas and no Preview 3 s without effects', () => {
		expect(liveState({ ...base, plan: plan({ effects: 0 }) })).toEqual({ mode: 'off', canvas: false, serverPaused: false, showClip: false, chips: [] });
	});

	it('says when it is measuring and when it stepped down', () => {
		expect(liveState({ ...base, plan: plan({ measuring: ['colour'] }), steppedDown: true }).chips).toEqual(['Measuring colours…', 'Preview at lower resolution']);
		expect(liveState({ ...base, plan: plan({ measuring: ['motion'] }) }).chips).toEqual(['Measuring the clip…']);
	});
});

describe('the budget', () => {
	it('steps down to 1280 then 960 after 30 frames over budget in a row', () => {
		const meter = new BudgetMeter();
		for (let i = 0; i < 29; i += 1) expect(meter.frame(9)).toBe(false);
		meter.frame(2);
		for (let i = 0; i < 29; i += 1) meter.frame(9);
		expect(meter.cap).toBe(1920);
		expect(meter.frame(9)).toBe(true);
		expect(meter.cap).toBe(1280);
		for (let i = 0; i < 30; i += 1) meter.frame(12);
		expect(meter.cap).toBe(960);
		for (let i = 0; i < 60; i += 1) expect(meter.frame(12)).toBe(false);
		expect(meter.cap).toBe(PREVIEW_CAPS[2]);
	});

	it('marks a pass slow only after a step-down', () => {
		const meter = new BudgetMeter();
		for (let i = 0; i < 40; i += 1) expect(meter.pass('film_grain', 20)).toBe(false);
		for (let i = 0; i < 30; i += 1) meter.frame(20);
		let flagged = false;
		for (let i = 0; i < 30; i += 1) flagged = meter.pass('film_grain', 20) || flagged;
		expect(flagged).toBe(true);
		expect([...meter.slow]).toEqual(['film_grain']);
	});

	it('counts frame drift only when frames come late', () => {
		expect(driftMs(34, 33.3)).toBe(0);
		expect(driftMs(70, 33.3)).toBeGreaterThan(8);
	});
});

describe('the shader prelude', () => {
	it('declares every uniform once and wraps the effect function', () => {
		const text = buildFragment('vec4 effect(vec4 color, vec2 uv) { return color * u_k; }', [
			{ name: 'k', type: 'float' },
			{ name: 'time', type: 'float' }
		]);
		expect(text.startsWith('#version 300 es')).toBe(true);
		expect(text.match(/uniform float u_k;/g)).toHaveLength(1);
		expect(text.match(/uniform float u_time;/g)).toHaveLength(1);
		expect(text).toContain('o_color = effect(texture(u_input, v_uv), v_uv);');
	});

	it('refuses shaders that take over what the host owns', () => {
		expect(() => checkEffectSource('#version 300 es\nvec4 effect(vec4 c, vec2 uv) { return c; }')).toThrow();
		expect(() => checkEffectSource('uniform float u_x;\nvec4 effect(vec4 c, vec2 uv) { return c; }')).toThrow();
		expect(() => checkEffectSource('vec4 other(vec4 c) { return c; }')).toThrow();
		expect(() => checkEffectSource('x'.repeat(70 * 1024) + 'vec4 effect(vec4 c, vec2 uv) { return c; }')).toThrow();
		expect(checkEffectSource('vec4 effect(in vec4 c, vec2 uv) { return c; }')).toContain('effect');
	});
});
