import { describe, it, expect } from 'vitest';
import { normalizeLimits, normalizeMeta, normalizeStorageBytes } from './meApi';

const envelope = {
	success: true,
	data: {
		plan: { id: 'p', name: 'Tier 1' },
		source: 'group',
		group: { id: 'g', name: 'premium-tier-1' },
		exempt: false,
		timezone: 'UTC',
		contact_line: 'Ask your admin for more.',
		limits: [
			{
				kind: 'storage_bytes',
				label: 'Storage',
				format: 'bytes',
				used: 100,
				limit: 200,
				percent: 50,
				state: 'ok',
				resets_at: null,
				enforced: true,
				kind_info: { short_label: 'Storage space', enforce_at: ['submit', 'upload'] }
			},
			{
				kind: 'cloud_spend_usd_month',
				label: 'Cloud spend',
				format: 'percent',
				used: null,
				limit: null,
				percent: 72,
				state: 'ok',
				resets_at: '2026-11-01T00:00:00+00:00',
				enforced: false
			}
		]
	}
};

describe('normalizeLimits', () => {
	it('reads rows from the envelope with the short label and enforcement points', () => {
		const rows = normalizeLimits(envelope);
		expect(rows).toHaveLength(2);
		expect(rows[0].label).toBe('Storage space');
		expect(rows[0].enforce_at).toEqual(['submit', 'upload']);
	});

	it('keeps null numbers off the row and carries the percent', () => {
		const cloud = normalizeLimits(envelope)[1];
		expect(cloud.used).toBe(0);
		expect(cloud.percent).toBe(72);
		expect(cloud.enforced).toBe(false);
	});

	it('returns nothing for an empty or odd payload', () => {
		expect(normalizeLimits(null)).toEqual([]);
		expect(normalizeLimits({ data: { limits: [] } })).toEqual([]);
	});
});

describe('normalizeMeta', () => {
	it('reads the plan header', () => {
		const meta = normalizeMeta(envelope);
		expect(meta.planName).toBe('Tier 1');
		expect(meta.groupName).toBe('premium-tier-1');
		expect(meta.contactLine).toBe('Ask your admin for more.');
	});
});

describe('per-item rows and storage usage', () => {
	const payload = {
		data: {
			usage: { storage_bytes: 1234 },
			limits: [
				{
					kind: 'upload_file_size',
					used: null,
					limit: 52428800,
					percent: null,
					state: 'ok',
					format: 'bytes',
					enforced: true,
					kind_info: { short_label: 'Largest upload', per_item: true }
				}
			]
		}
	};

	it('marks per-item rows and keeps their limit', () => {
		const row = normalizeLimits(payload)[0];
		expect(row.perItem).toBe(true);
		expect(row.limit).toBe(52428800);
		expect(row.label).toBe('Largest upload');
	});

	it('reads storage usage and treats a missing usage as unknown', () => {
		expect(normalizeStorageBytes(payload)).toBe(1234);
		expect(normalizeStorageBytes({ data: { limits: [] } })).toBeNull();
		expect(normalizeStorageBytes(null)).toBeNull();
	});
});
