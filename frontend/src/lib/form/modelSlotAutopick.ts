import type { FieldConfig } from '$lib/form/reactions';
import { fromComponentValue } from '$lib/utils/presetFormOverrides';
import { refFor } from '$lib/utils/modelRef';
import { buildModelSearchRequest } from '$lib/utils/modelSearchParams';

export interface AutopickSlot {
	name: string;
	presetId: string;
	modelType: string;
	modelTypes: string[];
	unquantizedOnly: boolean;
	filterTagIds: string[];
	tagFilters: string[];
	families?: string[];
	current?: string;
}

export interface AutopickSearchResult {
	models: any[];
	total?: number;
}

export interface AutopickDecisionInput {
	hasValue: boolean;
	restored: boolean;
	cleared: boolean;
	result: AutopickSearchResult | null | undefined;
}

export function hasModelValue(field: Pick<FieldConfig, 'type'>, value: unknown): boolean {
	const wire = fromComponentValue(field.type, value);
	return typeof wire === 'string' && wire.trim() !== '';
}

export function decideAutopick(input: AutopickDecisionInput): any | null {
	if (input.hasValue || input.restored || input.cleared) return null;
	const models = input.result?.models;
	if (!Array.isArray(models) || models.length !== 1) return null;
	if (typeof input.result?.total === 'number' && input.result.total !== 1) return null;
	return models[0]?.id ? models[0] : null;
}

export function modelWire(value: unknown): string {
	const wire = fromComponentValue('model', value);
	return typeof wire === 'string' ? wire.trim() : '';
}

export function collectAutopickSlots(
	fields: FieldConfig[],
	data: Record<string, unknown>,
	presetId: string,
	includeFilled = false
): AutopickSlot[] {
	const slots: AutopickSlot[] = [];
	const visit = (list: FieldConfig[]) => {
		for (const field of list ?? []) {
			if (
				field.name &&
				field.type === 'model' &&
				field.required &&
				field.visible !== false &&
				!field.readonly &&
				!field.disabled &&
				(includeFilled || !hasModelValue(field, data[field.name]))
			) {
				const configuration = field.configuration ?? {};
				const modelTypes = Array.isArray(configuration.model_types) ? configuration.model_types : [];
				const scopedPresetId = (field as { preset_id?: string }).preset_id || presetId;
				if (scopedPresetId) {
					const current = data[field.name] as { tagFilters?: string[] } | undefined;
					slots.push({
						name: field.name,
						presetId: scopedPresetId,
						modelType: configuration.model_type || modelTypes[0] || 'checkpoint',
						modelTypes,
						unquantizedOnly: configuration.unquantized_only === true,
						filterTagIds: Array.isArray(configuration.filter_tags) ? configuration.filter_tags : [],
						tagFilters: current?.tagFilters ?? configuration.tags ?? [],
						...(Array.isArray(configuration.families) && configuration.families.length > 0
							? { families: configuration.families }
							: {}),
						...(includeFilled ? { current: modelWire(data[field.name]) } : {})
					});
				}
			}
			if (Array.isArray(field.children)) visit(field.children);
		}
	};
	visit(fields);
	return slots;
}

export function resolveSlotTagIds(
	names: string[],
	tags: Array<{ id?: string; name?: string }> | null | undefined
): string[] | null {
	if (names.length === 0) return [];
	if (!tags) return null;
	const ids: string[] = [];
	for (const name of names) {
		const id = tags.find((t) => t.name === name)?.id;
		if (!id) return null;
		ids.push(id);
	}
	return ids;
}

export function isSlotClearEdit(isModelField: boolean, previous: unknown, next: unknown): boolean {
	if (!isModelField) return false;
	const field = { type: 'model' };
	return hasModelValue(field, previous) && !hasModelValue(field, next);
}

export function autopickValue(slot: AutopickSlot, model: { id?: string | null }) {
	return { modelPath: refFor(model), tagFilters: slot.tagFilters };
}

export function slotSearchRequest(slot: AutopickSlot, tagIds: string[]) {
	return buildModelSearchRequest({
		modelType: slot.modelType,
		presetId: slot.presetId,
		limit: 2,
		tagIds,
		anyTagIds: slot.filterTagIds,
		modelTypes: slot.modelTypes,
		unquantizedOnly: slot.unquantizedOnly,
		families: slot.families
	});
}

export type SlotVerdict = 'usable' | 'unusable' | 'unknown';

export type SlotDecision =
	| { action: 'keep' }
	| { action: 'pick'; model: any }
	| { action: 'clear' };

export interface SlotDecisionInput {
	current: string;
	restored: boolean;
	cleared: boolean;
	verdict: SlotVerdict;
	result: AutopickSearchResult | null | undefined;
}

