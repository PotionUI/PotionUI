import { canvasFromUrl } from '$lib/paint/loadPicked';
import { canvasToPngFile } from '$lib/paint/flatten';
import type { Command } from '$lib/paint/history';
import { createPatch, patchBytes, writePatch } from '$lib/paint/patch';
import type { Layer } from '$lib/paint/types';
import type { EditStackState, EditStep } from '$lib/tool-tabs/frame/editStack';
import type { PaintParams } from './steps';

const LOCAL_PREFIX = 'local-layer:';
const registry = new Map<string, () => HTMLCanvasElement>();
let stores = 0;
let layerIds = 1_000_000;

export function localLayerCanvas(url: string): HTMLCanvasElement | null {
	if (!url.startsWith(LOCAL_PREFIX)) return null;
	const key = url.slice(LOCAL_PREFIX.length, url.lastIndexOf('@'));
	return registry.get(key)?.() ?? null;
}

export function blankCanvas(width: number, height: number): HTMLCanvasElement {
	const canvas = document.createElement('canvas');
	canvas.width = Math.max(1, Math.round(width));
	canvas.height = Math.max(1, Math.round(height));
	return canvas;
}

function context2d(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
	const context = canvas.getContext('2d', { willReadFrequently: true });
	if (!context) throw new Error('This browser could not open a drawing surface.');
	return context;
}

export function newLayer(name: string, canvas: HTMLCanvasElement, x = 0, y = 0): Layer {
	return { id: layerIds++, name, canvas, x, y, visible: true, opacity: 1 };
}

export function isNormalized(layer: Layer, width: number, height: number): boolean {
	return layer.x === 0 && layer.y === 0 && layer.canvas.width === width && layer.canvas.height === height;
}

export function docCanvas(layer: Layer, width: number, height: number): HTMLCanvasElement {
	if (isNormalized(layer, width, height)) return layer.canvas;
	const out = blankCanvas(width, height);
	context2d(out).drawImage(layer.canvas, layer.x, layer.y);
	return out;
}

export function normalizeLayer(layer: Layer, width: number, height: number): void {
	if (isNormalized(layer, width, height)) return;
	layer.canvas = docCanvas(layer, width, height);
	layer.x = 0;
	layer.y = 0;
	layer.source = undefined;
}

export function copyCanvas(source: HTMLCanvasElement, width = source.width, height = source.height): HTMLCanvasElement {
	const out = blankCanvas(width, height);
	const context = context2d(out);
	context.imageSmoothingQuality = 'high';
	context.drawImage(source, 0, 0, out.width, out.height);
	return out;
}

export function paintInto(target: HTMLCanvasElement, source: HTMLCanvasElement | null): void {
	const context = context2d(target);
	context.clearRect(0, 0, target.width, target.height);
	if (source) context.drawImage(source, 0, 0, target.width, target.height);
}

export function diffCommand(layer: Layer, before: HTMLCanvasElement, width: number, height: number, label = 'Paint'): Command | null {
	normalizeLayer(layer, width, height);
	const rect = { x: 0, y: 0, width, height };
	const patch = createPatch(rect, context2d(before).getImageData(0, 0, width, height).data, context2d(layer.canvas).getImageData(0, 0, width, height).data);
	if (!patch) return null;
	const apply = (side: 'before' | 'after') => {
		normalizeLayer(layer, width, height);
		const context = context2d(layer.canvas);
		const region = context.getImageData(patch.x, patch.y, patch.width, patch.height);
		writePatch({ width: region.width, height: region.height, data: region.data }, { ...patch, x: 0, y: 0 }, side);
		context.putImageData(region, patch.x, patch.y);
	};
	return { label, bytes: patchBytes(patch), pixels: true, undo: () => apply('before'), redo: () => apply('after') };
}

export interface LayerUpload {
	id: string;
	url: string;
}

export interface PaintLayerOptions {
	upload(file: File): Promise<LayerUpload>;
	uploaded(id: string, previous: string, next: LayerUpload): void;
	changed(): void;
	failed(error: unknown): void;
	delay?: number;
}

interface Entry {
	layer: Layer;
	url: string;
	width: number;
	height: number;
	version: number;
	saved: number;
	timer: ReturnType<typeof setTimeout> | null;
	busy: Promise<boolean> | null;
	thumb: { version: number; url: string | null } | null;
}

export function urlOf(step: Pick<EditStep, 'params'>): string {
	const url = (step.params as Partial<PaintParams> | null)?.url;
	return typeof url === 'string' ? url : '';
}

export class PaintLayerStore {
	private entries = new Map<string, Entry>();
	private loads = new Map<string, Promise<Layer>>();
	private readonly key = `t${++stores}`;

	constructor(private readonly options: PaintLayerOptions) {}

	has(id: string): boolean {
		return this.entries.has(id);
	}

	layer(id: string): Layer | null {
		return this.entries.get(id)?.layer ?? null;
	}

