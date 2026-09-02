import { describe, it, expect } from 'vitest';
import {
	statusVariant,
	stageLabel,
	formatClockTime,
	checkedAgo,
	latestPercent,
	isBringingUp,
	bringUpTitle,
	canStart,
	isNearBottom
} from './provisionedComputeView';
import type { ProvisionProgressEntry } from '$lib/services/admin-api';

describe('statusVariant', () => {
	it('maps every known status', () => {
		expect(statusVariant('provisioning')).toBe('signal');
		expect(statusVariant('starting')).toBe('signal');
		expect(statusVariant('running')).toBe('success');
		expect(statusVariant('stopped')).toBe('neutral');
		expect(statusVariant('missing')).toBe('danger');
		expect(statusVariant('unreachable')).toBe('danger');
		expect(statusVariant('failed')).toBe('danger');
		expect(statusVariant('unknown')).toBe('warning');
	});

	it('falls back to neutral for anything else', () => {
		expect(statusVariant('some_future_status')).toBe('neutral');
	});
});

describe('isBringingUp', () => {
	it('is true only while a background job drives the row', () => {
		expect(isBringingUp('provisioning')).toBe(true);
		expect(isBringingUp('starting')).toBe(true);
		for (const status of ['running', 'stopped', 'missing', 'unreachable', 'failed', 'unknown']) {
			expect(isBringingUp(status)).toBe(false);
		}
	});
});

describe('bringUpTitle', () => {
	it('titles a start as Starting and everything else as Provisioning', () => {
		expect(bringUpTitle('starting')).toBe('Starting');
		expect(bringUpTitle('provisioning')).toBe('Provisioning');
	});
});

describe('canStart', () => {
	it('mirrors the server-side startable states', () => {
		expect(canStart('stopped')).toBe(true);
		expect(canStart('unreachable')).toBe(true);
		expect(canStart('unknown')).toBe(true);
	});

	it('refuses rows that are running, being brought up, gone or failed', () => {
		for (const status of ['running', 'provisioning', 'starting', 'missing', 'failed']) {
			expect(canStart(status)).toBe(false);
		}
	});
});

describe('isNearBottom', () => {
	it('is true when scrolled exactly to the bottom', () => {
		expect(isNearBottom(200, 100, 300, 24)).toBe(true);
	});

	it('is true within the threshold of the bottom', () => {
		expect(isNearBottom(180, 100, 300, 24)).toBe(true);
	});

	it('is false past the threshold from the bottom', () => {
		expect(isNearBottom(100, 100, 300, 24)).toBe(false);
	});

	it('is true when content does not overflow the viewport', () => {
		expect(isNearBottom(0, 300, 200, 24)).toBe(true);
	});
});

describe('stageLabel', () => {
	it('labels the conventional stages', () => {
		expect(stageLabel('preparing')).toBe('Preparing');
		expect(stageLabel('creating')).toBe('Creating');
		expect(stageLabel('starting')).toBe('Starting');
		expect(stageLabel('waiting_worker')).toBe('Waiting for worker');
		expect(stageLabel('ready')).toBe('Ready');
	});

	it('humanizes an unknown snake_case stage', () => {
		expect(stageLabel('spinning_up_volume')).toBe('Spinning up volume');
	});

	it('leaves a single unknown word capitalized', () => {
		expect(stageLabel('booting')).toBe('Booting');
	});
});

describe('formatClockTime', () => {
	it('formats a local time as zero-padded 24h HH:MM:SS', () => {
		const d = new Date(2026, 8, 2, 4, 5, 6);
		expect(formatClockTime(d.toISOString())).toBe('04:05:06');
	});

	it('zero-pads single-digit hours, minutes and seconds together', () => {
		const d = new Date(2026, 8, 2, 0, 0, 0);
		expect(formatClockTime(d.toISOString())).toBe('00:00:00');
	});

	it('returns an empty string for an invalid timestamp', () => {
		expect(formatClockTime('not-a-date')).toBe('');
	});
});

describe('checkedAgo', () => {
	const now = new Date(2026, 8, 2, 12, 0, 0).getTime();

	it('is null for a null timestamp', () => {
		expect(checkedAgo(null, now)).toBeNull();
	});

	it('is null for an invalid timestamp', () => {
		expect(checkedAgo('not-a-date', now)).toBeNull();
	});

	it('reads "checked just now" under 5 seconds', () => {
		const iso = new Date(now - 3000).toISOString();
		expect(checkedAgo(iso, now)).toBe('checked just now');
	});

	it('reads seconds between 5s and a minute', () => {
		const iso = new Date(now - 12000).toISOString();
		expect(checkedAgo(iso, now)).toBe('checked 12s ago');
	});

	it('reads minutes between a minute and an hour', () => {
		const iso = new Date(now - 3 * 60 * 1000).toISOString();
		expect(checkedAgo(iso, now)).toBe('checked 3m ago');
	});

	it('reads hours at an hour or beyond', () => {
		const iso = new Date(now - 2 * 60 * 60 * 1000).toISOString();
		expect(checkedAgo(iso, now)).toBe('checked 2h ago');
	});
});

describe('latestPercent', () => {
	function entry(percent: number | null, message = 'x'): ProvisionProgressEntry {
		return { stage: 'starting', message, percent, at: '2026-09-02T00:00:00+00:00' };
	}

	it('is null for an empty timeline', () => {
		expect(latestPercent([])).toBeNull();
	});

	it('returns the last non-null percent', () => {
		expect(latestPercent([entry(10), entry(40), entry(null)])).toBe(40);
	});

	it('skips trailing null entries to find the last known percent', () => {
		expect(latestPercent([entry(20), entry(null), entry(null)])).toBe(20);
	});

	it('is null when every entry is indeterminate', () => {
		expect(latestPercent([entry(null), entry(null)])).toBeNull();
	});

	it('clamps out-of-range percent to 0-100', () => {
		expect(latestPercent([entry(150)])).toBe(100);
		expect(latestPercent([entry(-5)])).toBe(0);
	});
});
