import { get } from 'svelte/store';
import { canvasFromBlob, canvasFromUrl } from '$lib/components/imageEditor/loadPicked';
import { canvasToPngFile } from '$lib/components/imageEditor/flatten';
import { paintFilters } from '$lib/components/imageEditor/registries';
import { filterCatalog, loadCube, loadFilterCatalog } from '$lib/filters/catalog';
import { sharedExtensions } from '$lib/filters/extensions';
import { renderRecipe, type FilterRecipe } from '$lib/filters/render';
import { planOutputSize } from '$lib/media/editors/imageEditPlan';
import { blockedUploadMessage } from '$lib/plans/uploadLimit';
import { previewFrame, uploadMediaToolArtifact } from '$lib/services/api/mediaTools';
import type { MediaToolInputRef } from '$lib/media-tools/types';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import { outputSize, payloadGaps, resizeOutput, runSteps, type PixelSize } from './payload';
import { adjustRecipeStep, type CropParams, type EffectParams, type PaintParams, type ResizeParams } from './steps';

export const FULL_SIDE = 8192;
export const PREVIEW_SIDE = 1600;

const CLIENT_TOOLS = new Set(['crop', 'resize', 'paint', 'effects', 'slice']);

const drawn = (step: Pick<EditStep, 'tool' | 'hidden'>) => !step.hidden && step.tool !== 'slice';

export function isClientStep(step: Pick<EditStep, 'tool'>): boolean {
	return CLIENT_TOOLS.has(step.tool);
}

export interface RenderSource {
	url: string;
	size: PixelSize;
	ref: MediaToolInputRef | null;
}

export interface Rendered {
	canvas: HTMLCanvasElement;
	size: PixelSize;
}

export interface RenderOptions {
	maxSide?: number;
	signal?: AbortSignal;
}

function makeCanvas(width: number, height: number): HTMLCanvasElement {
	const canvas = document.createElement('canvas');
	canvas.width = Math.max(1, Math.round(width));
	canvas.height = Math.max(1, Math.round(height));
	return canvas;
}

function context2d(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
	const context = canvas.getContext('2d', { willReadFrequently: true });
	if (!context) throw new Error('This browser could not open a drawing surface.');
	context.imageSmoothingQuality = 'high';
	return context;
}

export function fitSide(size: PixelSize, maxSide: number): PixelSize {
	const longest = Math.max(size.width, size.height);
	if (longest <= maxSide) return { width: Math.max(1, Math.round(size.width)), height: Math.max(1, Math.round(size.height)) };
	const scale = maxSide / longest;
	return { width: Math.max(1, Math.round(size.width * scale)), height: Math.max(1, Math.round(size.height * scale)) };
}

function scaled(canvas: HTMLCanvasElement, target: PixelSize): HTMLCanvasElement {
	if (canvas.width === target.width && canvas.height === target.height) return canvas;
	const out = makeCanvas(target.width, target.height);
	context2d(out).drawImage(canvas, 0, 0, out.width, out.height);
	return out;
}

export function orient(canvas: HTMLCanvasElement, params: CropParams): HTMLCanvasElement {
	const { rotation, flipHorizontal, flipVertical } = params.plan;
	if (!rotation && !flipHorizontal && !flipVertical) return canvas;
	const turned = rotation === 90 || rotation === 270;
	const out = makeCanvas(turned ? canvas.height : canvas.width, turned ? canvas.width : canvas.height);
	const context = context2d(out);
	context.translate(out.width / 2, out.height / 2);
	context.scale(flipHorizontal ? -1 : 1, flipVertical ? -1 : 1);
	context.rotate((rotation * Math.PI) / 180);
	context.drawImage(canvas, -canvas.width / 2, -canvas.height / 2);
	return out;
}

function cropCanvas(canvas: HTMLCanvasElement, params: CropParams, size: PixelSize, maxSide: number): Rendered {
	const oriented = orient(canvas, params);
	const rect = params.plan.rect;
	const logical = planOutputSize(params.plan, size);
	const target = fitSide(logical, maxSide);
	const out = makeCanvas(target.width, target.height);
	context2d(out).drawImage(
		oriented,
		rect.x * oriented.width,
		rect.y * oriented.height,
		rect.width * oriented.width,
		rect.height * oriented.height,
		0,
		0,
		out.width,
		out.height
	);
	return { canvas: out, size: logical };
}

async function paintCanvas(canvas: HTMLCanvasElement, params: PaintParams): Promise<HTMLCanvasElement> {
	const layer = await canvasFromUrl(params.url);
	const out = makeCanvas(canvas.width, canvas.height);
	const context = context2d(out);
	context.drawImage(canvas, 0, 0);
	context.drawImage(layer, 0, 0, out.width, out.height);
	return out;
}

