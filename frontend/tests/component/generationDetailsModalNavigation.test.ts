// @vitest-environment jsdom
//
// The details modal is opened from several places; only the ones that own a
// list (the history page) hand it a navigator, so the header controls and the
// generation-stepping keys have to stay absent everywhere else.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			post: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationParams: vi
			.fn()
			.mockResolvedValue({ success: true, data: { parameters: {}, models: [] } }),
		getGenerationById: vi.fn().mockResolvedValue({ success: false, error: 'not mocked' }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getBaseURL: vi.fn(() => ''),
		getToken: vi.fn(() => null),
		setOnAuthExpired: vi.fn()
	}
}));

const { default: GenerationDetailsModal } = await import(
	'$lib/components/modals/GenerationDetailsModal.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function generationWith(fileCount: number, id = 'gen-navigation') {
	return {
		id,
		form_data: {},
		status: 'completed' as const,
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		segments: [],
		rating: 0,
		is_favorite: false,
		files: Array.from({ length: fileCount }, (_, i) => ({
			file_path: `/outputs/${id}/${i}.png`,
			file_type: 'image',
			width: 512,
			height: 512
		}))
	};
}

// The modal's root node carries `use:portal`, which relocates it to
// `document.body`, so assertions have to look there rather than at `target`.
function mountModal(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationDetailsModal as never,
		target,
		props: { generation: generationWith(1) as never, isOpen: true, ...props }
	});
	const byLabel = (label: string) =>
		document.body.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
	return {
		component,
		set: (next: Record<string, unknown>) => component.$set(next as never),
		previous: () => byLabel('Previous generation'),
		next: () => byLabel('Next generation'),
		text: () => document.body.textContent ?? '',
		press: (key: string, init: KeyboardEventInit = {}) =>
			window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...init })),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

// The modal holds a busy flag across the awaited navigator, released a couple
// of microtasks later -- a single `await Promise.resolve()` lands before that.
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

let mounted: ReturnType<typeof mountModal> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('GenerationDetailsModal generation navigation', () => {
	it('renders no navigation for an opener that passes no navigator', () => {
		mounted = mountModal({});

		expect(mounted.previous()).toBeNull();
		expect(mounted.next()).toBeNull();
	});

	it('renders the controls and the across-list position when a navigator is given', () => {
		mounted = mountModal({
			onNavigate: vi.fn(),
			hasPrevious: true,
			hasNext: true,
			position: { index: 7, total: 42 }
		});

		expect(mounted.previous()).not.toBeNull();
		expect(mounted.next()).not.toBeNull();
		expect(mounted.text()).toContain('7 / 42');
	});

	it('calls the navigator with the direction of the clicked control', async () => {
		const onNavigate = vi.fn().mockResolvedValue(true);
		mounted = mountModal({ onNavigate, hasPrevious: true, hasNext: true });

		mounted.next()?.click();
		await flush();
		expect(onNavigate).toHaveBeenLastCalledWith(1);

		mounted.previous()?.click();
		await flush();
		expect(onNavigate).toHaveBeenLastCalledWith(-1);
		expect(onNavigate).toHaveBeenCalledTimes(2);
	});

	it('disables the control at each end of the list', () => {
		mounted = mountModal({ onNavigate: vi.fn(), hasPrevious: false, hasNext: true });

		expect(mounted.previous()?.disabled).toBe(true);
		expect(mounted.next()?.disabled).toBe(false);
	});

	it('does not call the navigator from a disabled end', async () => {
		const onNavigate = vi.fn();
		mounted = mountModal({ onNavigate, hasPrevious: false, hasNext: false });

		mounted.next()?.click();
		mounted.previous()?.click();
		await flush();

		expect(onNavigate).not.toHaveBeenCalled();
	});

	it('steps generations on Shift+Arrow regardless of the file on screen', async () => {
		const onNavigate = vi.fn().mockResolvedValue(true);
		mounted = mountModal({
			generation: generationWith(3) as never,
			onNavigate,
			hasPrevious: true,
			hasNext: true
		});

		mounted.press('ArrowRight', { shiftKey: true });
		await flush();
		expect(onNavigate).toHaveBeenLastCalledWith(1);

		mounted.press('ArrowLeft', { shiftKey: true });
		await flush();
		expect(onNavigate).toHaveBeenLastCalledWith(-1);
	});

	it('rolls a plain ArrowRight into the next generation only from the last file', async () => {
		const onNavigate = vi.fn().mockResolvedValue(true);
		mounted = mountModal({
			generation: generationWith(2) as never,
			onNavigate,
			hasPrevious: true,
			hasNext: true
		});

		// First file of two: the key belongs to the file strip.
		mounted.press('ArrowRight');
		await flush();
		expect(onNavigate).not.toHaveBeenCalled();

		// Now on the last file, so it crosses into the next generation.
		mounted.press('ArrowRight');
		await flush();
		expect(onNavigate).toHaveBeenLastCalledWith(1);
	});

	it('rolls a plain ArrowLeft into the previous generation from the first file', async () => {
		const onNavigate = vi.fn().mockResolvedValue(true);
		mounted = mountModal({
			generation: generationWith(2) as never,
			onNavigate,
			hasPrevious: true,
			hasNext: true
		});

		mounted.press('ArrowLeft');
		await flush();
		expect(onNavigate).toHaveBeenLastCalledWith(-1);
	});

	it('re-opens on the intended file when the generation is swapped in place', async () => {
		mounted = mountModal({
			generation: generationWith(2, 'gen-a') as never,
			onNavigate: vi.fn().mockResolvedValue(true),
			hasPrevious: true,
			hasNext: true
		});

		mounted.press('ArrowRight');
		await flush();
		expect(mounted.text()).toContain('2 / 2');

		// Forward: the new generation opens on its first file, not the index the
		// previous one was left on.
		mounted.set({
			generation: generationWith(3, 'gen-b') as never,
			initialFileIndex: 0
		});
		await flush();
		expect(mounted.text()).toContain('1 / 3');

		// Backward: a negative index resolves against the new generation's files.
		mounted.set({
			generation: generationWith(4, 'gen-c') as never,
			initialFileIndex: -1
		});
		await flush();
		expect(mounted.text()).toContain('4 / 4');
	});

	it('leaves arrow keys to a focused text field', async () => {
		const onNavigate = vi.fn();
		mounted = mountModal({ onNavigate, hasPrevious: true, hasNext: true });

		const input = document.createElement('input');
		document.body.appendChild(input);
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		await flush();

		expect(onNavigate).not.toHaveBeenCalled();
		input.remove();
	});
});
