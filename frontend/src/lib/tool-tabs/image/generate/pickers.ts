import type { TaskOffer } from '$lib/services/api/editorTasks';
import type { PresetInfo } from '$lib/types/api';
import type { PickerOption } from '$lib/components/preset/PickerSelect.svelte';
import { viaNote } from './taskOptions';
import { howOf, modeName, offerReasonText, offersOfPreset, qualifying, type FormulaChoice } from './setup';

export function presetListOf(offers: readonly TaskOffer[], catalog: readonly PresetInfo[]): PresetInfo[] {
	const known = new Map(catalog.map((preset) => [preset.id, preset]));
	const seen = new Set<string>();
	const list: PresetInfo[] = [];
	for (const offer of offers) {
		if (seen.has(offer.preset_id)) continue;
		seen.add(offer.preset_id);
		list.push(known.get(offer.preset_id) ?? { id: offer.preset_id, name: offer.preset_name, version: '', tags: [] });
	}
	return list;
}

export function presetDisabledReasons(offers: readonly TaskOffer[]): Record<string, string> {
	const reasons: Record<string, string> = {};
	for (const offer of offers) {
		const own = offersOfPreset(offers, offer.preset_id);
		if (own.some((entry) => entry.qualifies) || reasons[offer.preset_id]) continue;
		reasons[offer.preset_id] = offerReasonText(own[0]?.reason ?? null);
	}
	return reasons;
}

function descriptionOf(offer: TaskOffer): string {
	const base = offer.short_description ?? howOf(offer);
	const note = viaNote(offer);
	return note ? `${base.replace(/\.$/, '')}. ${note}` : base;
}

export function modeOptionsOf(offers: readonly TaskOffer[], presetId: string | null | undefined): PickerOption[] {
	if (!presetId) return [];
	return qualifying(offersOfPreset(offers, presetId)).map((offer) => ({
		value: offer.mode,
		label: modeName(offer),
		...(offer.mode_icon ? { icon: offer.mode_icon } : {}),
		description: descriptionOf(offer)
	}));
}

export const NO_FORMULA = '';

export function formulaOptionsOf(formulas: readonly FormulaChoice[]): PickerOption[] {
	return [{ value: NO_FORMULA, label: 'No formula' }, ...formulas.map((formula) => ({ value: formula.id, label: formula.name }))];
}
