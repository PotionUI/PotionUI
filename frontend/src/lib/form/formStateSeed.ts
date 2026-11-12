import { extractAllFields, type FieldConfig } from './reactions';
import { cloudModelId, collectCapabilityModelFields } from './capabilityBinder';
import {
	autopickValue,
	clearedSlotValue,
	collectAutopickSlots,
	decideSlotValue,
	hasModelValue,
	isSlotCleared,
	unavailableNotice,
	type AutopickSearchResult,
	type AutopickSlot,
	type SlotVerdict
} from './modelSlotAutopick';
import { flattenFormData, hydrateFormData, settleFormReactions, type CapabilityLookup } from './formState';
import { missingRequiredModelField, type MissingModelField } from '$lib/generation/requiredModelField';
import { applyAudienceVisibilityToSchema, type FormAudience } from '$lib/utils/audienceFilter';

const MAX_REACTION_ROUNDS = 8;
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000];
const MODEL_FIELD = { type: 'model' };

export interface FormSeedTarget {
	tabId: string;
	presetId: string | null | undefined;
	mode: string | null | undefined;
	variant: string | null | undefined;
	formData: Record<string, unknown> | null | undefined;
	formPublished: boolean;
	audience: FormAudience;
	videoDirectorActive: boolean;
}

export interface FormSeedDeps {
	loadSchema: (presetId: string, mode: string, variant: string | undefined) => Promise<any>;
	search: (slot: AutopickSlot) => Promise<AutopickSearchResult | null>;
	check?: (slot: AutopickSlot, wire: string) => Promise<SlotVerdict>;
	notify?: (message: string) => void;
	loadCapabilities: (modelId: string) => Promise<void>;
	capabilityOf: CapabilityLookup;
	publish: (data: Record<string, unknown>) => void;
	publishMissing: (tabId: string, key: string, missing: MissingModelField | null) => void;
	clearMissing: (tabId: string) => void;
	retryDelaysMs?: number[];
}

export interface FormStateSeed {
	sync(target: FormSeedTarget): void;
	dispose(): void;
}

export function formSchemaKey(presetId: string, mode: string, variant: string | null | undefined): string {
	return `${presetId}-${mode}-${variant ?? ''}`;
}

function signature(target: FormSeedTarget): string {
	return JSON.stringify([
		target.tabId,
		target.presetId ?? null,
		target.mode ?? null,
		target.variant ?? null,
		target.formPublished,
		target.audience,
		target.videoDirectorActive,
		target.formData ?? {}
	]);
}

