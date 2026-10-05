// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getOrganizeProvenance: vi.fn(() => Promise.resolve({ success: true, data: [] })),
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
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}


const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

let mounted: ReturnType<typeof mountModal> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

function section(rows: Array<Record<string, unknown>>, actions: Array<Record<string, unknown>> = []) {
	return [{ id: 'send', title: 'Send to', rows, actions }] as never;
}

describe('GenerationDetailsModal extra sections', () => {
	it('renders a plain-data section with its checkbox rows', () => {
		mounted = mountModal({
			extraSections: section([
				{ id: 'a', label: 'Upscale', checked: true, onToggle: vi.fn() },
				{ id: 'b', label: 'Video', checked: false, disabled: true, onToggle: vi.fn() }
			])
		});
		const box = document.body.querySelector('[data-extra-section="send"]')!;
		expect(box.querySelector('h3')?.textContent).toBe('Send to');
		const inputs = box.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
		expect(Array.from(inputs).map((i) => [i.getAttribute('aria-label'), i.checked, i.disabled])).toEqual([
			['Upscale', true, false],
			['Video', false, true]
		]);
	});

	it('reports a toggled row with its new state', () => {
		const onToggle = vi.fn();
		mounted = mountModal({ extraSections: section([{ id: 'a', label: 'Upscale', checked: false, onToggle }]) });
		const input = document.body.querySelector<HTMLInputElement>('[data-extra-section="send"] input')!;
		input.click();
		expect(onToggle).toHaveBeenCalledWith(true);
	});

	it('runs an action button', () => {
		const onClick = vi.fn();
		mounted = mountModal({ extraSections: section([], [{ id: 'go', label: 'Do it', onClick }]) });
		const button = Array.from(document.body.querySelectorAll('[data-extra-section="send"] button')).find((b) =>
			b.textContent?.includes('Do it')
		) as HTMLButtonElement;
		button.click();
		expect(onClick).toHaveBeenCalledTimes(1);
	});

	it('renders nothing extra by default', () => {
		mounted = mountModal({});
		expect(document.body.querySelector('[data-extra-section]')).toBeNull();
	});

	it('updates rows when the sections are replaced', async () => {
		mounted = mountModal({ extraSections: section([{ id: 'a', label: 'Upscale', checked: false, onToggle: vi.fn() }]) });
		mounted.set({ extraSections: section([{ id: 'a', label: 'Upscale', checked: true, onToggle: vi.fn() }]) });
		await flush();
		expect(document.body.querySelector<HTMLInputElement>('[data-extra-section="send"] input')!.checked).toBe(true);
	});
});

describe('GenerationDetailsModal file change report', () => {
	it('reports the file on screen when the modal opens', async () => {
		const onFileChange = vi.fn();
		mounted = mountModal({ generation: generationWith(2, 'gen-x') as never, onFileChange });
		await flush();
		expect(onFileChange).toHaveBeenCalledWith({ generationId: 'gen-x', fileIndex: 0, fileId: null });
	});

	it('reports again when the generation is swapped in place', async () => {
		const onFileChange = vi.fn();
		mounted = mountModal({ generation: generationWith(1, 'gen-a') as never, onFileChange });
		mounted.set({ generation: generationWith(3, 'gen-b') as never, initialFileIndex: 2 });
		await flush();
		expect(onFileChange).toHaveBeenLastCalledWith({ generationId: 'gen-b', fileIndex: 2, fileId: null });
	});
});
