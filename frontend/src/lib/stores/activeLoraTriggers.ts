import { derived, writable, type Readable } from 'svelte/store';

/**
 * Aggregates the trigger words of every LoRA currently added in a tab's
 * `lora_picker` field(s), scoped per tab id, so `PromptSection.svelte` can
 * highlight them inside that tab's own segment editors. `LoraPickerField.svelte`
 * registers its resolved trigger words here (one entry per mounted field,
 * keyed by field name, so multiple `lora_picker` fields in the same form don't
 * clobber each other); `activeLoraTriggersForTab` flattens a tab's entries into
 * one deduped list for display.
 *
 * A plain module-level store (rather than Svelte context) because the
 * contributing field and the consuming segment editor live in separate
 * subtrees — `DynamicForm` and `PromptSection` are siblings under
 * `GenerationPanels.svelte`, not parent/child.
 */
const contributionsByTab = writable<Record<string, Record<string, string[]>>>({});

export interface LoraTriggerSource {
	set(words: string[]): void;
	unregister(): void;
}

/** `tabId` is `undefined` when a `lora_picker` field is rendered outside a tab
 * context (e.g. isolation/tests) — contributions are silently dropped. */
export function registerLoraTriggerSource(
	tabId: string | undefined,
	fieldKey: string
): LoraTriggerSource {
	return {
		set(words: string[]) {
			if (!tabId) return;
			contributionsByTab.update((byTab) => ({
				...byTab,
				[tabId]: { ...(byTab[tabId] || {}), [fieldKey]: words }
			}));
		},
		unregister() {
			if (!tabId) return;
			contributionsByTab.update((byTab) => {
				const tabEntries = byTab[tabId];
				if (!tabEntries || !(fieldKey in tabEntries)) return byTab;
				const nextTabEntries = { ...tabEntries };
				delete nextTabEntries[fieldKey];
				return { ...byTab, [tabId]: nextTabEntries };
			});
		}
	};
}

export function activeLoraTriggersForTab(tabId: string | undefined): Readable<string[]> {
	return derived(contributionsByTab, (byTab) => {
		if (!tabId) return [];
		const perField = byTab[tabId] || {};
		const seen = new Set<string>();
		const words: string[] = [];
		for (const list of Object.values(perField)) {
			for (const word of list) {
				if (seen.has(word)) continue;
				seen.add(word);
				words.push(word);
			}
		}
		return words;
	});
}
