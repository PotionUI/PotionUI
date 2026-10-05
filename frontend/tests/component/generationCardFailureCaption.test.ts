import { describe, it, expect, vi, afterEach } from 'vitest';
vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationThumbnailURL: vi.fn(() => '/thumb.png')
	}
}));

const { default: GenerationCard } = await import('$lib/components/GenerationCard.svelte');
const { createClassComponent } = await import('svelte/legacy');

const CAPTION = 'Give this to your admin';

function failed(over: Record<string, unknown>) {
	return {
		id: 'gen-1',
		form_data: {},
		status: 'failed' as const,
		progress: 0,
		created_at: '2026-10-05T10:00:00Z',
		updated_at: '2026-10-05T10:00:00Z',
		files: [],
		rating: 0,
		is_favorite: false,
		error_id: '01M3QERRORID',
		error_contact_admin: false as boolean,
		...over
	};
}

const ordinary = {
	error_code: 'cuda_oom',
	error_message: "This was too much for the server's GPU right now.",
	error_user_message: "This was too much for the server's GPU right now.\n\n- Try a lower resolution\n- Try again in a moment"
};

const checkUnavailable = {
	error_code: 'content_check_unavailable',
	error_message: 'Content check unavailable.',
	error_user_message: 'Content check unavailable.\n\n- Send the error ID to your administrator'
};

function mountAs(viewer: 'user' | 'admin', generation: ReturnType<typeof failed>) {
	generation = { ...generation, error_contact_admin: viewer === 'user' };
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationCard as never,
		target,
		props: { generation: generation as never }
	});
	return {
		text: () => target.textContent ?? '',
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountAs> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('GenerationCard failure caption', () => {
	it('tells a regular user to hand the error ID to their admin', () => {
		mounted = mountAs('user', failed(ordinary));
		expect(mounted.text()).toContain('01M3QERRORID');
		expect(mounted.text()).toContain(CAPTION);
		expect(mounted.text()).toContain('Try a lower resolution');
	});

	it('keeps the error ID for an admin but drops the caption', () => {
		mounted = mountAs('admin', failed(ordinary));
		expect(mounted.text()).toContain('01M3QERRORID');
		expect(mounted.text()).not.toContain(CAPTION);
	});

	it('shows the caption on a content check notice only for a regular user', () => {
		mounted = mountAs('user', failed(checkUnavailable));
		expect(mounted.text()).toContain('01M3QERRORID');
		expect(mounted.text()).toContain(CAPTION);
		mounted.destroy();

		mounted = mountAs('admin', failed(checkUnavailable));
		expect(mounted.text()).toContain('01M3QERRORID');
		expect(mounted.text()).not.toContain(CAPTION);
	});
});