export function decideSlotValue(input: SlotDecisionInput): SlotDecision {
	if (!input.current) {
		const model = decideAutopick({ hasValue: false, restored: false, cleared: input.cleared, result: input.result });
		return model ? { action: 'pick', model } : { action: 'keep' };
	}
	if (input.verdict !== 'unusable') return { action: 'keep' };
	if (input.restored) return { action: 'clear' };
	const model = decideAutopick({ hasValue: false, restored: false, cleared: false, result: input.result });
	return model ? { action: 'pick', model } : { action: 'clear' };
}

export function clearedSlotValue(slot: AutopickSlot) {
	return { modelPath: '', tagFilters: slot.tagFilters };
}

export function unavailableNotice(label: string): string {
	return `${label} was cleared: the model that was set is not available to your account. Pick one of your models.`;
}

const clearedByTab = new Map<string, Set<string>>();

export function markSlotCleared(tabKey: string, name: string): void {
	let set = clearedByTab.get(tabKey);
	if (!set) {
		set = new Set();
		clearedByTab.set(tabKey, set);
	}
	set.add(name);
}

export function isSlotCleared(tabKey: string, name: string): boolean {
	return clearedByTab.get(tabKey)?.has(name) ?? false;
}

export function forgetClearedSlots(tabKey?: string): void {
	if (tabKey === undefined) clearedByTab.clear();
	else clearedByTab.delete(tabKey);
}

export interface AutopickSyncInput {
	loadKey: string;
	slots: AutopickSlot[];
	isPinned: (name: string) => boolean;
	isEmpty: (name: string) => boolean;
	onPick: (slot: AutopickSlot, model: any) => void;
	currentWire?: (name: string) => string;
	onClear?: (slot: AutopickSlot) => void;
	onPending?: (names: ReadonlySet<string>) => void;
}

export interface ModelSlotAutopick {
	sync(input: AutopickSyncInput): void;
	dispose(): void;
}

export interface ModelSlotAutopickDeps {
	tabKey: string;
	search: (slot: AutopickSlot) => Promise<AutopickSearchResult | null>;
	check?: (slot: AutopickSlot, wire: string) => Promise<SlotVerdict>;
	debounceMs?: number;
}

export function createModelSlotAutopick(deps: ModelSlotAutopickDeps): ModelSlotAutopick {
	const debounceMs = deps.debounceMs ?? 150;
	let currentLoadKey = '';
	let requested = new Set<string>();
	let pending = new Set<string>();
	let queue: Array<{ slot: AutopickSlot; input: AutopickSyncInput }> = [];
	let timer: ReturnType<typeof setTimeout> | undefined;
	let disposed = false;

	function settlePending(input: AutopickSyncInput, name: string) {
		if (!pending.delete(name)) return;
		input.onPending?.(new Set(pending));
	}

	async function run(slot: AutopickSlot, input: AutopickSyncInput) {
		const wire = slot.current ?? '';
		const stale = () =>
			disposed ||
			input.loadKey !== currentLoadKey ||
			(input.currentWire ? input.currentWire(slot.name) !== wire : wire === '' ? !input.isEmpty(slot.name) : false);
		let verdict: SlotVerdict = 'unknown';
		let result: AutopickSearchResult | null = null;
		try {
			if (wire && deps.check) verdict = await deps.check(slot, wire);
			if (!stale() && (!wire || verdict === 'unusable')) result = await deps.search(slot);
		} catch {
			result = null;
		}
		if (stale()) {
			if (!disposed && input.loadKey === currentLoadKey) settlePending(input, slot.name);
			return;
		}
		const decision = decideSlotValue({
			current: wire,
			restored: input.isPinned(slot.name),
			cleared: isSlotCleared(deps.tabKey, slot.name),
			verdict,
			result
		});
		if (decision.action === 'pick') input.onPick(slot, decision.model);
		else if (decision.action === 'clear') input.onClear?.(slot);
		settlePending(input, slot.name);
	}

	function flush() {
		timer = undefined;
		const batch = queue;
		queue = [];
		for (const { slot, input } of batch) void run(slot, input);
	}

	return {
		sync(input) {
			if (disposed) return;
			if (input.loadKey !== currentLoadKey) {
				currentLoadKey = input.loadKey;
				requested = new Set();
				pending = new Set();
				queue = [];
			}
			for (const slot of input.slots) {
				const wire = slot.current ?? '';
				if (isSlotCleared(deps.tabKey, slot.name) && !wire) continue;
				if (requested.has(slot.name)) continue;
				requested.add(slot.name);
				if (wire) pending.add(slot.name);
				queue.push({ slot, input });
			}
			if (pending.size > 0) input.onPending?.(new Set(pending));
			if (queue.length > 0 && timer === undefined) timer = setTimeout(flush, debounceMs);
		},
		dispose() {
			disposed = true;
			queue = [];
			if (timer !== undefined) clearTimeout(timer);
			timer = undefined;
		}
	};
}
