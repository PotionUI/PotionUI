import { applyLut, compileLut } from './lut';
import { OPS } from './ops';
import { applySpatial } from './spatial';
import type { FilterExtensions, FilterRecipe, ImageDataLike, Lut } from './types';

export interface OpCatalogueParam {
	id: string;
	label: string;
	type: 'int' | 'float' | 'points';
	min: number;
	max: number;
	default: number;
}

export interface OpCatalogueEntry {
	id: string;
	label: string;
	kind: 'colour' | 'spatial';
	params: OpCatalogueParam[];
}

export const OP_CATALOGUE: OpCatalogueEntry[] = OPS.map((op) => ({
	id: op.id,
	label: op.label,
	kind: op.kind,
	params: op.params.map((param) =>
		param.type === 'curve'
			? { id: param.id, label: param.label, type: 'points', min: 0, max: 1, default: 0 }
			: {
					id: param.id,
					label: param.label,
					type: param.type,
					min: param.min as number,
					max: param.max as number,
					default: param.default as number
				}
	)
}));

export type { FilterStep as FilterStepSpec } from './types';

export interface ApplyFilterOptions {
	lut?: Lut;
	extensions?: FilterExtensions;
}

export function applyFilter(
	imageData: ImageDataLike,
	filter: FilterRecipe,
	intensity: number = 100,
	options: ApplyFilterOptions = {}
): ImageDataLike {
	if (intensity <= 0) return imageData;
	const lut =
		options.lut ?? compileLut(filter.steps, filter.cube, intensity, options.extensions);
	applyLut(imageData.data, lut);
	applySpatial(
		imageData.data,
		imageData.width,
		imageData.height,
		filter.steps,
		intensity,
		options.extensions
	);
	return imageData;
}

export { applyLut, compileLut, identityLut, tetra, BASE_SIZE, FilterOpUnavailable } from './lut';
export { applySpatial, grainCell, grainNoise, lowbias32 } from './spatial';
export { CubeError, MAX_CUBE_SIZE, MIN_CUBE_SIZE, parseCube } from './cube';
export { COLOUR_OPS, pchip } from './colour';
export { OPS, OPS_BY_ID, getOp, resolveValues, splitSteps } from './ops';
export type {
	Cube,
	CurvePoint,
	FilterExtensions,
	FilterRecipe,
	FilterStep,
	ImageDataLike,
	Lut,
	OpKind,
	OpParam,
	OpSpec,
	ParamType,
	PluginColourMap,
	PluginSpatialApply,
	RgbTriple,
	StepValues
} from './types';
