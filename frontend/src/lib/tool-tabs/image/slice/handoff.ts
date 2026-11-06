import { get, type Readable } from 'svelte/store';
import type { EditedMediaItem } from '$lib/services/api/media';
import { isGenerationTab, type GenerationTab, type Tab } from '$lib/types/tabs';
import { referenceFieldsOf, referencePatch, type ReferenceField } from './references';

export interface HandoffTabs extends Readable<{ tabs: Tab[] }> {
	addTabWithData: (name: string, data: Partial<Omit<GenerationTab, 'kind'>>) => string;
	updateTab: (tabId: string, updates: Partial<Omit<GenerationTab, 'kind'>>) => void;
}

export interface HandoffDeps {
	tabs: HandoffTabs;
	loadSchema: (presetId: string, mode: string, variant: string | undefined) => Promise<unknown>;
	oncommit: (tab: GenerationTab, field: ReferenceField, takes: number) => void;
}

export function handOffToNewTab(items: readonly EditedMediaItem[], name: string, deps: HandoffDeps): string {
	const tabId = deps.tabs.addTabWithData(name, {});
	let checked = '';
	let done = false;
	let stop: (() => void) | null = null;
	const finish = () => {
		done = true;
		queueMicrotask(() => stop?.());
	};
	stop = deps.tabs.subscribe((state) => {
		if (done) return;
		const tab = state.tabs.find((candidate): candidate is GenerationTab => candidate.id === tabId && isGenerationTab(candidate));
		if (!tab) {
			finish();
			return;
		}
		if (!tab.selectedPreset || !tab.selectedMode) return;
		const key = `${tab.selectedPreset}|${tab.selectedMode}|${tab.selectedVariant ?? ''}`;
		if (key === checked) return;
		checked = key;
		void deps
			.loadSchema(tab.selectedPreset, tab.selectedMode, tab.selectedVariant ?? undefined)
			.then((schema) => {
				if (done || checked !== key) return;
				const field = referenceFieldsOf(schema)[0];
				const latest = get(deps.tabs).tabs.find((candidate): candidate is GenerationTab => candidate.id === tabId && isGenerationTab(candidate));
				if (!field || !latest) return;
				const current = (latest.formData ?? {})[field.name];
				const held = Array.isArray(current) ? current.length : 0;
				const takes = field.max === null ? items.length : Math.min(items.length, Math.max(0, field.max - held));
				if (!takes) return;
				finish();
				deps.tabs.updateTab(tabId, {
					formData: { ...(latest.formData ?? {}), [field.name]: referencePatch(current, items, takes) },
					formReloadGeneration: (latest.formReloadGeneration ?? 0) + 1
				});
				deps.oncommit(latest, field, takes);
			})
			.catch(() => {
				checked = '';
			});
	});
	if (done) stop();
	return tabId;
}
