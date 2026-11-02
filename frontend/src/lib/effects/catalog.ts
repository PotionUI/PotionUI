import { isAnchorValue } from '$lib/components/form-fields/anchorValues';
import { normalizeOptions } from '$lib/media-tools/params';
import type { ToolDTO, ToolParam } from '$lib/media-tools/types';
import type { DeriveScalar, DeriveValue } from './derive';

export type EffectTime = 'none' | 'seconds' | 'frame';

export type UniformType = 'float' | 'int' | 'bool' | 'vec2' | 'vec3';

export interface EffectUniform {
	name: string;
	type: UniformType;
}

export interface EffectTexture {
	uniform: string;
	from: string;
	kind: 'lut3d' | 'image2d';
}

export interface EffectBlock {
	group: string;
	time: EffectTime;
	shader: { url: string; etag: string } | null;
	uniforms: EffectUniform[];
	textures: EffectTexture[];
	derive: Record<string, string>;
	gain: string | null;
	analysis: boolean;
	analysisKeys: string[] | null;
}

export interface EffectTool {
	id: string;
	label: string;
	icon: string;
	description: string;
	params: ToolParam[];
	block: EffectBlock;
	core: boolean;
}

export interface EffectGroup {
	id: string;
	label: string;
	effects: EffectTool[];
}

export type UniformValue = number | boolean | readonly number[];

const GROUP_LABELS: Record<string, string> = {
	colour: 'Colour',
	texture: 'Texture',
	motion: 'Motion',
	transitions: 'Transitions'
};
const GROUP_ORDER = ['colour', 'texture', 'motion', 'transitions'];
const MORE = 'more';
const UNIFORM_TYPES: readonly string[] = ['float', 'int', 'bool', 'vec2', 'vec3'];
const PARAM_UNIFORMS: Record<string, UniformType> = {
	float: 'float',
	integer: 'int',
	checkbox: 'bool',
	color: 'vec3',
	select: 'int',
	anchor: 'vec2'
};
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

function record(value: unknown): Record<string, unknown> | null {
	return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function readDerive(value: unknown): Record<string, string> {
	const out: Record<string, string> = {};
	if (Array.isArray(value)) {
		for (const entry of value) {
			const item = record(entry);
			if (item && typeof item.name === 'string' && typeof item.expr === 'string') out[item.name] = item.expr;
		}
		return out;
	}
	for (const [name, expr] of Object.entries(record(value) ?? {})) if (typeof expr === 'string') out[name] = expr;
	return out;
}

function readUniforms(value: unknown): EffectUniform[] {
	if (!Array.isArray(value)) return [];
	return value.flatMap((entry) => {
		const item = record(entry);
		if (!item || typeof item.name !== 'string' || !NAME.test(item.name.replace(/^u_/, ''))) return [];
		if (typeof item.type !== 'string' || !UNIFORM_TYPES.includes(item.type)) return [];
		return [{ name: item.name.replace(/^u_/, ''), type: item.type as UniformType }];
	});
}

export function readEffectBlock(tool: Pick<ToolDTO, 'group' | 'effect'>): EffectBlock | null {
	if (tool.group !== 'effects') return null;
	const raw = record(tool.effect) ?? {};
	const shader = record(raw.shader);
	const time = raw.time === 'seconds' || raw.time === 'frame' ? raw.time : 'none';
	return {
		group: typeof raw.group === 'string' && raw.group ? raw.group : MORE,
		time,
		shader: shader && typeof shader.url === 'string' ? { url: shader.url, etag: String(shader.etag ?? '') } : null,
		uniforms: readUniforms(raw.uniforms),
		textures: Array.isArray(raw.textures) ? (raw.textures as EffectTexture[]) : [],
		derive: readDerive(raw.derive),
		gain: typeof raw.gain === 'string' && raw.gain.trim() ? raw.gain : null,
		analysis: raw.analysis === true || !!record(raw.analysis),
		analysisKeys: Array.isArray(record(raw.analysis)?.keys) ? (record(raw.analysis)!.keys as unknown[]).map(String) : null
	};
}

function acceptsVideo(tool: ToolDTO): boolean {
	return (tool.accepts as string[]).includes('video') && (tool.scopes.length === 0 || tool.scopes.includes('editor'));
}

export function effectTools(catalog: readonly ToolDTO[]): EffectTool[] {
	return catalog.flatMap((tool) => {
		const block = readEffectBlock(tool);
		if (!block || !acceptsVideo(tool)) return [];
		return [
			{
				id: tool.id,
				label: tool.label,
				icon: tool.icon || 'editor-effects',
				description: tool.description ?? '',
				params: tool.params ?? [],
				block,
				core: !tool.id.includes('.')
			}
		];
	});
}

export function groupLabel(id: string): string {
	return GROUP_LABELS[id] ?? 'More';
}

export function effectGroups(effects: readonly EffectTool[]): EffectGroup[] {
	const groups = new Map<string, EffectTool[]>();
	for (const effect of effects) {
		const id = GROUP_LABELS[effect.block.group] ? effect.block.group : MORE;
		groups.set(id, [...(groups.get(id) ?? []), effect]);
	}
	const rank = (id: string) => (GROUP_ORDER.includes(id) ? GROUP_ORDER.indexOf(id) : GROUP_ORDER.length);
	return [...groups.entries()]
		.sort(([a], [b]) => rank(a) - rank(b))
		.map(([id, list]) => ({
			id,
			label: groupLabel(id),
			effects: [...list].sort((a, b) => a.label.localeCompare(b.label) || Number(b.core) - Number(a.core) || a.id.localeCompare(b.id))
		}));
}

export function effectDefaults(effect: Pick<EffectTool, 'params'>): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	for (const param of effect.params) if (param.default !== undefined) out[param.name] = param.default;
	return out;
}

