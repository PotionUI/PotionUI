import { extractAllFields } from '$lib/form/reactions';
import { collectCapabilityModelFields } from '$lib/form/capabilityBinder';
import { flattenFormData, hydrateFormData, settleFormReactions } from '$lib/form/formState';
import {
	collectAutopickSlots,
	decideAutopick,
	type AutopickSearchResult,
	type AutopickSlot
} from '$lib/form/modelSlotAutopick';
import { MODEL_REF_PREFIX, refFor } from '$lib/utils/modelRef';

const MAX_REACTION_ROUNDS = 8;

export interface ModelSlotDeps {
	loadSchema: (presetId: string, mode: string, variant: string | undefined) => Promise<any>;
	search: (slot: AutopickSlot) => Promise<AutopickSearchResult | null>;
	findInstalled: (slot: AutopickSlot, value: string) => Promise<{ id?: string | null } | null>;
}

export type ModelSlotResult =
	| { ok: true; formData: Record<string, unknown> }
	| { ok: false; message: string };

function wireOf(value: unknown): string {
	const raw = value && typeof value === 'object' ? (value as { modelPath?: unknown }).modelPath : value;
	return typeof raw === 'string' ? raw.trim() : '';
}

export async function completeModelSlots(
	presetId: string,
	mode: string,
	variant: string | undefined,
	formData: Record<string, unknown> | null | undefined,
	deps: ModelSlotDeps
): Promise<ModelSlotResult> {
	const incoming = { ...(formData ?? {}) };
	let schema: any;
	try {
		schema = await deps.loadSchema(presetId, mode, variant);
	} catch {
		return { ok: true, formData: incoming };
	}
	if (!schema) return { ok: true, formData: incoming };

	const settled = settleFormReactions(hydrateFormData(schema, incoming), schema, {
		modelFields: collectCapabilityModelFields(schema),
		capabilityOf: () => undefined,
		baseline: null,
		pinned: new Set(Object.keys(incoming)),
		loaded: true,
		maxRounds: MAX_REACTION_ROUNDS
	});
	const fields = extractAllFields(settled.pass.processedSchema);
	const slots = collectAutopickSlots(fields, {}, presetId);
	const flat = flattenFormData(settled.data, new Set(settled.pass.hiddenByCapability));
	const labels = new Map(fields.map((field) => [field.name, field.title || field.name]));

	const picked: Record<string, string> = {};
	const unresolved: string[] = [];
	for (const slot of slots) {
		const stored = wireOf(flat[slot.name]);
		if (stored.startsWith(MODEL_REF_PREFIX)) continue;
		if (stored) {
			const installed = await deps.findInstalled(slot, stored).catch(() => null);
			if (installed?.id) {
				picked[slot.name] = refFor(installed);
				continue;
			}
		}
		let result: AutopickSearchResult | null = null;
		try {
			result = await deps.search(slot);
		} catch {
			result = null;
		}
		const model = decideAutopick({ hasValue: false, restored: false, cleared: false, result });
		if (model) picked[slot.name] = refFor(model);
		else unresolved.push(labels.get(slot.name) ?? slot.name);
	}

	if (unresolved.length > 0) {
		return {
			ok: false,
			message: `Choose ${unresolved.join(', ')} in the session this step uses and save it.`
		};
	}
	return { ok: true, formData: { ...incoming, ...picked } };
}
