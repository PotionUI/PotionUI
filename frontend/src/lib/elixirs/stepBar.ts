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

export function stepNeighbours(steps: StepOption[], index: number): { previous: StepOption | null; next: StepOption | null; label: string } {
	return {
		previous: index > 0 ? steps[index - 1] : null,
		next: index >= 0 && index < steps.length - 1 ? steps[index + 1] : null,
		label: `${index + 1}/${steps.length}`
	};
}