export interface ResolvedEffect {
	recipe: FilterRecipe;
	intensity: number;
}

export async function resolveEffect(params: EffectParams): Promise<ResolvedEffect> {
	if (params.kind === 'adjust') return { recipe: { steps: [adjustRecipeStep(params)], cube: null }, intensity: 100 };
	await loadFilterCatalog();
	const item = get(filterCatalog).items.find((candidate) => candidate.id === params.filter);
	if (!item) throw new Error(`The look ${params.name} is not available any more`);
	const cube = item.has_lut ? await loadCube(item) : null;
	return { recipe: { steps: item.steps, cube }, intensity: params.intensity };
}

export async function effectCanvas(canvas: HTMLCanvasElement, params: EffectParams): Promise<HTMLCanvasElement> {
	const effect = await resolveEffect(params);
	const out = makeCanvas(canvas.width, canvas.height);
	const context = context2d(out);
	context.drawImage(canvas, 0, 0);
	const pixels = context.getImageData(0, 0, out.width, out.height);
	const extensions = sharedExtensions(get(filterCatalog).ops, paintFilters.list());
	const result = renderRecipe(
		{ width: pixels.width, height: pixels.height, data: pixels.data },
		effect.recipe,
		effect.intensity,
		{ extensions }
	);
	context.putImageData(new ImageData(result.data as Uint8ClampedArray<ArrayBuffer>, result.width, result.height), 0, 0);
	return out;
}

export async function applyClientStep(rendered: Rendered, step: EditStep, maxSide: number): Promise<Rendered> {
	switch (step.tool) {
		case 'crop':
			return cropCanvas(rendered.canvas, step.params as CropParams, rendered.size, maxSide);
		case 'resize': {
			const size = resizeOutput(step.params as ResizeParams, rendered.size);
			return { canvas: scaled(rendered.canvas, fitSide(size, maxSide)), size };
		}
		case 'paint':
			return { canvas: await paintCanvas(rendered.canvas, step.params as PaintParams), size: rendered.size };
		case 'effects':
			return { canvas: await effectCanvas(rendered.canvas, step.params as EffectParams), size: rendered.size };
		default:
			return rendered;
	}
}

export function abortError(): DOMException {
	return new DOMException('Aborted', 'AbortError');
}

export function isAbort(error: unknown): boolean {
	return (error as { name?: string } | null)?.name === 'AbortError';
}

function waitFor<T>(promise: Promise<T>, signal: AbortSignal | undefined, onAbort: () => void): Promise<T> {
	if (!signal) return promise;
	if (signal.aborted) {
		onAbort();
		return Promise.reject(abortError());
	}
	return new Promise<T>((resolve, reject) => {
		const abort = () => {
			onAbort();
			reject(abortError());
		};
		signal.addEventListener('abort', abort, { once: true });
		promise.then(
			(value) => {
				signal.removeEventListener('abort', abort);
				resolve(value);
			},
			(error) => {
				signal.removeEventListener('abort', abort);
				reject(error);
			}
		);
	});
}

export function stepsKey(steps: readonly EditStep[]): string {
	return JSON.stringify(steps.filter(drawn).map((step) => [step.tool, step.params]));
}

export function sourceKey(source: RenderSource): string {
	return JSON.stringify([source.url, source.ref, source.size.width, source.size.height]);
}

export function bakeIsCapped(size: PixelSize | null): boolean {
	return !!size && Math.max(size.width, size.height) > FULL_SIDE;
}

interface PrefixEntry {
	promise: Promise<Rendered>;
	controller: AbortController;
	waiters: number;
	settled: boolean;
}

const MAX_PREFIXES = 8;

export class ImageRenderer {
	private bakes = new Map<string, Promise<string>>();
	private sources = new Map<string, Promise<HTMLCanvasElement>>();
	private prefixes = new Map<string, PrefixEntry>();

	constructor(private source: RenderSource) {}

	setSource(source: RenderSource): void {
		if (sourceKey(source) === sourceKey(this.source)) {
			this.source = source;
			return;
		}
		if (source.url !== this.source.url) this.sources.clear();
		this.bakes.clear();
		this.dropPrefixes();
		this.source = source;
	}

	reset(): void {
		this.sources.clear();
		this.bakes.clear();
		this.dropPrefixes();
	}

	get ref(): MediaToolInputRef | null {
		return this.source.ref;
	}

	private dropPrefixes(): void {
		for (const entry of this.prefixes.values()) if (!entry.settled) entry.controller.abort();
		this.prefixes.clear();
	}

	private original(): Promise<HTMLCanvasElement> {
		const url = this.source.url;
		let pending = this.sources.get(url);
		if (!pending) {
			pending = canvasFromUrl(url);
			pending.catch(() => this.sources.delete(url));
			this.sources.set(url, pending);
		}
		return pending;
	}

