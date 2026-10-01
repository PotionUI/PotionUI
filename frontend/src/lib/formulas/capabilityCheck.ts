import { processSchemaWithReactions } from '$lib/form/reactions';
import {
	applyCapabilitiesToSchema,
	collectCapabilityModelFields,
	resolveCapabilities,
	validateAgainstCapabilities,
	type CloudCapabilities
} from '$lib/form/capabilityBinder';
import { sharedCapabilityCache } from '$lib/form/capabilityTracker';
import type { CapabilityCheck } from './planApply';

export function buildCapabilityCheck(
	schema: unknown,
	formData: Record<string, unknown>,
	lookup: (modelId: string) => CloudCapabilities | undefined = (id) => sharedCapabilityCache.get(id)
): CapabilityCheck | undefined {
	const modelFields = collectCapabilityModelFields(schema as any);
	if (modelFields.length === 0) return undefined;
	return (patch) => {
		const merged = { ...formData, ...patch };
		const caps = resolveCapabilities(modelFields, merged, lookup);
		const processed = processSchemaWithReactions(schema, merged).processedSchema;
		const hidden = applyCapabilitiesToSchema(processed, caps);
		const invalid = validateAgainstCapabilities(processed, patch, caps);
		for (const name of hidden) if (name in patch) invalid[name] = [];
		return invalid;
	};
}