	canvasOf(id: string): HTMLCanvasElement | null {
		const entry = this.entries.get(id);
		return entry ? docCanvas(entry.layer, entry.width, entry.height) : null;
	}

	version(id: string): number {
		return this.entries.get(id)?.version ?? 0;
	}

	get unsaved(): number {
		let count = 0;
		for (const entry of this.entries.values()) if (entry.version !== entry.saved || entry.busy) count += 1;
		return count;
	}

	async load(step: EditStep, width: number, height: number): Promise<Layer> {
		const entry = this.entries.get(step.id);
		if (entry) {
			this.fit(entry, width, height);
			return entry.layer;
		}
		const running = this.loads.get(step.id);
		if (running) return running;
		const url = urlOf(step);
		const job = (async () => {
			const stored = url ? await canvasFromUrl(url) : null;
			const existing = this.entries.get(step.id);
			if (existing) return existing.layer;
			const canvas = blankCanvas(width, height);
			if (stored) paintInto(canvas, stored);
			const layer = newLayer(step.label, canvas);
			this.register(step.id, { layer, url, width, height, version: 0, saved: 0, timer: null, busy: null, thumb: null });
			return layer;
		})();
		this.loads.set(step.id, job);
		try {
			return await job;
		} finally {
			this.loads.delete(step.id);
		}
	}

	adopt(id: string, layer: Layer, width: number, height: number, url = ''): void {
		this.register(id, { layer, url, width, height, version: 0, saved: 0, timer: null, busy: null, thumb: null });
		this.touch(id);
	}

	copy(from: string, to: string, url: string): void {
		const source = this.entries.get(from);
		if (!source) return;
		const layer = newLayer(source.layer.name, copyCanvas(docCanvas(source.layer, source.width, source.height)));
		this.register(to, { layer, url, width: source.width, height: source.height, version: 0, saved: 0, timer: null, busy: null, thumb: null });
		if (source.version !== source.saved) this.touch(to);
	}

	replace(id: string, canvas: HTMLCanvasElement, url: string): void {
		const entry = this.entries.get(id);
		if (!entry) return;
		this.stop(entry);
		normalizeLayer(entry.layer, entry.width, entry.height);
		paintInto(entry.layer.canvas, canvas);
		entry.url = url;
		entry.version += 1;
		entry.saved = entry.version;
		this.options.changed();
	}

	touch(id: string): void {
		const entry = this.entries.get(id);
		if (!entry) return;
		entry.version += 1;
		this.stop(entry);
		entry.timer = setTimeout(() => {
			entry.timer = null;
			void this.upload(id);
		}, this.options.delay ?? 800);
		this.options.changed();
	}

	sync(steps: readonly EditStep[]): void {
		for (const step of steps) {
			if (step.tool !== 'paint') continue;
			const entry = this.entries.get(step.id);
			const url = urlOf(step);
			if (!entry || entry.url === url) continue;
			this.stop(entry);
			entry.url = url;
			entry.version += 1;
			entry.saved = entry.version;
			const version = entry.version;
			normalizeLayer(entry.layer, entry.width, entry.height);
			if (!url) {
				paintInto(entry.layer.canvas, null);
				this.options.changed();
				continue;
			}
			void canvasFromUrl(url)
				.then((stored) => {
					if (entry.version !== version) return;
					paintInto(entry.layer.canvas, stored);
					entry.version += 1;
					entry.saved = entry.version;
					this.options.changed();
				})
				.catch((error) => this.options.failed(error));
		}
	}

	display<S extends EditStep>(steps: readonly S[]): S[] {
		return steps.map((step) => {
			const entry = step.tool === 'paint' ? this.entries.get(step.id) : undefined;
			if (!entry) return step;
			return { ...step, params: { ...(step.params as object), url: `${LOCAL_PREFIX}${this.key}/${step.id}@${entry.version}` } };
		});
	}

	thumb(step: EditStep): string | null {
		const entry = this.entries.get(step.id);
		if (!entry || (entry.version === entry.saved && entry.url === urlOf(step) && entry.url)) return null;
		if (entry.thumb?.version === entry.version) return entry.thumb.url;
		const source = docCanvas(entry.layer, entry.width, entry.height);
		const scale = 96 / Math.max(source.width, source.height, 1);
		const small = copyCanvas(source, Math.max(1, Math.round(source.width * scale)), Math.max(1, Math.round(source.height * scale)));
		let url: string | null = null;
		try {
			url = small.toDataURL('image/png');
		} catch {
			url = null;
		}
		entry.thumb = { version: entry.version, url };
		return url;
	}

	async flush(ids?: readonly string[]): Promise<boolean> {
		const list = ids ?? [...this.entries.keys()];
		const results = await Promise.all(list.map((id) => this.upload(id)));
		return results.every(Boolean);
	}

