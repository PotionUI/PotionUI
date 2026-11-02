import type { MediaToolStep } from '$lib/media-tools/types';
import { outputDuration, type TimeOp } from '$lib/tool-tabs/video/timeMap';
import {
	effectParams,
	paramEnv,
	paramUniform,
	scalarUniform,
	uniformList,
	type EffectTool,
	type EffectUniform,
	type UniformValue
} from './catalog';
import { deriveBlock, type DeriveValue } from './derive';

export type Affine = readonly [number, number, number, number, number, number];

export const IDENTITY: Affine = [1, 0, 0, 0, 1, 0];

export interface Size {
	width: number;
	height: number;
}

export interface GeometryPass {
	kind: 'geometry';
	map: Affine;
	size: Size;
}

export interface EffectPass {
	kind: 'effect';
	index: number;
	tool: string;
	label: string;
	shaderUrl: string;
	uniforms: EffectUniform[];
	values: Record<string, UniformValue>;
	timeOps: TimeOp[];
	duration: number;
	size: Size;
	gain: { expr: string; env: Record<string, DeriveValue> } | null;
	identity: boolean;
}

export type ChainPass = GeometryPass | EffectPass;

export interface SkippedEffect {
	index: number;
	tool: string;
	label: string;
	reason: 'no-shader' | 'failed' | 'error';
}

export interface AnalysisNeed {
	index: number;
	tool: string;
	key: string;
	steps: MediaToolStep[];
}

export interface ChainPlan {
	passes: ChainPass[];
	skipped: SkippedEffect[];
	analysis: AnalysisNeed[];
	measuring: string[];
	size: Size;
	effects: number;
}

export interface PlanInput {
	steps: readonly MediaToolStep[];
	source: Size & { duration: number; fps: number };
	effects: ReadonlyMap<string, EffectTool>;
	cap: number;
	unavailable?: ReadonlySet<string>;
	analysis?: (key: string) => Record<string, number> | null;
	stale?: (tool: string, index: number) => Record<string, number> | null;
}

export function compose(outer: Affine, inner: Affine): Affine {
	const [a, b, c, d, e, f] = outer;
	const [g, h, i, j, k, l] = inner;
	return [a * g + b * j, a * h + b * k, a * i + b * l + c, d * g + e * j, d * h + e * k, d * i + e * l + f];
}

export function rotation(degrees: number): Affine {
	const turns = (((Math.round(degrees / 90) % 4) + 4) % 4) as 0 | 1 | 2 | 3;
	if (turns === 1) return [0, 1, 0, -1, 0, 1];
	if (turns === 2) return [-1, 0, 1, 0, -1, 1];
	if (turns === 3) return [0, -1, 1, 1, 0, 0];
	return IDENTITY;
}

export function capSize(size: Size, cap: number): Size {
	const scale = Math.min(1, cap / Math.max(1, size.width, size.height));
	return { width: Math.max(1, Math.round(size.width * scale)), height: Math.max(1, Math.round(size.height * scale)) };
}

function number(value: unknown, fallback: number): number {
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : fallback;
}

function evenRound(value: number): number {
	return Math.max(2, Math.round(value / 2) * 2);
}

export function analysisKey(steps: readonly MediaToolStep[], index: number, keys: readonly string[] | null = null): string {
	const step = steps[index];
	const params = keys ? Object.fromEntries(keys.map((key) => [key, step.params?.[key] ?? null])) : step.params;
	return JSON.stringify([steps.slice(0, index), step.tool, params]);
}

