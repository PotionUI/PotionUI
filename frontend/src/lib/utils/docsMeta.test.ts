import { describe, it, expect } from 'vitest';
import {
	arxivUrl,
	groupTechniqueRefs,
	isModelMeta,
	isTechniqueMeta,
	showsStatusDot,
	statusBadgeVariant,
	statusLabel
} from './docsMeta';
import type { DocTechniqueRef, ModelMeta, TechniqueMeta } from '$lib/types/api';

describe('statusBadgeVariant', () => {
	it('maps known statuses', () => {
		expect(statusBadgeVariant('stable')).toBe('success');
		expect(statusBadgeVariant('experimental')).toBe('warning');
		expect(statusBadgeVariant('needs-gpu-validation')).toBe('info');
	});

	it('falls back to neutral for missing/unknown status', () => {
		expect(statusBadgeVariant(null)).toBe('neutral');
		expect(statusBadgeVariant(undefined)).toBe('neutral');
	});
});

describe('statusLabel', () => {
	it('renders a human label per status', () => {
		expect(statusLabel('stable')).toBe('Stable');
		expect(statusLabel('experimental')).toBe('Experimental');
		expect(statusLabel('needs-gpu-validation')).toBe('Needs GPU validation');
		expect(statusLabel(null)).toBe('Unknown');
	});
});

describe('showsStatusDot', () => {
	it('flags experimental and needs-gpu-validation only', () => {
		expect(showsStatusDot('experimental')).toBe(true);
		expect(showsStatusDot('needs-gpu-validation')).toBe(true);
		expect(showsStatusDot('stable')).toBe(false);
		expect(showsStatusDot(null)).toBe(false);
	});
});

describe('arxivUrl', () => {
	it('builds the abstract page URL from a bare id', () => {
		expect(arxivUrl('2410.02416')).toBe('https://arxiv.org/abs/2410.02416');
	});

	it('strips an "arXiv:" prefix case-insensitively', () => {
		expect(arxivUrl('arXiv:2410.02416')).toBe('https://arxiv.org/abs/2410.02416');
		expect(arxivUrl('arxiv:2410.02416')).toBe('https://arxiv.org/abs/2410.02416');
	});
});

const techniqueMeta: TechniqueMeta = {
	title: 'APG',
	category_group: 'Quality',
	status: 'stable',
	families: ['wan22'],
	authors: ['tier1d'],
	knobs: []
};

const modelMeta: ModelMeta = {
	title: 'Wan 2.2',
	family_key: 'wan22',
	modes: ['txt2vid'],
	spec: { arch: 'DiT', latent: '5D causal-3D', vae: 'causal-3D', te: 'umt5', guidance: 'true-cfg', engine: 'native' }
};

describe('isTechniqueMeta / isModelMeta', () => {
	it('discriminates technique meta', () => {
		expect(isTechniqueMeta(techniqueMeta)).toBe(true);
		expect(isTechniqueMeta(modelMeta)).toBe(false);
		expect(isTechniqueMeta(null)).toBe(false);
		expect(isTechniqueMeta(undefined)).toBe(false);
	});

	it('discriminates model meta', () => {
		expect(isModelMeta(modelMeta)).toBe(true);
		expect(isModelMeta(techniqueMeta)).toBe(false);
		expect(isModelMeta(null)).toBe(false);
	});
});

describe('groupTechniqueRefs', () => {
	const ref = (category_group: string, slug: string): DocTechniqueRef => ({
		slug,
		title: slug,
		category_group,
		status: 'stable',
		doc_id: `techniques/${slug}`
	});

	it('splits Performance/Memory/Sampling into optimizations, Quality into quality', () => {
		const grouped = groupTechniqueRefs([
			ref('Performance', 'fbcache'),
			ref('Memory', 'partial-residency'),
			ref('Sampling', 'euler-restart'),
			ref('Quality', 'apg'),
			ref('Quality', 'nag')
		]);
		expect(grouped.optimizations.map((t) => t.slug)).toEqual([
			'fbcache',
			'partial-residency',
			'euler-restart'
		]);
		expect(grouped.quality.map((t) => t.slug)).toEqual(['apg', 'nag']);
	});

	it('omits unrecognized category_group values from both sections', () => {
		const grouped = groupTechniqueRefs([ref('Experimental', 'mystery')]);
		expect(grouped.optimizations).toEqual([]);
		expect(grouped.quality).toEqual([]);
	});

	it('returns empty groups for undefined input', () => {
		expect(groupTechniqueRefs(undefined)).toEqual({ optimizations: [], quality: [] });
	});
});
