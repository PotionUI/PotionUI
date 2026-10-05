import { afterEach, describe, expect, it } from 'vitest';
import type { LimitRow } from './meApi';
import { limits, limitsMeta, resetLimitsState } from './store';
import { blockedUploadMessage, uploadSizeBlock } from './uploadLimit';

const MB = 1024 ** 2;
const row: LimitRow = {
	kind: 'upload_file_size',
	label: 'Largest upload',
	used: 0,
	limit: 50 * MB,
	format: 'bytes',
	resets_at: null,
	state: 'ok',
	percent: null,
	enforced: true,
	perItem: true
};

describe('upload size pre-check', () => {
	it('refuses a file over the limit with the server wording', () => {
		expect(uploadSizeBlock(120 * MB, [row])).toBe('This file is 120 MB; your plan allows files up to 50 MB.');
	});

	it('lets a file under or exactly at the limit through', () => {
		expect(uploadSizeBlock(50 * MB - 1, [row])).toBeNull();
		expect(uploadSizeBlock(50 * MB, [row])).toBeNull();
		expect(uploadSizeBlock(50 * MB + 1, [row])).not.toBeNull();
	});

	it('does nothing for an exempt user or without a limit', () => {
		expect(uploadSizeBlock(120 * MB, [{ ...row, enforced: false }])).toBeNull();
		expect(uploadSizeBlock(120 * MB, [])).toBeNull();
	});

	it('appends the contact line', () => {
		expect(uploadSizeBlock(120 * MB, [row], 'Ask your admin for more.')).toBe(
			'This file is 120 MB; your plan allows files up to 50 MB. Ask your admin for more.'
		);
	});
});

describe('blockedUploadMessage', () => {
	afterEach(() => resetLimitsState());

	it('reads the cached limits and reports the first oversize file', () => {
		limits.set([row]);
		limitsMeta.set({ planName: null, source: 'none', groupName: null, exempt: false, timezone: null, contactLine: null });
		expect(blockedUploadMessage([{ size: MB }, { size: 60 * MB }])).toBe(
			'This file is 60 MB; your plan allows files up to 50 MB.'
		);
		expect(blockedUploadMessage([{ size: MB }])).toBeNull();
	});

	it('is quiet before the limits are known', () => {
		expect(blockedUploadMessage([{ size: 999 * MB }])).toBeNull();
	});
});
