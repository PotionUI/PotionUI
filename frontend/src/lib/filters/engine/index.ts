import { applyLut, compileLut } from './lut';
import { applySpatial } from './spatial';
import type { FilterExtensions, FilterRecipe, ImageDataLike, Lut } from './types';

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
