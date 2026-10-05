// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { get } from 'svelte/store';
import UsageRows from '$lib/plans/components/UsageRows.svelte';
import MenuUsage from '$lib/plans/components/MenuUsage.svelte';
import UsageBar from '../../src/routes/admin/components/plans/UsageBar.svelte';
import PlanEditor from '../../src/routes/admin/components/plans/PlanEditor.svelte';
import type { LimitRow } from '$lib/plans/meApi';
import type { LimitKindDescriptor, Plan, PlanBody } from '../../src/lib/plans/types';
import { KINDS } from './plansFixtures';

vi.mock('$lib/plans/meApi', async (orig) => {
	const actual = await orig<typeof import('$lib/plans/meApi')>();
	return {
		...actual,
		getMyLimits: vi.fn(async () => ({
			rows: [],
			meta: { planName: null, source: 'none', groupName: null, exempt: false, timezone: null, contactLine: null },
			storageBytes: null
		})),
		getMyStorageBreakdown: vi.fn(async () => [])
	};
});

import { postUpload } from '$lib/components/form-fields/mediaLoaderUpload';
import { generateGate, limits, reportLimitRefusal, resetLimitsState } from '$lib/plans/store';

const GB = 1024 ** 3;
const MB = 1024 ** 2;
const NOW = Date.parse('2026-10-05T19:48:00Z');

const storage: LimitRow = {
	kind: 'storage_bytes',
	label: 'Storage',
	used: 18.6 * GB,
	limit: 20 * GB,
	format: 'bytes',
	resets_at: null,
	state: 'warn',
	percent: null,
	enforced: true
};
const daily: LimitRow = {
	kind: 'generations_per_day',
	label: 'Generations today',
	used: 97,
	limit: 100,
	format: 'count',
	resets_at: '2026-10-06T00:00:00Z',
	state: 'warn',
	percent: null,
	enforced: true
};
const upload: LimitRow = {
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

const UPLOAD_KIND: LimitKindDescriptor = {
	key: 'upload_file_size',
	label: 'Largest upload',
	short_label: 'Largest upload',
	description: 'The largest single file a user can upload',
	value_type: 'bytes',
	unit: 'MB',
	input_scale: MB,
	window: 'none',
	per_item: true
};

let cleanup: (() => void) | undefined;

function render(component: any, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = mount(component, { target, props });
	flushSync();
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	return target;
}

beforeEach(() => resetLimitsState());
afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	resetLimitsState();
	document.body.innerHTML = '';
});

describe('account menu storage line', () => {
	it('shows used storage with no limit and no bar when storage is unlimited', () => {
		const target = render(MenuUsage, { rows: [], storageBytes: 18.6 * GB, onOpen: () => {} });

		const line = target.querySelector('[data-menu-storage]')!;
		expect(line.textContent).toContain('Storage');
		expect(line.textContent).toContain('18.6 GB · no limit');
		expect(line.querySelector('.font-mono.tabular-nums')).not.toBeNull();
		expect(target.querySelector('[role="progressbar"]')).toBeNull();
		expect(target.querySelector('[data-menu-usage]')).toBeNull();
	});

	it('keeps storage as its own line when another limit is the closest one', () => {
		const target = render(MenuUsage, { rows: [storage, { ...daily, used: 99 }], storageBytes: 18.6 * GB, onOpen: () => {} });

		expect(target.querySelector('[data-menu-usage]')!.textContent).toContain('Generations today');
		expect(target.querySelector('[data-menu-storage]')!.textContent).toContain('18.6 / 20 GB');
	});

	it('does not repeat storage when the storage bar is the one shown', () => {
		const target = render(MenuUsage, { rows: [storage, { ...daily, used: 50, state: 'ok' }], storageBytes: 18.6 * GB, onOpen: () => {} });

		expect(target.querySelector('[data-menu-usage]')!.textContent).toContain('Storage');
		expect(target.querySelector('[data-menu-storage]')).toBeNull();
	});

	it('draws no bar for a per-file limit and still shows storage', () => {
		const target = render(MenuUsage, { rows: [upload], storageBytes: 5 * GB, onOpen: () => {} });

		expect(target.querySelector('[data-menu-usage]')).toBeNull();
		expect(target.querySelector('[data-menu-storage]')!.textContent).toContain('5 GB · no limit');
	});

	it('shows nothing when storage usage is unknown and there are no limits', () => {
		const target = render(MenuUsage, { rows: [], storageBytes: null, onOpen: () => {} });
		expect(target.querySelector('[data-menu-storage]')).toBeNull();
		expect(target.querySelector('[data-menu-usage]')).toBeNull();
	});
});

describe('Plan and usage rows', () => {
	it('always lists storage, even with no limits at all', () => {
		const target = render(UsageRows, { rows: [], now: NOW, storageBytes: 18.6 * GB });

		const row = target.querySelector('[data-limit-row="storage_bytes"]')!;
		expect(row.textContent).toContain('18.6 GB used');
		expect(row.textContent).toContain('no limit');
		expect(target.querySelector('[role="progressbar"]')).toBeNull();
		expect(target.textContent).not.toContain('No limits on your account');
	});

	it('says there are no limits only when storage usage is unknown too', () => {
		const target = render(UsageRows, { rows: [], now: NOW, storageBytes: null });
		expect(target.textContent).toContain('No limits on your account');
	});

	it('adds the unlimited storage row above other limits and hides other unlimited kinds', () => {
		const target = render(UsageRows, { rows: [daily], now: NOW, storageBytes: 2 * GB });

		const kinds = Array.from(target.querySelectorAll('[data-limit-row]')).map((li) => li.getAttribute('data-limit-row'));
		expect(kinds).toEqual(['storage_bytes', 'generations_per_day']);
	});

	it('does not duplicate storage when it has a limit', () => {
		const target = render(UsageRows, { rows: [storage], now: NOW, storageBytes: 18.6 * GB });
		expect(target.querySelectorAll('[data-limit-row="storage_bytes"]')).toHaveLength(1);
		expect(target.querySelector('[data-storage-unlimited]')).toBeNull();
	});

	it('shows a per-file limit as an info row without a bar', () => {
		const target = render(UsageRows, { rows: [upload], now: NOW, storageBytes: null });

		const row = target.querySelector('[data-limit-row="upload_file_size"]')!;
		expect(row.textContent).toContain('Largest upload');
		expect(row.textContent).toContain('50 MB');
		expect(row.textContent).toContain('per file');
		expect(row.querySelector('[role="progressbar"]')).toBeNull();
	});
});

