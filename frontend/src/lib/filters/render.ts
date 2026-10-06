import type { PixelBuffer } from '$lib/components/imageEditor/types';
import {
	applyFilter,
	compileLut,
	splitSteps,
	type FilterExtensions,
	type FilterRecipe,
	type FilterStep,
	type Lut
} from './engine';

export type { FilterRecipe, FilterStep };

export interface RenderOptions {
	lut?: Lut;
	extensions?: FilterExtensions;
}

export function toImageData(buffer: PixelBuffer): ImageData {
	if (typeof ImageData === 'undefined') {
		return { data: buffer.data, width: buffer.width, height: buffer.height, colorSpace: 'srgb' } as ImageData;
	}
	return new ImageData(buffer.data as Uint8ClampedArray<ArrayBuffer>, buffer.width, buffer.height);
}

export function renderRecipe(
	buffer: PixelBuffer,
	recipe: FilterRecipe,
	intensity: number,
	options: RenderOptions = {}
): PixelBuffer {
	const image = { width: buffer.width, height: buffer.height, data: new Uint8ClampedArray(buffer.data) };
	applyFilter(image, recipe, Math.min(100, Math.max(0, intensity)), options);
	return image;
}

const identities = new WeakMap<object, number>();
let nextIdentity = 1;

function identity(value: object | null | undefined): number {
	if (!value) return 0;
	let id = identities.get(value);
	if (id === undefined) {
		id = nextIdentity++;
		identities.set(value, id);
	}
	return id;
}

export class LutMemo {
	private key: string | null = null;
	private lut: Lut | null = null;

	get(recipe: FilterRecipe, intensity: number, extensions?: FilterExtensions): Lut {
		const { colour } = splitSteps(recipe.steps, extensions);
		const key = `${identity(recipe.cube)}:${identity(extensions)}:${intensity}:${JSON.stringify(colour)}`;
		if (this.lut && this.key === key) return this.lut;
		this.lut = compileLut(recipe.steps, recipe.cube, intensity, extensions);
		this.key = key;
		return this.lut;
	}

	clear(): void {
		this.key = null;
		this.lut = null;
	}
}
