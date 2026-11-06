import type { ToolDTO } from '$lib/media-tools/types';

export const GRAIN_SHADER = `uint lowbias32(uint x) {
    x ^= x >> 16u;
    x *= 0x7feb352du;
    x ^= x >> 15u;
    x *= 0x846ca68bu;
    x ^= x >> 16u;
    return x;
}

float grainNoise(uvec2 cell, uint seed) {
    uint h = lowbias32((cell.x * 73856093u) ^ (cell.y * 19349663u) ^ ((seed + 1u) * 83492791u));
    uint s = lowbias32(h ^ 0x9e3779b9u);
    return float(h) / 4294967296.0 + float(s) / 4294967296.0 - 1.0;
}

uint frameSeed(int channel) {
    uint base = lowbias32(uint(u_seed) ^ lowbias32(uint(u_frame) + 0x632be5abu));
    return lowbias32(base ^ (uint(channel + 1) * 0x27d4eb2du));
}

vec4 effect(vec4 color, vec2 uv) {
    uvec2 cell = uvec2(floor(uv * u_resolution)) / uint(u_cell);
    float luma = dot(color.rgb, vec3(0.299, 0.587, 0.114));
    float t = 2.0 * luma - 1.0;
    float mid = 1.0 - 0.7 * t * t;
    vec3 noise = u_color
        ? vec3(grainNoise(cell, frameSeed(0)), grainNoise(cell, frameSeed(1)), grainNoise(cell, frameSeed(2)))
        : vec3(grainNoise(cell, frameSeed(0)));
    vec3 delta = noise * u_strength * mid / 255.0;
    return vec4(clamp(color.rgb + delta, 0.0, 1.0), color.a);
}
`;

export const FIX_SHADER = `vec4 effect(vec4 color, vec2 uv) {
    vec3 mixed = clamp(color.rgb * vec3(u_r, u_g, u_b), 0.0, 1.0);
    vec3 levelled = (mixed - u_lo) / max(u_hi - u_lo, 1e-4);
    return vec4(clamp(levelled, 0.0, 1.0), color.a);
}
`;

export const SCANLINES_SHADER = `vec4 effect(vec4 color, vec2 uv) {
    float y = floor(uv.y * u_resolution.y + u_roll * u_time);
    float dark = mod(y, float(u_gap)) < 1.0 ? u_k : 0.0;
    return vec4(color.rgb * (1.0 - dark), color.a);
}
`;

export const SHADERS: Record<string, string> = {
	'/api/media-tools/effects/film_grain/shader': GRAIN_SHADER,
	'/api/media-tools/effects/fix_colors/shader': FIX_SHADER,
	'/api/media-tools/effects/retro-tape.scanlines/shader': SCANLINES_SHADER
};

function effectTool(overrides: Partial<ToolDTO> & { id: string }): ToolDTO {
	return {
		label: overrides.id,
		icon: 'editor-effects',
		accepts: ['video'],
		produces: 'same',
		cardinality: 'one_to_one',
		params: [],
		requires: {},
		selection: null,
		scopes: ['library', 'history', 'field'],
		editor: null,
		preview: false,
		component: null,
		runnable: true,
		group: 'effects',
		source: 'core',
		last_params: null,
		...overrides
	};
}

export const GRAIN_TOOL = effectTool({
	id: 'film_grain',
	label: 'Film grain',
	description: 'Moving film grain, new on every frame',
	accepts: ['video', 'image'],
	params: [
		{ name: 'amount', type: 'float', label: 'Amount', default: 30, min: 0, max: 100, step: 1, unit: '%' },
		{ name: 'size', type: 'float', label: 'Size', default: 1.5, min: 1, max: 3, step: 0.1 },
		{ name: 'color', type: 'checkbox', label: 'Colour grain', default: false },
		{ name: 'seed', type: 'integer', label: 'Seed', default: 0, min: 0, max: 2147483647 }
	],
	effect: {
		group: 'texture',
		time: 'frame',
		shader: { url: '/api/media-tools/effects/film_grain/shader', etag: 'grain-1' },
		uniforms: [
			{ name: 'u_amount', type: 'float', from: 'param', key: 'amount' },
			{ name: 'u_size', type: 'float', from: 'param', key: 'size' },
			{ name: 'u_color', type: 'bool', from: 'param', key: 'color' },
			{ name: 'u_seed', type: 'int', from: 'param', key: 'seed' },
			{ name: 'u_strength', type: 'float', from: 'derive', key: 'strength' },
			{ name: 'u_cell', type: 'float', from: 'derive', key: 'cell' }
		],
		textures: [],
		derive: [
			{ name: 'strength', expr: 'amount / 100 * 0.14 * 255' },
			{ name: 'cell', expr: 'max(1, floor(size * min(media.width, media.height) / 1000 + 0.5))' }
		],
		gain: null,
		analysis: false
	}
});

