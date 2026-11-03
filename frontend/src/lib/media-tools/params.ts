import { ANCHOR_VALUES } from '$lib/components/form-fields/anchorValues';
import { evaluateCondition } from '$lib/form/reactions';
import type { ToolDTO, ToolParam, ToolParamOption } from './types';

export type ControlKind =
	| 'segmented'
	| 'dropdown'
	| 'slider'
	| 'number'
	| 'switch'
	| 'color'
	| 'anchor'
	| 'text';

export const SEGMENTED_MAX_OPTIONS = 4;

export { ANCHOR_VALUES, type AnchorValue } from '$lib/components/form-fields/anchorValues';

export function humanize(value: unknown): string {
	const words = String(value ?? '')
		.replace(/[_-]+/g, ' ')
		.trim();
	return words ? words.charAt(0).toUpperCase() + words.slice(1) : '';
}

export function paramLabel(param: ToolParam): string {
	return param.label?.trim() || humanize(param.name);
}

export function normalizeOptions(param: ToolParam): ToolParamOption[] {
	return (param.options ?? []).map((option) =>
		typeof option === 'object' && option !== null
			? { label: option.label ?? humanize(option.value), value: option.value }
			: { label: humanize(option), value: option }
	);
}

export function hasRange(param: ToolParam): boolean {
	return (
		typeof param.min === 'number' &&
		Number.isFinite(param.min) &&
		typeof param.max === 'number' &&
		Number.isFinite(param.max) &&
		param.max > param.min
	);
}

export function controlFor(param: ToolParam): ControlKind {
	switch (param.type) {
		case 'anchor':
			return 'anchor';
		case 'select':
			if (param.widget === 'anchor') return 'anchor';
			return normalizeOptions(param).length <= SEGMENTED_MAX_OPTIONS ? 'segmented' : 'dropdown';
		case 'integer':
		case 'float':
			return hasRange(param) ? 'slider' : 'number';
		case 'checkbox':
			return 'switch';
		case 'color':
			return 'color';
		case 'lut':
			return 'dropdown';
		default:
			return 'text';
	}
}

export function stepFor(param: ToolParam): number {
	if (typeof param.step === 'number' && param.step > 0) return param.step;
	return param.type === 'integer' ? 1 : 0.01;
}

export function toFieldConfig(param: ToolParam): Record<string, unknown> {
	const config: Record<string, unknown> = {
		title: paramLabel(param),
		description: param.description,
		default: param.default,
		unit: param.unit
	};
	switch (controlFor(param)) {
		case 'segmented':
		case 'dropdown':
			config.options = normalizeOptions(param);
			break;
		case 'slider':
			config.minimum = param.min;
			config.maximum = param.max;
			config.step = stepFor(param);
			break;
		case 'number':
			config.minimum = param.min;
			config.maximum = param.max;
			config.step = stepFor(param);
			config.integer = param.type === 'integer';
			config.placeholder = param.placeholder ?? 'Auto';
			break;
		case 'color':
			config.allow_transparent = param.allow_transparent === true;
			break;
		case 'text':
			config.placeholder = param.placeholder;
			break;
		default:
			break;
	}
	return config;
}

export function isParamVisible(param: ToolParam, values: Record<string, unknown>): boolean {
	if (!param.when) return true;
	return evaluateCondition(param.when, values);
}

export function visibleParams(
	params: readonly ToolParam[],
	values: Record<string, unknown>
): ToolParam[] {
	return params.filter((param) => isParamVisible(param, values));
}

function typedDefault(param: ToolParam): unknown {
	if (param.default !== undefined) return param.default;
	if ((param.type === 'integer' || param.type === 'float') && hasRange(param)) return param.min;
	switch (param.type) {
		case 'checkbox':
			return false;
		case 'select':
			return normalizeOptions(param)[0]?.value;
		case 'anchor':
			return 'center';
		case 'string':
			return '';
		default:
			return undefined;
	}
}

export function acceptsValue(param: ToolParam, value: unknown): boolean {
	switch (controlFor(param)) {
		case 'segmented':
		case 'dropdown':
			return normalizeOptions(param).some((option) => String(option.value) === String(value));
		case 'anchor':
			return (ANCHOR_VALUES as readonly string[]).includes(String(value));
		case 'slider':
		case 'number': {
			if (typeof value !== 'number' || !Number.isFinite(value)) return false;
			if (typeof param.min === 'number' && value < param.min) return false;
			if (typeof param.max === 'number' && value > param.max) return false;
			return true;
		}
		case 'switch':
			return typeof value === 'boolean';
		case 'color':
			return (
				typeof value === 'string' &&
				(/^#[0-9a-fA-F]{6}$/.test(value) || (param.allow_transparent === true && value === 'transparent'))
			);
		default:
			return typeof value === 'string';
	}
}

export function initialValues(
	tool: Pick<ToolDTO, 'params' | 'last_params'>
): Record<string, unknown> {
	const last = tool.last_params ?? {};
	const values: Record<string, unknown> = {};
	for (const param of tool.params) {
		const remembered = last[param.name];
		values[param.name] =
			remembered !== undefined && acceptsValue(param, remembered)
				? remembered
				: typedDefault(param);
	}
	return values;
}

export function paramIssues(
	params: readonly ToolParam[],
	values: Record<string, unknown>
): Record<string, string> {
	const issues: Record<string, string> = {};
	for (const param of visibleParams(params, values)) {
		const value = values[param.name];
		const control = controlFor(param);
		if (control !== 'slider' && control !== 'number') continue;
		if (value === undefined || value === null || value === '') continue;
		if (typeof value !== 'number' || !Number.isFinite(value)) {
			issues[param.name] = 'Enter a number';
		} else if (typeof param.min === 'number' && value < param.min) {
			issues[param.name] = `At least ${param.min}`;
		} else if (typeof param.max === 'number' && value > param.max) {
			issues[param.name] = `At most ${param.max}`;
		}
	}
	return issues;
}

export function buildParams(
	params: readonly ToolParam[],
	values: Record<string, unknown>
): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	for (const param of visibleParams(params, values)) {
		const value = values[param.name];
		if (value === undefined || value === null || value === '') continue;
		out[param.name] = param.type === 'integer' && typeof value === 'number' ? Math.round(value) : value;
	}
	return out;
}
