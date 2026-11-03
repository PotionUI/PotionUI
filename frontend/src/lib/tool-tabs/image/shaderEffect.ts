import { canvasFromUrl } from '$lib/components/imageEditor/loadPicked';
import { effectTools, type EffectTool } from '$lib/effects/catalog';
import { planChain, type EffectPass, type PassTexture } from '$lib/effects/plan';
import { ShaderChain } from '$lib/effects/ShaderChain';
import type { ToolDTO } from '$lib/media-tools/types';
import { effectShader, textureTable } from '$lib/services/api/mediaTools';

export interface StillPixels {
	width: number;
	height: number;
	data: Uint8ClampedArray;
}

export interface ShaderSources {
	shader: (url: string) => Promise<string>;
	table: (texture: PassTexture) => Promise<Uint16Array>;
}

const STILL_FRAME = { mediaTime: 0, sourceDuration: 0, fps: 24 };

export function stillEffects(catalog: readonly ToolDTO[]): EffectTool[] {
	return effectTools(catalog, 'image').filter(
		(effect) => !!effect.block.shader && !effect.block.analysis && effect.block.textures.every((texture) => texture.kind === 'lut3d')
	);
}

export function imageEffects(catalog: readonly ToolDTO[]): EffectTool[] {
	return stillEffects(catalog).filter((effect) => !effect.params.some((param) => param.type === 'look'));
}

function remembered<T>(cache: Map<string, Promise<T>>, key: string, load: () => Promise<T>): Promise<T> {
	let pending = cache.get(key);
	if (!pending) {
		pending = load();
		pending.catch(() => {
			if (cache.get(key) === pending) cache.delete(key);
		});
		cache.set(key, pending);
	}
	return pending;
}

export class StillShader {
	private chain: ShaderChain | null = null;
	private failed = new Set<string>();
	private shaders = new Map<string, Promise<string>>();
	private tables = new Map<string, Promise<Uint16Array>>();

	constructor(private readonly sources: ShaderSources = { shader: (url) => effectShader(url), table: (texture) => textureTable(texture) }) {}

	private open(): ShaderChain {
		if (!this.chain || this.chain.isLost) {
			this.chain?.dispose();
			this.failed.clear();
			this.chain = ShaderChain.create(document.createElement('canvas'), { failed: (tool) => this.failed.add(tool) });
		}
		if (!this.chain) throw new Error('This browser cannot preview effects');
		return this.chain;
	}

	async render(pixels: StillPixels, effect: EffectTool, values: Record<string, unknown>): Promise<StillPixels> {
		const size = { width: pixels.width, height: pixels.height };
		const plan = planChain({
			steps: [{ tool: effect.id, params: values }],
			source: { ...size, duration: 0, fps: STILL_FRAME.fps },
			effects: new Map([[effect.id, effect]]),
			cap: Math.max(size.width, size.height)
		});
		const pass = plan.passes.find((item): item is EffectPass => item.kind === 'effect');
		if (!pass || plan.skipped.length) throw new Error(`${effect.label} cannot preview here`);
		const text = await remembered(this.shaders, pass.shaderUrl, () => this.sources.shader(pass.shaderUrl));
		const tables = await Promise.all(
			pass.textures.filter((texture) => texture.ref).map(async (texture) => [texture.ref, await remembered(this.tables, texture.ref, () => this.sources.table(texture))] as const)
		);
		const chain = this.open();
		chain.setSource(pass.shaderUrl, text);
		for (const [ref, table] of tables) if (!chain.hasLut(ref) && !chain.setLut(ref, table)) throw new Error(`${effect.label} could not load its table`);
		if (!chain.uploadPixels(size, new Uint8Array(pixels.data.buffer, pixels.data.byteOffset, pixels.data.byteLength))) {
			throw new Error('The picture could not reach the graphics card');
		}
		const out = chain.readPixels(plan, STILL_FRAME);
		if (this.failed.has(effect.id)) throw new Error(`${effect.label} could not run in this browser`);
		if (!out) throw new Error(`${effect.label} could not be drawn`);
		return { ...size, data: new Uint8ClampedArray(out.buffer, out.byteOffset, out.byteLength) };
	}

	dispose(): void {
		this.chain?.dispose();
		this.chain = null;
	}
}

export const stillShader = new StillShader();

export const STILL_PREVIEW_SIDE = 1280;

export async function stillPreview(url: string, effect: EffectTool, values: Record<string, unknown>, signal?: AbortSignal): Promise<Blob> {
	const source = await canvasFromUrl(url);
	const scale = Math.min(1, STILL_PREVIEW_SIDE / Math.max(1, source.width, source.height));
	const canvas = document.createElement('canvas');
	canvas.width = Math.max(1, Math.round(source.width * scale));
	canvas.height = Math.max(1, Math.round(source.height * scale));
	const context = canvas.getContext('2d', { willReadFrequently: true });
	if (!context) throw new Error('This browser could not open a drawing surface.');
	context.drawImage(source, 0, 0, canvas.width, canvas.height);
	if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
	const result = await stillShader.render(context.getImageData(0, 0, canvas.width, canvas.height), effect, values);
	if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
	context.putImageData(new ImageData(result.data as Uint8ClampedArray<ArrayBuffer>, result.width, result.height), 0, 0);
	return new Promise((resolve, reject) => canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('The preview could not be encoded'))), 'image/png'));
}