export const FIX_TOOL = effectTool({
	id: 'fix_colors',
	label: 'Fix colours',
	icon: 'wand',
	description: 'Neutral whites and full contrast, measured over the clip',
	params: [
		{ name: 'white_balance', type: 'checkbox', label: 'White balance', default: true },
		{ name: 'contrast', type: 'checkbox', label: 'Normalise contrast', default: true },
		{ name: 'strength', type: 'float', label: 'Strength', default: 100, min: 0, max: 100, step: 5, unit: '%' }
	],
	effect: {
		group: 'colour',
		time: 'none',
		shader: { url: '/api/media-tools/effects/fix_colors/shader', etag: 'fix-1' },
		uniforms: [],
		textures: [],
		derive: {
			k: 'strength / 100',
			r: '1 + k * (gain_r - 1)',
			g: '1 + k * (gain_g - 1)',
			b: '1 + k * (gain_b - 1)',
			lo: 'black * k',
			hi: '1 - (1 - white) * k'
		},
		gain: null,
		analysis: true,
		analysis_keys: ['white_balance', 'contrast']
	}
});

export const SCANLINES_TOOL = effectTool({
	id: 'retro-tape.scanlines',
	label: 'Scanlines',
	description: 'CRT lines that roll slowly down the picture',
	source: 'retro-tape',
	accepts: ['video', 'image'],
	params: [
		{ name: 'strength', type: 'float', label: 'Strength', default: 35, min: 0, max: 100, step: 1, unit: '%' },
		{ name: 'gap', type: 'integer', label: 'Line gap', default: 3, min: 2, max: 12, unit: 'px' },
		{ name: 'roll', type: 'float', label: 'Roll speed', default: 8, min: 0, max: 60, step: 1, unit: 'px/s' }
	],
	effect: {
		group: 'texture',
		time: 'seconds',
		shader: { url: '/api/media-tools/effects/retro-tape.scanlines/shader', etag: 'scan-1' },
		uniforms: [],
		textures: [],
		derive: { k: 'strength / 100' },
		gain: null,
		analysis: false
	}
});

export const TAPE_TOOL = effectTool({
	id: 'retro-tape.wobble',
	label: 'Tape wobble',
	description: 'An old tape that wobbles',
	source: 'retro-tape',
	params: [{ name: 'amount', type: 'float', label: 'Amount', default: 40, min: 0, max: 100, step: 1, unit: '%' }],
	effect: { group: 'warp', time: 'seconds', shader: null, uniforms: [], textures: [], derive: {}, gain: null, analysis: false }
});

export const TRIM_TOOL = effectTool({ id: 'trim', label: 'Trim', icon: 'scissors', group: 'edit', effect: null });

export const AUDIO_EFFECT = effectTool({ id: 'echo', label: 'Echo', accepts: ['audio'], effect: { group: 'texture', shader: null } });

export const EFFECT_CATALOG: ToolDTO[] = [TRIM_TOOL, SCANLINES_TOOL, GRAIN_TOOL, TAPE_TOOL, FIX_TOOL, AUDIO_EFFECT];

const SLOTS = [0, 1, 2, 3].flatMap((slot) => ['kind', 'a', 'b', 'c'].map((key) => `s${slot}_${key}`));

function lookEnv(values: Record<string, number> = {}): Record<string, number> {
	return { has: 0, intensity: 0, live: 1, spatial: 0, ...Object.fromEntries(SLOTS.map((name) => [name, 0])), ...values };
}

export const LOOK_TOOL = effectTool({
	id: 'look',
	label: 'Look',
	description: 'Grade with one of your looks',
	accepts: ['video', 'image'],
	params: [
		{
			name: 'look',
			type: 'look',
			label: 'Look',
			default: '',
			options: [
				{ label: 'None', value: '', env: lookEnv(), revision: '' },
				{ label: 'Warm', value: 'warm', group: 'Film', env: lookEnv({ has: 1, intensity: 100 }), revision: 'w1' },
				{ label: 'Reel', value: 'mine:reel', group: 'Mine', env: lookEnv({ has: 1, intensity: 80, spatial: 1, s0_kind: 1, s0_a: 40, s0_b: 50, s0_c: 60 }), revision: 'r7' },
				{ label: 'Smear', value: 'smear', group: 'Art', env: lookEnv({ has: 1, intensity: 100, spatial: 1, live: 0 }), revision: 's1' }
			] as unknown as ToolDTO['params'][number]['options']
		},
		{ name: 'strength', type: 'float', label: 'Strength', default: 1, min: 0, max: 1, step: 0.05 }
	],
	effect: {
		group: 'colour',
		time: 'none',
		shader: { url: '/api/media-tools/effects/look/shader', etag: '"look"' },
		uniforms: [],
		textures: [{ uniform: 'table', from: 'look', kind: 'lut3d' }],
		derive: [
			{ name: 'amount', expr: 'look.intensity / 100 * strength if look.has else 0' },
			{ name: 'plain', expr: 'look.spatial == 0' },
			...SLOTS.map((name) => ({ name, expr: `look.${name}` }))
		],
		gain: null,
		live: 'look.live == 1',
		analysis: false,
		analysis_keys: []
	}
});
