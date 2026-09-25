import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: StatusCell } = await import('../../src/lib/components/table/StatusCell.svelte');
const { default: TablePager } = await import('../../src/lib/components/table/TablePager.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountComponent(Component: any, props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(Component, { target, props });
	flushSync();
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('StatusCell', () => {
	it('renders the label and a tone-colored dot', () => {
		mountComponent(StatusCell, { tone: 'danger', label: 'failed' });
		expect(target.textContent).toContain('failed');
		expect(target.querySelector('.text-danger')).not.toBeNull();
		expect(target.querySelector('.bg-danger')).not.toBeNull();
	});

	it('never uses fg-disabled for the muted tone (reserved for disabled controls, not status)', () => {
		mountComponent(StatusCell, { tone: 'muted', label: 'idle' });
		expect(target.innerHTML).not.toContain('fg-disabled');
	});
});

describe('TablePager', () => {
	it('shows the page label and disables prev/next at the bounds', () => {
		mountComponent(TablePager, {
			page: 1,
			pageCount: 3,
			pageSize: 25,
			onPageChange: vi.fn(),
			onPageSizeChange: vi.fn()
		});
		expect(target.textContent).toContain('Page 1 of 3');
		expect((target.querySelector('button[aria-label="Previous page"]') as HTMLButtonElement).disabled).toBe(true);
		expect((target.querySelector('button[aria-label="Next page"]') as HTMLButtonElement).disabled).toBe(false);
	});

	it('calls onPageChange with the adjacent page on prev/next', () => {
		const onPageChange = vi.fn();
		mountComponent(TablePager, {
			page: 2,
			pageCount: 3,
			pageSize: 25,
			onPageChange,
			onPageSizeChange: vi.fn()
		});
		(target.querySelector('button[aria-label="Next page"]') as HTMLButtonElement).click();
		expect(onPageChange).toHaveBeenCalledWith(3);
		(target.querySelector('button[aria-label="Previous page"]') as HTMLButtonElement).click();
		expect(onPageChange).toHaveBeenCalledWith(1);
	});

	it('calls onPageSizeChange when the rows-per-page select changes', () => {
		const onPageSizeChange = vi.fn();
		mountComponent(TablePager, {
			page: 1,
			pageCount: 1,
			pageSize: 25,
			pageSizeOptions: [10, 25, 50],
			onPageChange: vi.fn(),
			onPageSizeChange
		});
		const select = target.querySelector('select') as HTMLSelectElement;
		select.value = '50';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		expect(onPageSizeChange).toHaveBeenCalledWith(50);
	});
});
