import { describe, it, expect } from 'vitest';
import type { LimitRow } from './meApi';
import {
	blocksSubmit,
	gateFromRefusal,
	isPerItemError,
	parseLimitRefusal,
	refusalFromError,
	refusalMessage,
	uploadSizeMessage
} from './refusal';

const NOW = Date.parse('2026-10-05T19:48:00Z');
const daily = {
	error: 'limit_exceeded' as const,
	kind: 'generations_per_day',
	label: 'Generations today',
	format: 'count' as const,
	resets_at: '2026-10-06T00:00:00Z'
};

describe('refusal parsing', () => {
	it('reads a plain body and a detail-wrapped body', () => {
		expect(parseLimitRefusal(daily)?.kind).toBe('generations_per_day');
		expect(parseLimitRefusal({ detail: daily })?.kind).toBe('generations_per_day');
	});

	it('ignores other errors', () => {
		expect(parseLimitRefusal({ error: 'form_validation_failed' })).toBeNull();
		expect(parseLimitRefusal(null)).toBeNull();
	});

	it('reads the refusal off an axios-shaped error', () => {
		expect(refusalFromError({ response: { status: 403, data: daily } })?.kind).toBe('generations_per_day');
	});
});

describe('gate wording', () => {
	it('words the daily limit with a countdown and the admin line', () => {
		const gate = gateFromRefusal(daily, NOW)!;
		expect(gate.title).toBe('Daily limit reached');
		expect(gate.reason).toBe('Daily limit reached, resets in 4 h 12 min. Ask your admin for more.');
		expect(gate.canFreeUp).toBe(false);
	});

	it('uses the admin contact line when the server sends one', () => {
		const gate = gateFromRefusal({ ...daily, contact_line: 'Mail ops@example.com.' }, NOW)!;
		expect(gate.reason).toContain('Mail ops@example.com.');
	});

	it('offers freeing space for a full storage limit', () => {
		const gate = gateFromRefusal(
			{ error: 'limit_exceeded', kind: 'storage_bytes', label: 'Storage', format: 'bytes' },
			NOW
		)!;
		expect(gate.title).toBe('Storage is full');
		expect(gate.canFreeUp).toBe(true);
		expect(gate.resetsAt).toBeNull();
	});

	it('has no gate once the reset time passed', () => {
		expect(gateFromRefusal(daily, Date.parse('2026-10-06T00:00:01Z'))).toBeNull();
	});

	it('gives upload text that points to History or Library for storage', () => {
		const error = {
			response: { data: { error: 'limit_exceeded', kind: 'storage_bytes', label: 'Storage', format: 'bytes' } }
		};
		expect(refusalMessage(error)).toBe('Storage is full. Free up space in History or Library, then try again.');
		expect(refusalMessage(new Error('x'))).toBeNull();
	});
});

describe('blocksSubmit', () => {
	const base: LimitRow = { kind: 'k', label: 'K', used: 1, limit: 1, format: 'count', resets_at: null, state: 'full', percent: null, enforced: true };

	it('blocks on a full row', () => {
		expect(blocksSubmit(base)).toBe(true);
		expect(blocksSubmit({ ...base, state: 'warn' })).toBe(false);
	});

	it('follows enforce_at when given', () => {
		expect(blocksSubmit({ ...base, enforce_at: ['upload'] })).toBe(false);
		expect(blocksSubmit({ ...base, enforce_at: ['submit'] })).toBe(true);
	});

	it('never pre-blocks a hidden-money row, which only refuses cloud submits', () => {
		expect(blocksSubmit({ ...base, format: 'percent', enforce_at: ['submit'] })).toBe(false);
	});

	it('does not block an exempt account', () => {
		expect(blocksSubmit({ ...base, enforced: false })).toBe(false);
	});
});

describe('upload file size refusal', () => {
	const detail = {
		error: 'limit_exceeded',
		kind: 'upload_file_size',
		code: 'upload_file_size_exceeded',
		point: 'upload',
		used: 125829120,
		limit: 52428800,
		incoming: 125829120,
		message: 'This file is 120 MB; your plan allows files up to 50 MB. Ask your admin for more.'
	};
	const error = { response: { status: 403, data: { detail } } };

	it('surfaces the server message instead of a gate title', () => {
		expect(refusalMessage(error)).toBe(detail.message);
		expect(isPerItemError(error)).toBe(true);
		expect(isPerItemError({ response: { data: daily } })).toBe(false);
	});

	it('builds the same wording itself when the message is missing', () => {
		const bare = { response: { data: { detail: { ...detail, message: undefined } } } };
		expect(refusalMessage(bare)).toBe('This file is 120 MB; your plan allows files up to 50 MB.');
	});

	it('appends the contact line only when there is one', () => {
		expect(uploadSizeMessage(125829120, 52428800, '')).toBe('This file is 120 MB; your plan allows files up to 50 MB.');
		expect(uploadSizeMessage(125829120, 52428800, 'Ask your admin.')).toBe(
			'This file is 120 MB; your plan allows files up to 50 MB. Ask your admin.'
		);
	});
});
