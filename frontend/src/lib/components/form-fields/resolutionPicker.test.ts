import { describe, it, expect } from 'vitest';
import {
	filterResolutionOptions,
	groupOptionsByTier,
	matchesResolutionQuery,
	optionTier,
	parseCustomResolution,
	parseResolutionValue
} from './resolutionPicker';

const FLUX_OPTIONS = [
	{ value: '1024x1024', ratio: [1, 1], description: 'Square', tier: 'Full' },
	{ value: '1920x1080', ratio: [16, 9], description: 'Widescreen', tier: 'Full' },
	{ value: '768x768', ratio: [1, 1], description: 'Square (Compact)', tier: 'Compact' },
	{ value: '1024x576', ratio: [16, 9], description: 'Widescreen (Compact)', tier: 'Compact' },
	{ value: '512x512', ratio: [1, 1], description: 'Square (Minimum)', tier: 'Minimum' }
];

describe('parseResolutionValue', () => {
	it('parses a WIDTHxHEIGHT string', () => {
		expect(parseResolutionValue('1216x832')).toEqual({ width: 1216, height: 832 });
	});

	it('returns null for a value with no x separator', () => {
		expect(parseResolutionValue('1216')).toBeNull();
	});

	it('returns null for a non-numeric value', () => {
		expect(parseResolutionValue('widthxheight')).toBeNull();
	});
});

describe('optionTier', () => {
	it("uses the option's own tier when present", () => {
		expect(optionTier({ value: '1024x1024', tier: 'Compact' })).toBe('Compact');
	});

	it('falls back to group when tier is absent', () => {
		expect(optionTier({ value: '2048x2048', group: '2K' })).toBe('2K');
	});

	it('falls back to Standard when neither tier nor group is present', () => {
		expect(optionTier({ value: '1920x1080' })).toBe('Standard');
	});
});

describe('matchesResolutionQuery', () => {
	it('matches an empty query against everything', () => {
		expect(matchesResolutionQuery(FLUX_OPTIONS[0], '')).toBe(true);
	});

	it('matches a ratio string', () => {
		expect(matchesResolutionQuery(FLUX_OPTIONS[1], '16:9')).toBe(true);
		expect(matchesResolutionQuery(FLUX_OPTIONS[0], '16:9')).toBe(false);
	});

	it('matches a px fragment against width or height', () => {
		expect(matchesResolutionQuery(FLUX_OPTIONS[2], '768')).toBe(true);
		expect(matchesResolutionQuery(FLUX_OPTIONS[0], '768')).toBe(false);
	});

	it('matches a full WxH value with an "x" separator', () => {
		expect(matchesResolutionQuery(FLUX_OPTIONS[0], '1024x1024')).toBe(true);
	});

	it('matches a full WxH value with a "×" separator', () => {
		expect(matchesResolutionQuery(FLUX_OPTIONS[0], '1024×1024')).toBe(true);
	});

	it('matches description words case-insensitively', () => {
		expect(matchesResolutionQuery(FLUX_OPTIONS[3], 'widescreen')).toBe(true);
		expect(matchesResolutionQuery(FLUX_OPTIONS[3], 'WIDESCREEN')).toBe(true);
	});

	it('matches a tier name', () => {
		expect(matchesResolutionQuery(FLUX_OPTIONS[4], 'minimum')).toBe(true);
	});

	it('does not match an unrelated query', () => {
		expect(matchesResolutionQuery(FLUX_OPTIONS[0], 'portrait')).toBe(false);
	});
});

describe('filterResolutionOptions', () => {
	it('filters down to matching rows only, preserving order', () => {
		const result = filterResolutionOptions(FLUX_OPTIONS, 'compact');
		expect(result.map((o) => o.value)).toEqual(['768x768', '1024x576']);
	});

	it('returns everything for an empty query', () => {
		expect(filterResolutionOptions(FLUX_OPTIONS, '   ')).toHaveLength(FLUX_OPTIONS.length);
	});

	it('returns an empty array when nothing matches', () => {
		expect(filterResolutionOptions(FLUX_OPTIONS, 'nonexistent')).toEqual([]);
	});
});

describe('groupOptionsByTier', () => {
	it('groups by tier, preserving first-appearance order', () => {
		const sections = groupOptionsByTier(FLUX_OPTIONS);
		expect(sections.map((s) => s.tier)).toEqual(['Full', 'Compact', 'Minimum']);
		expect(sections[0].options.map((o) => o.value)).toEqual(['1024x1024', '1920x1080']);
		expect(sections[1].options.map((o) => o.value)).toEqual(['768x768', '1024x576']);
	});

	// zImage's shared list authors tiers smallest-first (Minimum..XL) rather
	// than Flux/SDXL's largest-first - grouping must not impose its own order.
	it('preserves smallest-first authored tier order too', () => {
		const options = [
			{ value: '512x512', tier: 'Minimum' },
			{ value: '640x640', tier: 'Small' },
			{ value: '1536x1536', tier: 'XL' }
		];
		expect(groupOptionsByTier(options).map((s) => s.tier)).toEqual(['Minimum', 'Small', 'XL']);
	});

	it('falls back to group-derived tiers for options with no explicit tier', () => {
		const options = [
			{ value: '2048x2048', group: '2K' },
			{ value: '1920x1080' }
		];
		expect(groupOptionsByTier(options).map((s) => s.tier)).toEqual(['2K', 'Standard']);
	});
});

describe('parseCustomResolution', () => {
	it('accepts a well-formed value within bounds', () => {
		expect(parseCustomResolution('768', '768')).toEqual({ ok: true, value: '768x768' });
	});

	it('rejects a non-integer input', () => {
		const result = parseCustomResolution('768.5', '768');
		expect(result.ok).toBe(false);
		expect(result.error).toMatch(/whole numbers/i);
	});

	it('rejects an empty field', () => {
		expect(parseCustomResolution('', '768').ok).toBe(false);
		expect(parseCustomResolution('768', '').ok).toBe(false);
	});

	it('rejects a value below the minimum bound', () => {
		const result = parseCustomResolution('32', '768');
		expect(result.ok).toBe(false);
		expect(result.error).toMatch(/between/i);
	});

	it('rejects a value above the maximum bound', () => {
		const result = parseCustomResolution('768', '16000');
		expect(result.ok).toBe(false);
		expect(result.error).toMatch(/between/i);
	});

	it('rejects a value not a multiple of the granularity', () => {
		const result = parseCustomResolution('770', '768');
		expect(result.ok).toBe(false);
		expect(result.error).toMatch(/multiple of/i);
	});

	it('rejects a negative-looking input', () => {
		expect(parseCustomResolution('-768', '768').ok).toBe(false);
	});
});
