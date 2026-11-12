import type { GenerationTab, ModeState } from '$lib/types/tabs';
import { DIRECTOR_MODE, LONG_MODE, convertLongVideo, isLongVideoForm } from '$lib/utils/longVideoConversion';

export type LongVideoTabSource = Pick<GenerationTab, 'selectedPreset' | 'modeStateByMode' | 'name'>;

export function pendingLongVideo(tab: Pick<GenerationTab, 'modeStateByMode'>, modeNames: readonly string[]): ModeState | null {
	if (modeNames.length === 0 || modeNames.includes(LONG_MODE) || !modeNames.includes(DIRECTOR_MODE)) return null;
	const stored = tab.modeStateByMode?.[LONG_MODE];
	return stored && isLongVideoForm(stored.formData) ? stored : null;
}

export interface LongVideoTabPlan {
	name: string;
	tabData: Partial<GenerationTab>;
	notes: string[];
}

export function planLongVideoTab(tab: LongVideoTabSource, stored: ModeState): LongVideoTabPlan {
	const conversion = convertLongVideo({ prompt: stored.prompt, formData: stored.formData });
	return {
		name: `${tab.name}: Video Director`,
		tabData: {
			selectedPreset: tab.selectedPreset,
			selectedMode: DIRECTOR_MODE,
			selectedVariant: null,
			formData: conversion.formPatch,
			videoDirector: conversion.directorValue
		},
		notes: conversion.notes
	};
}
