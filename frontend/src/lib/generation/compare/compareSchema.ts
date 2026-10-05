import {
	applyCapabilitiesToSchema,
	cloudModelId,
	collectCapabilityModelFields,
	resolveCapabilities,
	type CloudCapabilities
} from '$lib/form/capabilityBinder';

export type CapabilityLookup = (modelId: string) => CloudCapabilities | undefined;

export function compareSchemaSignature(
	key: string,
	schema: unknown,
	formData: Record<string, unknown>,
	revision: number
): string {
	const models = collectCapabilityModelFields(schema as never).map((field) => cloudModelId(formData[field]) ?? '');
	return `${key}|${revision}|${models.join(',')}`;
}

export function schemaForCompare(schema: unknown, formData: Record<string, unknown>, lookup: CapabilityLookup): unknown {
	const modelFields = collectCapabilityModelFields(schema as never);
	if (modelFields.length === 0) return schema;
	const copy = JSON.parse(JSON.stringify(schema));
	applyCapabilitiesToSchema(copy, resolveCapabilities(modelFields, formData, lookup));
	return copy;
}

export function quantityFieldsOf(schema: unknown): string[] {
	const raw = (schema as { quantity_fields?: unknown } | null)?.quantity_fields;
	return Array.isArray(raw) ? raw.filter((name): name is string => typeof name === 'string') : [];
}
