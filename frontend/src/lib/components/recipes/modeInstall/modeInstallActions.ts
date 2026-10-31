import { api } from '$lib/services/api/index';
import type { ModeConsentRequest } from '$lib/services/api/recipeModes';
import type { SetupRun } from '$lib/services/api/setup';
import { extractModesConsent } from './modeInstallModel';

export function replanFor(recipeId: string) {
	return async (target: string, modes: string[]): Promise<ModeConsentRequest | null> => {
		const plan = await api.planRecipe(recipeId, { preselect_modes: modes, target });
		return plan.consent_request ?? null;
	};
}

export function preselectOf(run: Pick<SetupRun, 'safe_input'>): string[] {
	const raw = run.safe_input?.preselect_modes;
	return Array.isArray(raw) ? raw.filter((id): id is string => typeof id === 'string') : [];
}

export interface ModeCatalogEntry {
	label: string;
	description: string | null;
}

export interface ModeRunContext {
	presetName: string;
	modes: Record<string, ModeCatalogEntry>;
}

export function modeContextOf(run: Pick<SetupRun, 'steps' | 'attempts'>): ModeRunContext {
	const attempts = (run.steps ?? []).flatMap((step) => step.attempts ?? []);
	const source = attempts.length > 0 ? attempts : (run.attempts ?? []);
	for (let index = source.length - 1; index >= 0; index -= 1) {
		const request = extractModesConsent(source[index]);
		if (!request) continue;
		const out: Record<string, ModeCatalogEntry> = {};
		for (const mode of request.modes) {
			out[mode.id] = { label: mode.label, description: mode.short_description ?? null };
		}
		return { presetName: request.preset_name, modes: out };
	}
	return { presetName: '', modes: {} };
}
