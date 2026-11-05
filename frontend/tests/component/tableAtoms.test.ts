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
	const navButton = (label: string) =>
		target.querySelector(`nav[aria-label="Pagination"] button[aria-label="${label}"]`) as HTMLButtonElement;

	it('renders the standard numeric pagination with the current page marked', () => {
		mountComponent(TablePager, {
			page: 1,
			pageCount: 3,
			pageSize: 25,
			onPageChange: vi.fn(),
			onPageSizeChange: vi.fn()
		});
		expect(target.textContent).not.toContain('Page 1 of 3');
		expect(navButton('Page 1').getAttribute('aria-current')).toBe('page');
		expect(navButton('Page 3')).not.toBeNull();
		expect(navButton('Previous page').disabled).toBe(true);
		expect(navButton('Next page').disabled).toBe(false);
	});

	it('calls onPageChange with the adjacent or chosen page', () => {
		const onPageChange = vi.fn();
		mountComponent(TablePager, {
			page: 2,
			pageCount: 3,
			pageSize: 25,
			onPageChange,
			onPageSizeChange: vi.fn()
		});
		navButton('Next page').click();
		expect(onPageChange).toHaveBeenCalledWith(3);
		navButton('Previous page').click();
		expect(onPageChange).toHaveBeenCalledWith(1);
		navButton('Page 3').click();
		expect(onPageChange).toHaveBeenLastCalledWith(3);
	});

	it('calls onPageSizeChange when the per-page select changes', () => {
		const onPageSizeChange = vi.fn();
		mountComponent(TablePager, {
			page: 1,
			pageCount: 2,
			pageSize: 25,
			pageSizeOptions: [10, 25, 50],
			onPageChange: vi.fn(),
			onPageSizeChange
		});
		expect(target.textContent).toContain('Per page');
		const select = target.querySelector('select') as HTMLSelectElement;
		select.value = '50';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		expect(onPageSizeChange).toHaveBeenCalledWith(50);
	});

	it('renders nothing when there is a single page', () => {
		mountComponent(TablePager, {
			page: 1,
			pageCount: 1,
			pageSize: 25,
			onPageChange: vi.fn(),
			onPageSizeChange: vi.fn()
		});
		expect(target.querySelector('nav')).toBeNull();
		expect(target.querySelector('select')).toBeNull();
	});
});
