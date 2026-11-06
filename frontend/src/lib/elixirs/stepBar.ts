import { writable } from 'svelte/store';
import type { StepConfirm, StepConflict } from './stepEditor';
import type { StepOption } from './stepEdit';

export interface StepBarModel {
	elixirName: string;
	stepLabel: string;
	follow: boolean;
	sessionName: string | null;
	steps: StepOption[];
	index: number;
	edits: number;
	saving: boolean;
	opening: boolean;
	confirm: StepConfirm | null;
	conflict: StepConflict | null;
	back(): void;
	go(nodeId: string): void;
	save(): void;
	answerConfirm(choice: 'save' | 'discard' | 'cancel'): void;
	resolveConflict(choice: 'cancel' | 'reload' | 'overwrite'): void;
}

const store = writable<Record<string, StepBarModel>>({});

export const stepBars = { subscribe: store.subscribe };

export function publishStepBar(toolTabId: string, model: StepBarModel | null): void {
	store.update((current) => {
		if (!model) {
			if (!(toolTabId in current)) return current;
			const { [toolTabId]: _removed, ...rest } = current;
			return rest;
		}
		return { ...current, [toolTabId]: model };
	});
}
