// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

const apiMocks = vi.hoisted(() => ({
	getGenerationHistory: vi.fn(),
	getTags: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({
	api: new Proxy(apiMocks as Record<string, any>, {
		get: (target, key: string) =>
			key in target ? target[key] : vi.fn().mockResolvedValue({ success: false })
	})
}));

const { default: GenerationHistoryModal } = await import('$lib/components/modals/GenerationHistoryModal.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function file(id: number, type: string, name: string, isFinal = true) {
	return { id, file_path: `out/${name}`, file_type: type, is_final: isFinal, created_at: '2026-01-01T00:00:00Z' };
}

function generation(id: string, files: ReturnType<typeof file>[]) {
	return {
		id,
		preset_name: 'Video Director',
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		form_data: {},
		files,
		rating: 0,
		is_favorite: false
	};
}

const FILM = generation('film', [
	file(1, 'video', '1.mp4', false),
	file(2, 'video', '2.mp4', false),
	file(3, 'video', '3.mp4', false),
	file(4, 'video', '4.mp4'),
	file(5, 'image', 'cover.png')
]);

let components: Array<{ $destroy: () => void }> = [];

async function mountSingle(props: Record<string, unknown>) {
	apiMocks.getGenerationHistory.mockResolvedValue({
		success: true,
		data: { generations: [FILM], total: 1, page: 1, total_pages: 1 }
	});
	apiMocks.getTags.mockResolvedValue({ success: true, data: [] });
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationHistoryModal as never,
		target,
		props: { isOpen: true, onClose: () => {}, ...props }
	});
	components.push(component);
	for (let i = 0; i < 20; i++) {
		await tick();
		await new Promise((resolve) => setTimeout(resolve, 0));
	}
	return target;
}

afterEach(() => {
	for (const component of components) component.$destroy();
	components = [];
	document.body.innerHTML = '';
});

describe('single-pick history modal', () => {
	it('offers every file of a multi-output generation of the accepted kind', async () => {
		await mountSingle({ mediaType: 'video' });
		const ids = [...document.querySelectorAll('[data-testid="history-file-tile"]')].map((tile) => tile.getAttribute('data-file-id'));
		expect(ids).toEqual(['1', '2', '3', '4']);
	});

	it('leaves out files of a kind the field does not accept', async () => {
		await mountSingle({ mediaType: 'image' });
		const ids = [...document.querySelectorAll('[data-testid="history-file-tile"]')].map((tile) => tile.getAttribute('data-file-id'));
		expect(ids).toEqual(['5']);
	});

	it('hands the chosen file, not the first one, to onSelect', async () => {
		const onSelect = vi.fn();
		await mountSingle({ mediaType: 'video', onSelect });
		const button = document.querySelector('[data-file-id="4"] button') as HTMLButtonElement;
		button.click();
		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect.mock.calls[0][0].id).toBe('film');
		expect(onSelect.mock.calls[0][1].id).toBe(4);
	});
});