export function effectParams(effect: Pick<EffectTool, 'params'>, params: Record<string, unknown>): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	const defaults = effectDefaults(effect);
	for (const param of effect.params) {
		const value = params[param.name] ?? defaults[param.name];
		if (value === undefined) continue;
		if (param.type === 'float') out[param.name] = Number.isFinite(Number(value)) ? Number(value) : defaults[param.name];
		else if (param.type === 'integer') out[param.name] = Number.isFinite(Number(value)) ? Math.trunc(Number(value)) : defaults[param.name];
		else if (param.type === 'checkbox') out[param.name] = value === true;
		else out[param.name] = value;
	}
	return out;
}

export function anchorPoint(value: unknown): [number, number] {
	const anchor = isAnchorValue(value) ? value : 'center';
	const x = anchor.endsWith('left') ? 0 : anchor.endsWith('right') ? 1 : 0.5;
	const y = anchor.startsWith('top') ? 0 : anchor.startsWith('bottom') ? 1 : 0.5;
	return [x, y];
}

export function hexColor(value: unknown): [number, number, number] {
	const match = typeof value === 'string' ? /^#?([0-9a-fA-F]{6})/.exec(value) : null;
	if (!match) return [0, 0, 0];
	const n = parseInt(match[1], 16);
	return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export function paramEnv(params: readonly ToolParam[], values: Record<string, unknown>): Record<string, DeriveValue> {
	const env: Record<string, DeriveValue> = {};
	for (const param of params) {
		const value = values[param.name];
		if (param.type === 'anchor') {
			const [x, y] = anchorPoint(value);
			env[param.name] = { x, y };
		} else if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'string') {
			env[param.name] = value;
		}
	}
	return env;
}

export function paramUniform(param: ToolParam, value: unknown): UniformValue | null {
	switch (param.type) {
		case 'float':
		case 'integer':
			return Number.isFinite(Number(value)) ? Number(value) : 0;
		case 'checkbox':
			return value === true;
		case 'color':
			return hexColor(value);
		case 'anchor':
			return anchorPoint(value);
		case 'select':
			return Math.max(0, normalizeOptions(param).findIndex((option) => option.value === value));
		default:
			return null;
	}
}

export function uniformList(effect: Pick<EffectTool, 'params' | 'block'>, extra: readonly string[] = []): EffectUniform[] {
	if (effect.block.uniforms.length) return effect.block.uniforms;
	const out = new Map<string, UniformType>();
	for (const param of effect.params) {
		const type = PARAM_UNIFORMS[param.type];
		if (type && NAME.test(param.name)) out.set(param.name, type);
	}
	for (const name of [...Object.keys(effect.block.derive), ...extra]) if (NAME.test(name)) out.set(name, 'float');
	return [...out.entries()].map(([name, type]) => ({ name, type }));
}

export function scalarUniform(value: DeriveScalar): UniformValue | null {
	if (typeof value === 'boolean') return value ? 1 : 0;
	return typeof value === 'number' ? value : null;
}

