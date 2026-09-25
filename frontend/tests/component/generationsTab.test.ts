import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/admin-api', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/services/admin-api')>();
	return {
		...actual,
		getUsers: vi.fn(async () => ({ success: true, data: [{ id: 'user-1', username: 'alice' }] })),
		getAdminGenerations: vi.fn(async () => ({
			success: true,
			data: {
				generations: [
					{
						id: 'gen-1',
						form_data: {},
						status: 'completed',
						progress: 1,
						created_at: '2026-08-14T00:00:00Z',
						completed_at: '2026-08-14T00:00:05Z',
						updated_at: '2026-08-14T00:00:05Z',
						files: [],
						rating: 0,
						is_favorite: false,
						user_id: 'user-1',
						has_run_report: true,
						preset_name: 'SDXL',
						mode: 't2i'
					}
				],
				total: 1
			}
		}))
	};
});

const { default: GenerationsTab } = await import('../../src/routes/admin/components/GenerationsTab.svelte');
const { mount, unmount, flushSync } = await import('svelte');

let target: HTMLDivElement | undefined;
let component: ReturnType<typeof mount> | null = null;

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

describe('GenerationsTab', () => {
	it('mounts onto LibraryShell and renders the generations table with its columns', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		component = mount(GenerationsTab, { target });
		await settle();

		const text = target.textContent ?? '';
		expect(text).toContain('Generations');
		expect(text).toContain('Status');
		expect(text).toContain('Preset');
		expect(text).toContain('SDXL');
		expect(text).toContain('alice');
		expect(text).toContain('t2i');
	});
});
