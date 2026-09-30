import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

const queue = vi.hoisted(() => ({ value: { running: [] as any[], pending: [] as any[] } }));

vi.mock('$lib/services/admin-api', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/services/admin-api')>();
	return {
		...actual,
		getUsers: vi.fn(async () => ({ success: true, data: [{ id: 'user-1', username: 'alice' }] })),
		getBackends: vi.fn(async () => ({ success: true, data: [] })),
		getAdminGenerationQueue: vi.fn(async () => ({ success: true, data: queue.value })),
		getAdminGenerations: vi.fn(async () => ({ success: true, data: { generations: [], total: 0 } }))
	};
});

vi.mock('$lib/stores/presetsCatalog', () => ({
	loadPresets: vi.fn(async () => ({ success: true, data: [] }))
}));

const { default: GenerationsTab } = await import('../../src/routes/admin/components/GenerationsTab.svelte');
const { page } = await import('$app/stores');
const { mount, unmount, flushSync } = await import('svelte');

let target: HTMLDivElement | undefined;
let component: ReturnType<typeof mount> | null = null;

function setStatus(status: string) {
	const url = new URL('http://localhost/admin');
	if (status) url.searchParams.set('status', status);
	(page as any).set({ url, params: {}, route: { id: null }, status: 200, error: null, data: {}, form: undefined } as any);
}

beforeEach(() => {
	queue.value = { running: [], pending: [] };
});

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
	target = undefined;
});

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

async function render(status: string) {
	setStatus(status);
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(GenerationsTab, { target });
	await settle();
	return target;
}

const runningRow = {
	generation_id: 'run-1',
	user_id: 'user-1',
	tab_id: 'tab-1',
	preset_id: 'p1',
	backend_id: 'b1',
	progress: 0.5,
	started_at: 1,
	created_at: 1
};

describe('GenerationsTab running section', () => {
	it('shows the empty state and no history table when nothing is running', async () => {
		const el = await render('running');
		const text = el.textContent ?? '';
		expect(text).toContain('Nothing is running');
		expect(text).not.toContain('No generations match your filters');
		expect(el.querySelector('[data-running-generations]')).toBeNull();
		expect(text).not.toContain('Duration');
	});

	it('shows the live row without the stale empty messages', async () => {
		queue.value = { running: [runningRow], pending: [] };
		const el = await render('running');
		const text = el.textContent ?? '';
		expect(el.querySelector('[data-running-row="run-1"]')).not.toBeNull();
		expect(text).not.toContain('Nothing is running');
		expect(text).not.toContain('No generations match your filters');
		expect(text).not.toContain('No generations yet');
		const heading = el.querySelector('h1');
		expect(heading?.parentElement?.parentElement?.querySelector('span.font-mono')?.textContent?.trim()).toBe('1');
	});

	it('keeps the history table and no panel empty state in the all section', async () => {
		const el = await render('');
		const text = el.textContent ?? '';
		expect(text).not.toContain('Nothing is running');
		expect(text).toContain('No generations yet');
		expect(text).toContain('Duration');
	});
});
