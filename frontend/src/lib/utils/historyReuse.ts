import type { GenerationHistoryItem, ImportBundleReuse } from '$lib/types/history';
import type { Tab } from '$lib/types/tabs';
import type { Backend } from '$lib/services/admin-api';

export interface HistoryReuseResult {
	/** Partial tab data to hand to `tabsStore.addTabWithData`. */
	tabData: Partial<Tab>;
	/** True when a `backend_id` was set but is not among the caller-supplied
	 *  available backends (deleted/renamed since) — the caller should surface
	 *  a quiet notice; `tabData` deliberately omits `selectedBackendId` in
	 *  this case so the tab falls back to its normal default-backend
	 *  resolution. */
	backendUnavailable: boolean;
}

/** Common fields `buildHistoryReuseTabData` and `buildImportBundleTabData`
 *  both restore onto a tab. A `GenerationHistoryItem` carries all of these;
 *  an imported bundle's `reuse` payload carries the subset it has (no
 *  `backend_id`, no top-level `seed` — `form_data.seed` covers it). */
interface ReusableSettings {
	preset_id?: string | null;
	mode?: string | null;
	form_name?: string | null;
	form_data?: Record<string, unknown> | null;
	prompt_state?: Record<string, unknown> | null;
	seed?: number | null;
	backend_id?: string | null;
}

function buildReuseTabData(
	source: ReusableSettings,
	availableBackends: Backend[]
): HistoryReuseResult {
	const mode = source.mode || 'txt2img';
	const promptState = source.prompt_state ?? {};

	// form_data.seed and top-level seed come from the exact same backend field
	// (Generation.to_dict() sets seed = form_data.get('seed')), so they can
	// never legitimately disagree. The explicit override below is defensive
	// only — it protects reuse from ever silently dropping the seed if
	// form_data ever lacks the key while the top-level seed is present.
	const formData: Record<string, unknown> = {
		...source.form_data,
		...(source.seed !== undefined && source.seed !== null ? { seed: source.seed } : {})
	};

	const tabData: Partial<Tab> = {
		selectedPreset: source.preset_id ?? null,
		selectedMode: mode,
		selectedVariant: source.form_name ?? null,
		formData,
		...promptState
	};

	if (source.seed !== undefined && source.seed !== null) {
		tabData.seed = source.seed;
	}

	let backendUnavailable = false;
	if (source.backend_id) {
		const stillAvailable = availableBackends.some((b) => b.id === source.backend_id);
		if (stillAvailable) {
			tabData.selectedBackendId = source.backend_id;
		} else {
			backendUnavailable = true;
		}
	}

	return { tabData, backendUnavailable };
}

/** Builds the `Partial<Tab>` used to reuse a past generation as a new
 *  generate-page tab, given the generation's history record and the list of
 *  currently-available backends. Pure and side-effect free (no toasts) so
 *  it stays trivially unit-testable — fire any "backend gone" notice from
 *  the returned `backendUnavailable` flag at the call site instead. */
export function buildHistoryReuseTabData(
	generation: GenerationHistoryItem,
	availableBackends: Backend[]
): HistoryReuseResult {
	return buildReuseTabData(generation, availableBackends);
}

/** Same restoration as `buildHistoryReuseTabData`, for the `reuse` payload
 *  returned by importing a generation bundle. An import never carries a
 *  `backend_id`, so `backendUnavailable` is always false here and the tab
 *  falls back to its normal default-backend resolution. */
export function buildImportBundleTabData(reuse: ImportBundleReuse): HistoryReuseResult {
	return buildReuseTabData(reuse, []);
}
