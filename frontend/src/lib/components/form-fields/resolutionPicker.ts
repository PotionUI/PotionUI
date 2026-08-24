/**
 * Pure logic for the resolution picker panel: search filtering, tier
 * grouping, and custom W×H validation. Kept free of Svelte/DOM so it can be
 * unit-tested directly.
 */

export interface ResolutionOptionLike {
	value: string;
	description?: string;
	ratio?: number[];
	group?: string;
	tier?: string;
}

/** "1024x1024" -> { width: 1024, height: 1024 }, or null if unparseable. */
export function parseResolutionValue(value: string): { width: number; height: number } | null {
	if (!value || typeof value !== 'string') return null;
	const parts = value.split('x');
	if (parts.length !== 2) return null;
	const width = parseInt(parts[0], 10);
	const height = parseInt(parts[1], 10);
	if (!Number.isFinite(width) || !Number.isFinite(height)) return null;
	return { width, height };
}

/** An option's tier, falling back the same way the backend normalizer does
 * (own tier -> group -> "Standard") so a stale/partial payload still groups. */
export function optionTier(opt: ResolutionOptionLike): string {
	return opt.tier || opt.group || 'Standard';
}

export function optionRatioLabel(opt: ResolutionOptionLike): string | null {
	if (opt.ratio && opt.ratio.length === 2) return `${opt.ratio[0]}:${opt.ratio[1]}`;
	return null;
}

function normalizeForSearch(value: string): string {
	return value.toLowerCase().replace(/[×✕]/g, 'x').trim();
}

/**
 * True if `option` matches a free-text `query` against its resolved WxH
 * (either "x" or "×" separator), its ratio ("16:9"), and its
 * description/tier words ("portrait", "compact"). Empty query matches
 * everything.
 */
export function matchesResolutionQuery(option: ResolutionOptionLike, query: string): boolean {
	const q = normalizeForSearch(query);
	if (!q) return true;

	const haystacks: string[] = [normalizeForSearch(option.value)];
	const ratio = optionRatioLabel(option);
	if (ratio) haystacks.push(ratio.toLowerCase());
	if (option.description) haystacks.push(option.description.toLowerCase());
	haystacks.push(optionTier(option).toLowerCase());

	return haystacks.some((h) => h.includes(q));
}

export function filterResolutionOptions<T extends ResolutionOptionLike>(options: T[], query: string): T[] {
	return options.filter((opt) => matchesResolutionQuery(opt, query));
}

export interface TierSection<T extends ResolutionOptionLike> {
	tier: string;
	options: T[];
}

/**
 * Groups options by resolved tier, preserving each tier's first-appearance
 * order in `options` - the flattened list is already authored size-ordered
 * per family (large-to-small for Flux/SDXL, small-to-large for zImage), and
 * that authored order is more meaningful than an alphabetical one.
 */
export function groupOptionsByTier<T extends ResolutionOptionLike>(options: T[]): TierSection<T>[] {
	const order: string[] = [];
	const byTier = new Map<string, T[]>();
	for (const opt of options) {
		const tier = optionTier(opt);
		if (!byTier.has(tier)) {
			byTier.set(tier, []);
			order.push(tier);
		}
		byTier.get(tier)!.push(opt);
	}
	return order.map((tier) => ({ tier, options: byTier.get(tier)! }));
}

export interface CustomResolutionResult {
	ok: boolean;
	value?: string;
	error?: string;
}

// MIN/MAX mirror src/features/fields/resolution.py's Resolution.input()
// range check exactly, so a rejected value never round-trips to the server.
// GRANULARITY is a client-only UX guard for values a user is typing from
// scratch - the backend can't require it universally (some shipped preset
// options, e.g. flux.yml's "Portrait" Full tier at 1140x1472, aren't a
// multiple of 8 themselves), but nudging freshly-typed custom sizes onto an
// 8px step keeps them safe for every native family's own (larger) snap.
export const CUSTOM_RESOLUTION_MIN = 64;
export const CUSTOM_RESOLUTION_MAX = 8192;
export const CUSTOM_RESOLUTION_GRANULARITY = 8;

/** Validates a custom W×H entry: the MIN/MAX bound the backend also
 * enforces, plus a client-only 8px snap so a hand-typed value stays safe. */
export function parseCustomResolution(rawWidth: string, rawHeight: string): CustomResolutionResult {
	const widthText = (rawWidth ?? '').trim();
	const heightText = (rawHeight ?? '').trim();
	if (!widthText || !heightText) {
		return { ok: false, error: 'Enter both width and height.' };
	}

	if (!/^\d+$/.test(widthText) || !/^\d+$/.test(heightText)) {
		return { ok: false, error: 'Width and height must be whole numbers.' };
	}

	const width = parseInt(widthText, 10);
	const height = parseInt(heightText, 10);

	if (
		width < CUSTOM_RESOLUTION_MIN ||
		width > CUSTOM_RESOLUTION_MAX ||
		height < CUSTOM_RESOLUTION_MIN ||
		height > CUSTOM_RESOLUTION_MAX
	) {
		return {
			ok: false,
			error: `Width and height must be between ${CUSTOM_RESOLUTION_MIN} and ${CUSTOM_RESOLUTION_MAX}px.`
		};
	}

	if (width % CUSTOM_RESOLUTION_GRANULARITY !== 0 || height % CUSTOM_RESOLUTION_GRANULARITY !== 0) {
		return {
			ok: false,
			error: `Width and height must be a multiple of ${CUSTOM_RESOLUTION_GRANULARITY}px.`
		};
	}

	return { ok: true, value: `${width}x${height}` };
}