export function planChain(input: PlanInput): ChainPlan {
	const { source, effects, cap } = input;
	const passes: ChainPass[] = [];
	const skipped: SkippedEffect[] = [];
	const analysis: AnalysisNeed[] = [];
	const measuring: string[] = [];
	const ops: TimeOp[] = [];
	let size: Size = { width: source.width, height: source.height };
	let geometry: GeometryPass = { kind: 'geometry', map: IDENTITY, size: capSize(size, cap) };
	let open = true;
	let count = 0;

	const geometryStep = (map: Affine, next: Size) => {
		if (!open) {
			geometry = { kind: 'geometry', map: IDENTITY, size: geometry.size };
			open = true;
		}
		size = next;
		geometry = { kind: 'geometry', map: compose(geometry.map, map), size: capSize(next, cap) };
	};

	const flush = () => {
		if (open) passes.push(geometry);
		open = false;
	};

	input.steps.forEach((step, index) => {
		if (step.hidden) return;
		const params = step.params ?? {};
		switch (step.tool) {
			case 'trim': {
				ops.push({ kind: 'trim', start: number(params.start, 0), end: number(params.end, 0) });
				return;
			}
			case 'speed':
				ops.push({ kind: 'speed', speed: number(params.speed, 1) });
				return;
			case 'rotate': {
				const map = rotation(number(params.degrees, 90));
				const quarter = Math.round(number(params.degrees, 90) / 90) % 2 !== 0;
				geometryStep(map, quarter ? { width: size.height, height: size.width } : size);
				return;
			}
			case 'flip':
				geometryStep(params.axis === 'vertical' ? [1, 0, 0, 0, -1, 1] : [-1, 0, 1, 0, 1, 0], size);
				return;
			case 'crop': {
				const x = number(params.x, 0);
				const y = number(params.y, 0);
				const width = Math.max(1, number(params.width, size.width));
				const height = Math.max(1, number(params.height, size.height));
				geometryStep([width / size.width, 0, x / size.width, 0, height / size.height, y / size.height], { width, height });
				return;
			}
			case 'resize': {
				const aspect = size.width / Math.max(1, size.height);
				const next = params.width !== undefined
					? { width: number(params.width, size.width), height: evenRound(number(params.width, size.width) / aspect) }
					: { width: evenRound(number(params.height, size.height) * aspect), height: number(params.height, size.height) };
				geometryStep(IDENTITY, next);
				return;
			}
		}
		const effect = effects.get(step.tool);
		if (!effect) return;
		count += 1;
		const timeOps = [...ops];
		const duration = outputDuration(source.duration, timeOps);
		if (!effect.block.shader || effect.block.textures.length > 0) {
			skipped.push({ index, tool: step.tool, label: effect.label, reason: 'no-shader' });
			return;
		}
		if (input.unavailable?.has(step.tool)) {
			skipped.push({ index, tool: step.tool, label: effect.label, reason: 'failed' });
			return;
		}
		flush();
		const passSize = capSize(size, cap);
		const values = effectParams(effect, params);
		let measured: Record<string, number> | null = {};
		if (effect.block.analysis) {
			const key = analysisKey(input.steps, index, effect.block.analysisKeys);
			const fresh = input.analysis?.(key) ?? null;
			if (!fresh) analysis.push({ index, tool: step.tool, key, steps: input.steps.slice(0, index + 1).map((entry) => ({ ...entry })) });
			measured = fresh ?? input.stale?.(step.tool, index) ?? null;
			if (!measured) measuring.push(effect.block.group);
		}
		const media = { width: passSize.width, height: passSize.height, duration, fps: source.fps };
		const env: Record<string, DeriveValue> = { ...paramEnv(effect.params, values), ...(measured ?? {}), media };
		let derived: Record<string, DeriveValue> = {};
		try {
			derived = measured ? deriveBlock(effect.block.derive, env) : {};
		} catch {
			skipped.push({ index, tool: step.tool, label: effect.label, reason: 'error' });
			return;
		}
		const uniformValues: Record<string, UniformValue> = {};
		for (const param of effect.params) {
			const value = paramUniform(param, values[param.name]);
			if (value !== null) uniformValues[param.name] = value;
		}
		for (const [name, value] of Object.entries({ ...(measured ?? {}), ...derived })) {
			const uniform = typeof value === 'object' ? null : scalarUniform(value);
			if (uniform !== null) uniformValues[name] = uniform;
		}
		passes.push({
			kind: 'effect',
			index,
			tool: step.tool,
			label: effect.label,
			shaderUrl: effect.block.shader.url,
			uniforms: uniformList(effect, Object.keys(measured ?? {})),
			values: uniformValues,
			timeOps,
			duration,
			size: passSize,
			gain: effect.block.gain && measured ? { expr: effect.block.gain, env: { ...env, ...derived } } : null,
			identity: !measured
		});
	});
	flush();
	const last = passes[passes.length - 1];
	return { passes, skipped, analysis, measuring, size: last ? last.size : capSize(size, cap), effects: count };
}

export function hasGeometry(plan: ChainPlan): boolean {
	return plan.passes.some((pass) => pass.kind === 'geometry' && pass.map.some((value, i) => value !== IDENTITY[i]));
}
