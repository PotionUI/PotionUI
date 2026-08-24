import { derived, writable, type Readable } from 'svelte/store';

/**
 * Publishes the rows currently selected in a tab's `lora_picker` field(s) —
 * resolved display name, model id and weight — so the chat's @form picker can
 * offer them as browsable per-LoRA suggestions. `LoraPickerField.svelte`
 * contributes (one entry per mounted field, keyed by field name); consumers
 * read a tab's entries via `loraSelectionsForTab`. Keying happens through the
 * field COMPONENT (mounted by type), never through field names.
 *
 * A module-level store rather than Svelte context for the same reason as
 * `activeLoraTriggers.ts`: the contributing field and the consuming chat
 * panel live in separate subtrees.
 */

export interface LoraSelectionRow {
	id: string | null;
	name: string;
	strength: number;
}

const contributionsByTab = writable<Record<string, Record<string, LoraSelectionRow[]>>>({});

export interface LoraSelectionSource {
	set(rows: LoraSelectionRow[]): void;
	unregister(): void;
}

/** `tabId` is `undefined` when a `lora_picker` field is rendered outside a tab
 * context (e.g. isolation/tests) — contributions are silently dropped. */
export function registerLoraSelectionSource(
	tabId: string | undefined,
	fieldKey: string
): LoraSelectionSource {
	return {
		set(rows: LoraSelectionRow[]) {
			if (!tabId) return;
			contributionsByTab.update((byTab) => ({
				...byTab,
				[tabId]: { ...(byTab[tabId] || {}), [fieldKey]: rows }
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

export function loraSelectionsForTab(
	tabId: string | undefined
): Readable<Record<string, LoraSelectionRow[]>> {
	return derived(contributionsByTab, (byTab) => (tabId ? byTab[tabId] || {} : {}));
}