describe('admin usage bar for a per-file kind', () => {
	it('shows the limit and no bar or percent', () => {
		const target = render(UsageBar, { kind: UPLOAD_KIND, used: 0, limit: 50 * MB, compact: true });
		expect(target.textContent).toContain('50 MB');
		expect(target.querySelector('.rounded.bg-surface-3')).toBeNull();
		expect(target.textContent).not.toMatch(/%|full/);
	});

	it('shows a dash when the user has no such limit', () => {
		const target = render(UsageBar, { kind: UPLOAD_KIND, used: 0, limit: null, compact: true });
		expect(target.textContent).toContain('—');
	});
});

describe('plan editor with the upload size kind', () => {
	const plan: Plan = { id: 'p', name: 'Free', description: '', is_system: false, limits: [{ kind: 'upload_file_size', value: 52428800 }] };
	const kinds = [...KINDS, UPLOAD_KIND];

	function renderEditor(value: Plan | null, onSave: (body: PlanBody) => void = () => {}) {
		return render(PlanEditor, { plan: value, kinds, onSave, onBack: () => {} });
	}

	async function click(el: Element | null) {
		(el as HTMLElement).click();
		await tick();
		flushSync();
	}

	it('offers it in the picker', async () => {
		const root = renderEditor(null);
		await click(root.querySelector('[data-add-limit] button'));
		expect(root.querySelector('[data-kind-option="upload_file_size"]')).not.toBeNull();
	});

	it('shows a stored 50 MB as 50 with an MB unit and a stored 1 GB as 1 GB', () => {
		const root = renderEditor(plan);
		expect((root.querySelector('[data-limit-input="upload_file_size"]') as HTMLInputElement).value).toBe('50');
		expect(root.querySelector('[data-limit-unit="upload_file_size"]')!.textContent).toContain('MB');
		cleanup?.();
		const gig = renderEditor({ ...plan, limits: [{ kind: 'upload_file_size', value: GB }] });
		expect((gig.querySelector('[data-limit-input="upload_file_size"]') as HTMLInputElement).value).toBe('1');
		expect(gig.querySelector('[data-limit-unit="upload_file_size"]')!.textContent).toContain('GB');
	});

	it('saves the stored value unchanged', async () => {
		const onSave = vi.fn();
		const root = renderEditor(plan, onSave);
		const input = root.querySelector('[data-limit-input="upload_file_size"]') as HTMLInputElement;
		input.value = '60';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		flushSync();
		const save = Array.from(root.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Save') as HTMLButtonElement;
		await click(save);
		expect(onSave).toHaveBeenCalledWith({ name: 'Free', description: '', limits: [{ kind: 'upload_file_size', value: 60 * MB }] });
	});
});

describe('upload refusals', () => {
	class FakeXhr {
		static sent = 0;
		status = 403;
		responseText = JSON.stringify({
			detail: {
				error: 'limit_exceeded',
				kind: 'upload_file_size',
				code: 'upload_file_size_exceeded',
				point: 'upload',
				used: 125829120,
				limit: 52428800,
				incoming: 125829120,
				message: 'This file is 120 MB; your plan allows files up to 50 MB.'
			}
		});
		withCredentials = false;
		upload: Record<string, unknown> = {};
		onload: (() => void) | null = null;
		onerror: (() => void) | null = null;
		open() {}
		setRequestHeader() {}
		send() {
			FakeXhr.sent += 1;
			queueMicrotask(() => this.onload?.());
		}
	}

	function bigFile(size: number): File {
		const file = new File(['x'], 'big.png');
		Object.defineProperty(file, 'size', { value: size });
		return file;
	}

	beforeEach(() => {
		FakeXhr.sent = 0;
		vi.stubGlobal('XMLHttpRequest', FakeXhr);
	});
	afterEach(() => vi.unstubAllGlobals());

	it('refuses an oversize file locally without sending it', async () => {
		limits.set([upload]);
		await expect(postUpload(bigFile(120 * MB), null, () => {})).rejects.toThrow(
			'This file is 120 MB; your plan allows files up to 50 MB.'
		);
		expect(FakeXhr.sent).toBe(0);
	});

	it('still asks the server when the file is within the cached limit and shows the server message', async () => {
		limits.set([upload]);
		await expect(postUpload(bigFile(MB), null, () => {})).rejects.toThrow(
			'This file is 120 MB; your plan allows files up to 50 MB.'
		);
		expect(FakeXhr.sent).toBe(1);
	});

	it('sends the file when the limits are not known yet', async () => {
		await expect(postUpload(bigFile(999 * MB), null, () => {})).rejects.toThrow('120 MB');
		expect(FakeXhr.sent).toBe(1);
	});

	it('does not turn a per-file refusal into a generate gate', () => {
		reportLimitRefusal({
			error: 'limit_exceeded',
			kind: 'upload_file_size',
			code: 'upload_file_size_exceeded',
			format: 'bytes'
		});
		expect(get(generateGate)).toBeNull();
	});
});
