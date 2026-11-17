import type { MarkState } from '$lib/components/generation-panel/barState';

export const START_LABEL = 'Generate';
export const CANCEL_LABEL = 'Cancel generation';
export const STARTING_LABEL = 'Starting the edit';

export interface MarkInput {
	running: boolean;
	starting: boolean;
	reason: string | null;
}

export interface Mark {
	state: MarkState;
	label: string;
	disabled: boolean;
}

export function markOf(input: MarkInput): Mark {
	if (input.running) return { state: 'running', label: CANCEL_LABEL, disabled: false };
	if (input.starting) return { state: 'disabled', label: STARTING_LABEL, disabled: true };
	if (input.reason) return { state: 'disabled', label: input.reason, disabled: true };
	return { state: 'ready', label: START_LABEL, disabled: false };
}

export function blockedReason(input: {
	hasSetup: boolean;
	promptRequired: boolean;
	prompt: string;
	regionRequired: boolean;
	hasArea: boolean;
	redoArea: boolean;
}): string | null {
	if (!input.hasSetup) return 'Choose a preset first';
	if (input.redoArea) return 'Mark the area again';
	if (input.regionRequired && !input.hasArea) return 'Mark the object first';
	if (input.promptRequired && !input.prompt.trim()) return 'Describe the change first';
	return null;
}
