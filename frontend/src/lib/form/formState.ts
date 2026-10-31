import { getSchemaDefaults } from './defaults';
import { applyMergeFrom } from './mergeFrom';
import { processSchemaWithReactions, staleOptionFields, type ValueGate } from './reactions';
import { annotateLockHints } from './lockHints';
import {
	applyCapabilitiesToSchema,
	capabilityValueChanges,
	omitCapabilityHidden,
	resolveCapabilities,
	type CloudCapabilities
} from './capabilityBinder';
import { applyReadonlyToSchema } from '$lib/utils/readonlyFilter';
import { applyReactionValueChanges } from '$lib/components/dynamicFormReactionApply';

export type CapabilityLookup = (modelId: string) => CloudCapabilities | undefined;

export interface ReactionPass {
	processedSchema: any;
	valueChanges: Record<string, any>;
	hiddenByCapability: string[];
}

export interface SettleOptions {
	modelFields: string[];
	capabilityOf: CapabilityLookup;
	baseline: Record<string, any> | null;
	pinned: ReadonlySet<string>;
	loaded: boolean;
	maxRounds: number;
}

export interface SettledForm {
	data: Record<string, any>;
	pass: ReactionPass;
	unsettled: boolean;
}

export function mergeFormData(
	schemaDefaults: Record<string, any>,
	userData: Record<string, any> | null
): Record<string, any> {
	if (!userData) return { ...schemaDefaults };

	const merged = { ...schemaDefaults };

	for (const key in userData) {
		if (key.endsWith('_tagFilters')) continue;

		if (merged[key] && typeof merged[key] === 'object' && merged[key].hasOwnProperty('modelPath')) {
			const modelPath = userData[key] || '';
			const tagFiltersKey = `${key}_tagFilters`;
			const tagFilters = userData[tagFiltersKey] || merged[key].tagFilters || [];

			merged[key] = {
				modelPath: modelPath,
				tagFilters: tagFilters
			};
		} else {
			merged[key] = userData[key];
		}
	}

	return merged;
}

export function hydrateFormData(schema: any, incoming: Record<string, any> | null | undefined): Record<string, any> {
	return mergeFormData(getSchemaDefaults(schema), applyMergeFrom(schema, incoming ?? null));
}

export function flattenFormData(data: Record<string, any>, hiddenNames: ReadonlySet<string>): Record<string, any> {
	const flattened: Record<string, any> = {};

	for (const [key, value] of Object.entries(omitCapabilityHidden(data, hiddenNames))) {
		if (value && typeof value === 'object' && !Array.isArray(value) && value.constructor === Object) {
			if (value.hasOwnProperty('modelPath') && value.hasOwnProperty('tagFilters')) {
				flattened[key] = value.modelPath;
				flattened[`${key}_tagFilters`] = value.tagFilters;
			} else if (value.hasOwnProperty('path')) {
				flattened[key] = value.path;
			} else {
				flattened[key] = value;
			}
		} else {
			flattened[key] = value;
		}
	}

	return flattened;
}

export function reprocessSchema(
	schema: any,
	data: Record<string, any>,
	modelFields: string[],
	capabilityOf: CapabilityLookup,
	gate?: ValueGate
): ReactionPass {
	const result = processSchemaWithReactions(schema, data, gate);
	applyReadonlyToSchema(result.processedSchema);
	annotateLockHints(result.processedSchema, data);
	const capabilities = resolveCapabilities(modelFields, data, capabilityOf);
	const hiddenByCapability = applyCapabilitiesToSchema(result.processedSchema, capabilities);
	const valueChanges = {
		...result.valueChanges,
		...capabilityValueChanges(result.processedSchema, data, capabilities)
	};
	return { processedSchema: result.processedSchema, valueChanges, hiddenByCapability };
}

export function settleFormReactions(start: Record<string, any>, schema: any, options: SettleOptions): SettledForm {
	const { modelFields, capabilityOf, baseline, pinned, loaded, maxRounds } = options;
	let data = start;
	let gate: ValueGate | undefined = loaded
		? {
				previous: baseline,
				pinned,
				stale: baseline === null ? staleOptionFields(schema, data) : undefined
			}
		: undefined;
	let pass = reprocessSchema(schema, data, modelFields, capabilityOf, gate);
	let unsettled = false;
	for (let round = 0; round < maxRounds && loaded; round++) {
		const applied = applyReactionValueChanges(data, pass.valueChanges);
		unsettled = applied.changed;
		if (!applied.changed) break;
		gate = { previous: data };
		data = applied.data;
		pass = reprocessSchema(schema, data, modelFields, capabilityOf, gate);
	}
	return { data, pass, unsettled };
}
