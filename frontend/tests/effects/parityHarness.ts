import { effectTools } from '$lib/effects/catalog';
import { buildFragment } from '$lib/effects/glsl';
import { planChain, type EffectPass } from '$lib/effects/plan';
import { ShaderChain } from '$lib/effects/ShaderChain';
import type { ToolDTO } from '$lib/media-tools/types';

export interface ParityCase {
	params: Record<string, unknown>;
	analysis: Record<string, number> | null;
	frames: number[];
	tables?: Record<string, string>;
}

export interface ParityJob {
	tool: ToolDTO;
	shader: string;
	width: number;
	height: number;
	fps: number;
	duration: number;
	input: string;
	cases: ParityCase[];
}

export interface ParityOutput {
	case: number;
	frame: number;
	pixels: string;
}

function decode(text: string): Uint8Array {
	const raw = atob(text);
	const bytes = new Uint8Array(raw.length);
	for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
	return bytes;
}

function encode(bytes: Uint8Array): string {
	let raw = '';
	for (let i = 0; i < bytes.length; i += 0x8000) raw += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
	return btoa(raw);
}

function compileLog(gl: WebGL2RenderingContext, source: string): string {
	const shader = gl.createShader(gl.FRAGMENT_SHADER);
	if (!shader) return 'no shader object';
	gl.shaderSource(shader, source);
	gl.compileShader(shader);
	const log = gl.getShaderInfoLog(shader) ?? '';
	gl.deleteShader(shader);
	return log;
}

export function rendererName(): string {
	const gl = document.createElement('canvas').getContext('webgl2');
	if (!gl) return 'none';
	const info = gl.getExtension('WEBGL_debug_renderer_info');
	return String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
}

export function renderParityJob(job: ParityJob): ParityOutput[] {
	const effect = effectTools([job.tool])[0];
	if (!effect?.block.shader) throw new Error(`${job.tool.id} has no shader`);
	const failed: string[] = [];
	const chain = ShaderChain.create(document.createElement('canvas'), { failed: (tool) => failed.push(tool) });
	if (!chain) throw new Error('WebGL2 is not available');
	try {
		chain.setSource(effect.block.shader.url, job.shader);
		const size = { width: job.width, height: job.height };
		if (!chain.uploadPixels(size, decode(job.input))) throw new Error('The fixture frame did not upload');
		const outputs: ParityOutput[] = [];
		job.cases.forEach((entry, index) => {
			for (const [ref, table] of Object.entries(entry.tables ?? {})) {
				const bytes = decode(table);
				if (!chain.setLut(ref, new Uint16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2))) throw new Error(`${effect.id} LUT ${ref} did not upload`);
			}
			const plan = planChain({
				steps: [{ tool: effect.id, params: entry.params }],
				source: { ...size, duration: job.duration, fps: job.fps },
				effects: new Map([[effect.id, effect]]),
				cap: Math.max(size.width, size.height),
				analysis: () => entry.analysis
			});
			const pass = plan.passes.find((item): item is EffectPass => item.kind === 'effect');
			if (!pass || pass.identity || plan.skipped.length) throw new Error(`${effect.id} case ${index} did not plan a shader pass`);
			for (const frame of entry.frames) {
				const pixels = chain.readPixels(plan, { mediaTime: frame / job.fps, sourceDuration: job.duration, fps: job.fps });
				if (failed.length) throw new Error(`${effect.id} shader failed: ${compileLog(chain.gl, buildFragment(job.shader, pass.uniforms))}`);
				if (!pixels) throw new Error(`${effect.id} case ${index} frame ${frame} could not be read`);
				outputs.push({ case: index, frame, pixels: encode(pixels) });
			}
		});
		return outputs;
	} finally {
		chain.dispose();
	}
}

Object.assign(globalThis, { renderParityJob, rendererName });