	async render(steps: readonly EditStep[], options: RenderOptions = {}): Promise<Rendered> {
		const maxSide = options.maxSide ?? PREVIEW_SIDE;
		const visible = steps.filter(drawn);
		let serverAt = -1;
		visible.forEach((step, i) => {
			if (!isClientStep(step)) serverAt = i;
		});
		let rendered: Rendered;
		if (serverAt >= 0) {
			rendered = await this.prefix(visible.slice(0, serverAt + 1), maxSide, options.signal);
		} else {
			const original = await waitFor(this.original(), options.signal, () => undefined);
			rendered = { canvas: scaled(original, fitSide(this.source.size, maxSide)), size: this.source.size };
		}
		for (const step of visible.slice(serverAt + 1)) {
			if (options.signal?.aborted) throw abortError();
			rendered = await applyClientStep(rendered, step, maxSide);
		}
		return rendered;
	}

	private prefix(steps: readonly EditStep[], maxSide: number, signal?: AbortSignal): Promise<Rendered> {
		const key = `${sourceKey(this.source)}|${maxSide}|${stepsKey(steps)}`;
		let entry = this.prefixes.get(key);
		if (!entry) {
			const controller = new AbortController();
			const created: PrefixEntry = { promise: this.server(steps, maxSide, controller.signal), controller, waiters: 0, settled: false };
			created.promise.then(
				() => (created.settled = true),
				() => {
					created.settled = true;
					if (this.prefixes.get(key) === created) this.prefixes.delete(key);
				}
			);
			this.prefixes.set(key, created);
			while (this.prefixes.size > MAX_PREFIXES) this.prefixes.delete(this.prefixes.keys().next().value!);
			entry = created;
		}
		const held = entry;
		held.waiters += 1;
		return waitFor(held.promise, signal, () => {
			held.waiters -= 1;
			if (held.waiters > 0 || held.settled) return;
			held.controller.abort();
			if (this.prefixes.get(key) === held) this.prefixes.delete(key);
		});
	}

	async payload(steps: readonly EditStep[]): Promise<ReturnType<typeof runSteps>> {
		const sizes: Record<string, PixelSize> = {};
		const baked: Record<string, string> = {};
		for (const gap of payloadGaps(steps, { source: this.source.size })) {
			const index = steps.findIndex((step) => step.id === gap.step);
			if (gap.kind === 'bake') baked[gap.step] = await this.bake(steps.slice(0, index + 1));
		}
		let gaps = payloadGaps(steps, { source: this.source.size, sizes, baked });
		while (gaps.some((gap) => gap.kind === 'size')) {
			const gap = gaps.find((entry) => entry.kind === 'size')!;
			const index = steps.findIndex((step) => step.id === gap.step);
			sizes[gap.step] = (await this.render(steps.slice(0, index), { maxSide: FULL_SIDE })).size;
			gaps = payloadGaps(steps, { source: this.source.size, sizes, baked });
		}
		return runSteps(steps, { source: this.source.size, sizes, baked });
	}

	bake(steps: readonly EditStep[]): Promise<string> {
		const key = `${sourceKey(this.source)}|${stepsKey(steps)}`;
		let pending = this.bakes.get(key);
		if (!pending) {
			pending = this.renderBake(steps);
			pending.catch(() => {
				if (this.bakes.get(key) === pending) this.bakes.delete(key);
			});
			this.bakes.set(key, pending);
		}
		return pending;
	}

	private async renderBake(steps: readonly EditStep[]): Promise<string> {
		const last = steps[steps.length - 1];
		const base = await this.render(steps.slice(0, -1), { maxSide: FULL_SIDE });
		const out = await effectCanvas(base.canvas, last.params as EffectParams);
		const file = await canvasToPngFile(out, 'look.png');
		const blocked = blockedUploadMessage([file]);
		if (blocked) throw new Error(blocked);
		const artifact = await uploadMediaToolArtifact(file);
		return artifact.id;
	}

	private async server(steps: readonly EditStep[], maxSide: number, signal?: AbortSignal): Promise<Rendered> {
		const ref = this.source.ref;
		if (!ref) throw new Error('This picture has to be in your Library or History first');
		const known = outputSize(steps, this.source.size);
		const payload = await this.payload(steps);
		const { blob } = await previewFrame({ input: ref, steps: payload, max_side: known ? maxSide : FULL_SIDE, format: 'png' }, signal);
		const canvas = await canvasFromBlob(blob);
		const size = known ?? { width: canvas.width, height: canvas.height };
		return { canvas: known ? canvas : scaled(canvas, fitSide(size, maxSide)), size };
	}
}
