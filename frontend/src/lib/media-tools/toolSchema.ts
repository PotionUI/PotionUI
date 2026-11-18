import type { FieldConfig } from '$lib/form/reactions';
import { logger } from '$lib/utils/logger';
import {
	KNOWN_PARAM_TYPES,
	fieldTypeFor,
	normalizeOptions,
	paramLabel,
	stepFor,
	type ParamFieldType
} from './params';
import type { ToolParam } from './types';

export interface ToolSchemaOptions {
	compactSliders?: boolean;
	sliderLayout?: 'inline';
	pairNumberBoxes?: boolean;
	signedSliders?: boolean;
}

const warned = new Set<string>();

function warnUnsupported(param: ToolParam): void {
	if (!import.meta.env?.DEV) return;
	const key = `${param.name}:${param.type}`;
	if (warned.has(key)) return;
	warned.add(key);
	logger.warn(`Tool param "${param.name}" has unsupported type "${param.type}"; shown as a text field.`);
}

function strictlyInside(param: ToolParam): boolean {
	return (
		typeof param.default === 'number' &&
		typeof param.min === 'number' &&
		typeof param.max === 'number' &&
		param.default > param.min &&
		param.default < param.max
	);
}

function fieldFor(param: ToolParam, type: ParamFieldType, options: ToolSchemaOptions): FieldConfig {
	const config: Record<string, unknown> = {
		type,
		name: param.name,
		title: paramLabel(param),
		description: param.description,
		default: param.default
	};
	switch (type) {
		case 'segmented':
		case 'select':
			config.options = normalizeOptions(param);
			break;
		case 'slider':
			config.minimum = param.min;
			config.maximum = param.max;
			config.step = stepFor(param);
			config.unit = param.unit;
			if (options.compactSliders ?? true) {
				config.reset = true;
				config.legend = false;
				if (strictlyInside(param)) config.fill = 'from-default';
			} else config.reset = false;
			if (options.sliderLayout) config.layout = options.sliderLayout;
			if (options.signedSliders && typeof param.min === 'number' && param.min < 0) config.signed = true;
			break;
		case 'seed':
			config.minimum = param.min ?? 0;
			config.maximum = param.max ?? 2 ** 31 - 1;
			config.step = 1;
			config.default = param.default ?? 0;
			break;
		case 'number_box':
			config.minimum = param.min;
			config.maximum = param.max;
			config.step = stepFor(param);
			config.unit = param.unit;
			config.integer = param.type === 'integer';
			config.placeholder = param.placeholder ?? 'Auto';
			break;
		case 'color':
			config.allow_transparent = param.allow_transparent === true;
			break;
		case 'string':
			config.placeholder = param.placeholder;
			break;
		default:
			break;
	}
	if (param.when) {
		config.visible = false;
		config.reactions = [{ when: param.when, then: { set_visibility: true } }];
	}
	return config as unknown as FieldConfig;
}

export function paramField(param: ToolParam, options: ToolSchemaOptions = {}): FieldConfig {
	if (!KNOWN_PARAM_TYPES.includes(param.type)) warnUnsupported(param);
	return fieldFor(param, fieldTypeFor(param), options);
}

export function toolParamsToSchema(params: readonly ToolParam[], options: ToolSchemaOptions = {}): FieldConfig[] {
	const pair = options.pairNumberBoxes ?? true;
	const fields: FieldConfig[] = [];
	let run: FieldConfig[] = [];
	const flush = () => {
		if (run.length < 2) fields.push(...run);
		else {
			for (let at = 0; at < run.length; at += 2) {
				const group = run.slice(at, at + 2);
				if (group.length === 1) fields.push(group[0]);
				else
					fields.push({
						type: 'row',
						name: `pair_${group.map((field) => field.name).join('_')}`,
						configuration: { columns: 2 },
						children: group
					});
			}
		}
		run = [];
	};
	for (const param of params) {
		const field = paramField(param, options);
		if (pair && field.type === 'number_box' && !param.when) {
			run.push(field);
			continue;
		}
		flush();
		fields.push(field);
	}
	flush();
	return fields;
}