export function createFormStateSeed(deps: FormSeedDeps): FormStateSeed {
	const retryDelays = deps.retryDelaysMs ?? RETRY_DELAYS_MS;
	let disposed = false;
	let run = 0;
	let handled = '';
	let attempts = 0;
	let retryTimer: ReturnType<typeof setTimeout> | undefined;
	let reportedTab: string | null = null;

	const isCurrent = (id: number) => !disposed && id === run;

	function cancelRetry() {
		clearTimeout(retryTimer);
		retryTimer = undefined;
	}

	function forgetMissing() {
		if (reportedTab !== null) deps.clearMissing(reportedTab);
		reportedTab = null;
	}

	function start(target: FormSeedTarget) {
		cancelRetry();
		const id = ++run;
		void seed(target, id);
	}

	function scheduleRetry(target: FormSeedTarget, id: number) {
		if (attempts >= retryDelays.length) return;
		const delay = retryDelays[attempts++];
		retryTimer = setTimeout(() => {
			retryTimer = undefined;
			if (isCurrent(id)) start(target);
		}, delay);
	}

	async function seed(target: FormSeedTarget, id: number) {
		const presetId = target.presetId as string;
		const mode = target.mode as string;
		const variant = target.variant ?? undefined;
		const key = formSchemaKey(presetId, mode, variant);
		let schema: any;
		try {
			schema = await deps.loadSchema(presetId, mode, variant);
		} catch {
			if (isCurrent(id)) scheduleRetry(target, id);
			return;
		}
		if (!isCurrent(id) || !schema) return;

		const incoming = (target.formData ?? {}) as Record<string, any>;
		const pinned = new Set(Object.keys(incoming));
		const modelFields = collectCapabilityModelFields(schema);
		const settle = (data: Record<string, any>, baseline: Record<string, any> | null) =>
			settleFormReactions(data, schema, {
				modelFields,
				capabilityOf: deps.capabilityOf,
				baseline,
				pinned,
				loaded: true,
				maxRounds: MAX_REACTION_ROUNDS
			});

		let state = settle(hydrateFormData(schema, incoming), null);
		let unverified: ReadonlySet<string> = new Set();
		const report = () => {
			reportedTab = target.tabId;
			deps.publishMissing(
				target.tabId,
				key,
				missingRequiredModelField(extractAllFields(state.pass.processedSchema), state.data, unverified)
			);
		};
		report();

		applyAudienceVisibilityToSchema(state.pass.processedSchema, target.audience, undefined, target.videoDirectorActive);
		const slotFields = Object.values(state.pass.processedSchema?.properties ?? {}) as FieldConfig[];
		const labels = new Map(extractAllFields(state.pass.processedSchema).map((field) => [field.name, field.title || field.name]));
		const slots = collectAutopickSlots(slotFields, state.data, presetId, true).filter(
			(slot) => slot.current || !isSlotCleared(target.tabId, slot.name)
		);
		const waiting = new Set(slots.filter((slot) => slot.current).map((slot) => slot.name));
		unverified = waiting;
		report();

		await Promise.all(
			slots.map(async (slot) => {
				const wire = slot.current ?? '';
				let verdict: SlotVerdict = 'unknown';
				let result: AutopickSearchResult | null = null;
				try {
					if (wire && deps.check) verdict = await deps.check(slot, wire);
					if (isCurrent(id) && (!wire || verdict === 'unusable')) result = await deps.search(slot);
				} catch {
					result = null;
				}
				if (!isCurrent(id)) return;
				waiting.delete(slot.name);
				const restored = hasModelValue(MODEL_FIELD, incoming[slot.name]);
				const decision = decideSlotValue({
					current: wire,
					restored,
					cleared: isSlotCleared(target.tabId, slot.name),
					verdict,
					result
				});
				if (decision.action === 'pick') {
					state = settle({ ...state.data, [slot.name]: autopickValue(slot, decision.model) }, state.data);
				} else if (decision.action === 'clear') {
					state = settle({ ...state.data, [slot.name]: clearedSlotValue(slot) }, state.data);
					if (restored) deps.notify?.(unavailableNotice(labels.get(slot.name) ?? slot.name));
				}
				report();
			})
		);
		unverified = new Set();
		report();
		if (!isCurrent(id)) return;

		const cloudIds = [...new Set(modelFields.map((field) => cloudModelId(state.data[field])))].filter(
			(modelId): modelId is string => !!modelId
		);
		if (cloudIds.length > 0) {
			await Promise.all(cloudIds.map((modelId) => deps.loadCapabilities(modelId).catch(() => undefined)));
			if (!isCurrent(id)) return;
			state = settle(state.data, state.data);
			report();
		}

		const flattened = flattenFormData(state.data, new Set(state.pass.hiddenByCapability));
		if (target.formPublished && JSON.stringify(flattened) === JSON.stringify(incoming)) return;
		handled = signature({ ...target, formData: flattened, formPublished: true });
		deps.publish(flattened);
	}

	return {
		sync(target) {
			if (disposed) return;
			const next = signature(target);
			if (next === handled) return;
			handled = next;
			attempts = 0;
			if (!target.presetId || !target.mode) {
				run += 1;
				cancelRetry();
				forgetMissing();
				return;
			}
			start(target);
		},
		dispose() {
			disposed = true;
			run += 1;
			cancelRetry();
			forgetMissing();
		}
	};
}
