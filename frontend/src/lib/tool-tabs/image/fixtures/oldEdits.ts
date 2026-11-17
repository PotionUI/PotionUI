const step = (id: string, tool: string, label: string, params: unknown, extra: Record<string, unknown> = {}) => ({
	id,
	tool,
	icon: 'editor-effects',
	label,
	summary: '',
	params,
	hidden: false,
	...extra
});

export const OLD_ADJUST_EDIT = {
	v: 1,
	steps: [
		step('s1', 'effects', 'Tone', { kind: 'adjust', op: 'tone', name: 'Tone', values: { brightness: 20, contrast: 10, saturation: -15, hue: 0 } }),
		step('s2', 'effects', 'Exposure', { kind: 'adjust', op: 'exposure', name: 'Exposure', values: { stops: 0.5 } }),
		step('s3', 'effects', 'White balance', { kind: 'adjust', op: 'white_balance', name: 'White balance', values: { temperature: 12, tint: 0 } }),
		step('s4', 'effects', 'Vignette', { kind: 'adjust', op: 'vignette', name: 'Vignette', values: { amount: 30, midpoint: 50, feather: 60 } }),
		step('s5', 'effects', 'Grain', { kind: 'adjust', op: 'grain', name: 'Grain', values: { amount: 20, size: 1, seed: 7 } })
	],
	cursor: 5,
	pending: null,
	seq: 5
};

export const OLD_LOOK_EDIT = {
	v: 1,
	steps: [step('s1', 'effects', 'Dusk', { kind: 'look', filter: 'dusk', name: 'Dusk', intensity: 65 })],
	cursor: 1,
	pending: null,
	seq: 1
};

export const OLD_EFFECT_EDIT = {
	v: 1,
	steps: [step('s1', 'effects', 'Film grain', { kind: 'effect', tool: 'film_grain', name: 'Film grain', values: { amount: 42, size: 1.8, color: false, seed: 3 } })],
	cursor: 1,
	pending: null,
	seq: 1
};

export const OLD_MASK_STATE = {
	v: 1,
	stack: { v: 1, steps: [], cursor: 0, pending: null, seq: 0 },
	activeTool: 'mask',
	viewport: { fit: true, zoom: 1, panX: 0, panY: 0 },
	mask: { path: 'uploads/mask-1.png', url: '/api/media/uploads/mask-1.png', geometry: '[]' },
	saved: null
};