	dispose(): void {
		for (const [id, entry] of this.entries) {
			this.stop(entry);
			registry.delete(`${this.key}/${id}`);
		}
		this.entries.clear();
	}

	private register(id: string, entry: Entry): void {
		this.entries.set(id, entry);
		registry.set(`${this.key}/${id}`, () => docCanvas(entry.layer, entry.width, entry.height));
	}

	private fit(entry: Entry, width: number, height: number): void {
		if (entry.width === width && entry.height === height) return;
		const canvas = copyCanvas(docCanvas(entry.layer, entry.width, entry.height), width, height);
		entry.layer.canvas = canvas;
		entry.layer.x = 0;
		entry.layer.y = 0;
		entry.layer.source = undefined;
		entry.width = width;
		entry.height = height;
	}

	private stop(entry: Entry): void {
		if (entry.timer) clearTimeout(entry.timer);
		entry.timer = null;
	}

	private async upload(id: string): Promise<boolean> {
		const entry = this.entries.get(id);
		if (!entry) return true;
		this.stop(entry);
		while (entry.busy) await entry.busy;
		if (entry.version === entry.saved) return true;
		const version = entry.version;
		const previous = entry.url;
		const run = (async () => {
			try {
				const file = await canvasToPngFile(docCanvas(entry.layer, entry.width, entry.height), 'paint-layer.png');
				const stored = await this.options.upload(file);
				if (entry.version !== version || entry.url !== previous || this.entries.get(id) !== entry) return true;
				entry.url = stored.url;
				entry.saved = version;
				this.options.uploaded(id, previous, stored);
				return true;
			} catch (error) {
				this.options.failed(error);
				return false;
			} finally {
				entry.busy = null;
				this.options.changed();
			}
		})();
		entry.busy = run;
		if (!(await run)) return false;
		return entry.version === entry.saved || this.entries.get(id) !== entry ? true : this.upload(id);
	}
}

function relinkParams(params: unknown, previous: string, next: LayerUpload): unknown {
	const current = params as Partial<PaintParams> | null;
	if (!current || (current.url ?? '') !== previous) return params;
	return { ...current, layer: next.id, url: next.url };
}

function relinkStep<S extends { id?: string; tool: string; params: unknown }>(step: S, id: string, previous: string, next: LayerUpload): S {
	if (step.tool !== 'paint' || (step.id !== undefined && step.id !== id)) return step;
	const params = relinkParams(step.params, previous, next);
	return params === step.params ? step : { ...step, params };
}

function relinkRevision<S extends { id?: string; tool: string; params: unknown }, R extends { id: string; before: S; after: S; swap?: { before: { steps: S[]; cursor: number }; after: { steps: S[]; cursor: number } } }>(revision: R, id: string, previous: string, next: LayerUpload): R {
	const relinkSnapshot = (snapshot: { steps: S[]; cursor: number }) => ({ ...snapshot, steps: snapshot.steps.map((step) => relinkStep(step, id, previous, next)) });
	const swap = revision.swap ? { before: relinkSnapshot(revision.swap.before), after: relinkSnapshot(revision.swap.after) } : undefined;
	if (revision.id !== id) return swap ? { ...revision, swap } : revision;
	const out = { ...revision, before: relinkStep(revision.before, id, previous, next), after: relinkStep(revision.after, id, previous, next) };
	return swap ? { ...out, swap } : out;
}

export function relinkLayer<P>(state: EditStackState<P>, id: string, previous: string, next: LayerUpload): EditStackState<P> {
	const steps = state.steps.map((step) => relinkStep(step, id, previous, next));
	const revisions = state.revisions?.map((revision) => relinkRevision(revision, id, previous, next));
	const redoRevisions = state.redoRevisions?.map((revision) => relinkRevision(revision, id, previous, next));
	const pending = state.pending?.target === id ? relinkStep(state.pending, id, previous, next) : state.pending;
	const out: EditStackState<P> = { ...state, steps, pending };
	if (revisions) out.revisions = revisions;
	if (redoRevisions) out.redoRevisions = redoRevisions;
	if (state.cleared) {
		const held = state.cleared;
		out.cleared = { ...held, steps: held.steps.map((step) => relinkStep(step, id, previous, next)), after: held.after === state.steps ? steps : held.after.map((step) => relinkStep(step, id, previous, next)) };
	}
	if (state.live) out.live = state.live.id === id && state.live.before ? { ...state.live, before: relinkStep(state.live.before, id, previous, next) } : state.live;
	return out;
}

export function nextPaintName(taken: Iterable<string>): string {
	const names = new Set(taken);
	let n = 1;
	while (names.has(`Paint ${n}`)) n += 1;
	return `Paint ${n}`;
}

export function nextPictureName(taken: Iterable<string>): string {
	const names = new Set(taken);
	let n = 1;
	while (names.has(`Picture ${n}`)) n += 1;
	return `Picture ${n}`;
}
