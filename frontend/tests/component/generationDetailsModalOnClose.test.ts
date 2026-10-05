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
		getGenerationParams: vi.fn().mockResolvedValue({ success: true, data: { parameters: {}, models: [] } }),
		getGenerationById: vi.fn().mockResolvedValue({ success: false, error: 'not mocked' }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getBaseURL: vi.fn(() => ''),
		getToken: vi.fn(() => null),
		setOnAuthExpired: vi.fn()
	}
}));

const { default: GenerationDetailsModal } = await import('$lib/components/modals/GenerationDetailsModal.svelte');
const { createClassComponent } = await import('svelte/legacy');

const generation = {
	id: 'gen-close',
	form_data: {},
	status: 'completed' as const,
	progress: 1,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z',
	segments: [],
	rating: 0,
	is_favorite: false,
	files: [{ file_path: '/outputs/gen-close/0.png', file_type: 'image', width: 512, height: 512 }]
};

let destroy: (() => void) | undefined;

afterEach(() => {
	destroy?.();
	destroy = undefined;
});

function mountWith(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationDetailsModal as never,
		target,
		props: { generation: generation as never, isOpen: true, ...props }
	});
	destroy = () => {
		component.$destroy();
		target.remove();
	};
	return component;
}

describe('GenerationDetailsModal close callback', () => {
	it('calls onClose for a host that mounts it with props only', () => {
		const onClose = vi.fn();
		mountWith({ onClose });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('still emits the close event for existing callers', () => {
		const listener = vi.fn();
		const component = mountWith({});
		component.$on('close', listener);

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

		expect(listener).toHaveBeenCalledTimes(1);
	});
});
