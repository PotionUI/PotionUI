import type { CurvePoint, FilterExtensions, FilterStep, OpParam, OpSpec, StepValues } from './types';

const identityCurve = (): CurvePoint[] => [
	[0, 0],
	[1, 1]
];

function int(
	id: string,
	label: string,
	min: number,
	max: number,
	def = 0,
	unit: string | null = null
): OpParam {
	return { id, label, type: 'int', min, max, default: def, unit };
}

function float(id: string, label: string, min: number, max: number, def = 0): OpParam {
	return { id, label, type: 'float', min, max, default: def, unit: null };
}

function curve(id: string, label: string): OpParam {
	return { id, label, type: 'curve', min: null, max: null, default: identityCurve(), unit: null };
}

function core(
	id: string,
	label: string,
	kind: 'colour' | 'spatial',
	params: OpParam[] = []
): OpSpec {
	return { id, label, kind, source: 'core', plugin_id: null, params };
}

export const OPS: OpSpec[] = [
	core('tone', 'Tone', 'colour', [
		int('brightness', 'Brightness', -100, 100),
		int('contrast', 'Contrast', -100, 100),
		int('saturation', 'Saturation', -100, 100),
		int('hue', 'Hue', -180, 180, 0, '°')
	]),
	core('exposure', 'Exposure', 'colour', [float('stops', 'Stops', -2, 2)]),
	core('white_balance', 'White balance', 'colour', [
		int('temperature', 'Temperature', -100, 100),
		int('tint', 'Tint', -100, 100)
	]),
	core('curves', 'Curves', 'colour', [
		curve('master', 'Master'),
		curve('r', 'Red'),
		curve('g', 'Green'),
		curve('b', 'Blue')
	]),
	core('vibrance', 'Vibrance', 'colour', [int('amount', 'Amount', -100, 100)]),
	core('split_tone', 'Split tone', 'colour', [
		int('shadow_hue', 'Shadow hue', 0, 360, 0, '°'),
		int('shadow_sat', 'Shadow saturation', 0, 100),
		int('highlight_hue', 'Highlight hue', 0, 360, 0, '°'),
		int('highlight_sat', 'Highlight saturation', 0, 100),
		int('balance', 'Balance', -100, 100)
	]),
	core('fade', 'Fade', 'colour', [
		int('black_lift', 'Black lift', 0, 40, 0, '%'),
		int('white_cap', 'White cap', 0, 30, 0, '%')
	]),
	core('grayscale', 'Grayscale', 'colour'),
	core('invert', 'Invert', 'colour'),
	core('vignette', 'Vignette', 'spatial', [
		int('amount', 'Amount', -100, 100),
		int('midpoint', 'Midpoint', 0, 100, 50),
		int('feather', 'Feather', 0, 100, 60)
	]),
	core('grain', 'Grain', 'spatial', [
		int('amount', 'Amount', 0, 100),
		float('size', 'Size', 0.5, 4, 1),
		int('seed', 'Seed', 0, 65535)
	])
];

export const OPS_BY_ID: Record<string, OpSpec> = Object.fromEntries(OPS.map((op) => [op.id, op]));

export function getOp(id: string, extensions?: FilterExtensions): OpSpec | undefined {
	return OPS_BY_ID[id] ?? extensions?.ops?.[id];
}

export function resolveValues(spec: OpSpec, step: FilterStep): StepValues {
	const values: StepValues = {};
	for (const param of spec.params) {
		const given = step[param.id];
		if (param.type === 'curve') {
			const points = Array.isArray(given) ? (given as CurvePoint[]) : (param.default as CurvePoint[]);
			values[param.id] = points.map((point) => [point[0], point[1]] as CurvePoint);
		} else {
			values[param.id] = typeof given === 'number' ? given : (param.default as number);
		}
	}
	return values;
}

export function splitSteps(
	steps: FilterStep[],
	extensions?: FilterExtensions
): { colour: FilterStep[]; spatial: FilterStep[] } {
	const colour: FilterStep[] = [];
	const spatial: FilterStep[] = [];
	for (const step of steps) {
		if (step.enabled === false) continue;
		const spec = getOp(step.op, extensions);
		if (!spec) continue;
		(spec.kind === 'spatial' ? spatial : colour).push(step);
	}
	return { colour, spatial };
}
