export type OpKind = 'colour' | 'spatial';
export type ParamType = 'int' | 'float' | 'curve';
export type CurvePoint = [number, number];

export interface OpParam {
	id: string;
	label: string;
	type: ParamType;
	min: number | null;
	max: number | null;
	default: number | CurvePoint[];
	unit: string | null;
}

export interface OpSpec {
	id: string;
	label: string;
	kind: OpKind;
	source: 'core' | 'plugin';
	plugin_id: string | null;
	params: OpParam[];
}

export interface FilterStep {
	op: string;
	enabled?: boolean;
	[param: string]: unknown;
}

export type StepValues = Record<string, number | CurvePoint[]>;

export interface Cube {
	size: number;
	data: Float32Array;
	domainMin: [number, number, number];
	domainMax: [number, number, number];
	title: string;
}

export interface Lut {
	size: number;
	data: Float32Array;
}

export interface FilterRecipe {
	steps: FilterStep[];
	cube?: Cube | null;
}

export interface ImageDataLike {
	data: Uint8ClampedArray;
	width: number;
	height: number;
}

export type RgbTriple = [number, number, number];

export type PluginColourMap = (rgb: RgbTriple, values: StepValues) => RgbTriple;

export type PluginSpatialApply = (
	data: Uint8ClampedArray,
	width: number,
	height: number,
	values: StepValues,
	amount: number
) => void;

export interface FilterExtensions {
	ops?: Record<string, OpSpec>;
	colour?: Record<string, PluginColourMap>;
	spatial?: Record<string, PluginSpatialApply>;
}
