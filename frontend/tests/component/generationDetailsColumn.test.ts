import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const response: { data: Record<string, unknown> } = { data: { parameters: {}, models: [] } };

vi.mock('$lib/services/api/index', () => ({
	api: {
		getOrganizeProvenance: vi.fn(() => Promise.resolve({ success: true, data: [] })),
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			post: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationParams: vi.fn(() => Promise.resolve({ success: true, data: response.data })),
		getGenerationById: vi.fn().mockResolvedValue({ success: false, error: 'not mocked' }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getBaseURL: vi.fn(() => ''),
		getToken: vi.fn(() => null),
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn()
	}
}));

const { default: GenerationDetailsModal } = await import(
	'$lib/components/modals/GenerationDetailsModal.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { forgetDetailsPrefs, DETAILS_PREFS_KEY } = await import('$lib/components/generation/detailsPrefs');
const { historyStore } = await import('$lib/stores/history');
const { authStore } = await import('$lib/stores/auth');

function signInAs(accountType: string) {
	vi.spyOn(authStore, 'subscribe').mockImplementation(((run: (value: unknown) => void) => {
		run({ user: { account_type: accountType }, isAuthenticated: true });
		return () => {};
	}) as never);
}


const flush = async () => {
	for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

function tags(n: number) {
	return Array.from({ length: n }, (_, i) => ({ tag: `tag_${i}`, confidence: 0.9 }));
}

function generation(over: Record<string, unknown> = {}, systemTags: unknown[] = []) {
	return {
		id: `gen-${Math.random()}`,
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: '2026-11-12T20:40:00Z',
		completed_at: '2026-11-12T20:49:00Z',
		updated_at: '2026-11-12T20:49:00Z',
		run_seconds: 102,
		preset_id: 'portrait',
		preset_version: '1.4',
		segments: [],
		rating: 0,
		is_favorite: false,
		files: [
			{ id: 1, file_path: 'a.png', file_type: 'image', is_final: true, created_at: '2026-11-12T20:49:00Z', system_tags: systemTags }
		],
		...over
	};
}

function segments(n: number) {
	return Array.from({ length: n }, (_, i) => ({
		prompt_index: 0,
		segment_index: i,
		channel: i === 0 ? 'negative' : 'positive',
		text: `segment text ${i}`
	}));
}

function director(shots: number) {
	return {
		video_director: {
			mode: 'x',
			segments: Array.from({ length: shots }, (_, i) => ({ id: `s${i}`, prompt: `shot text ${i}`, sub_type: 'follow' })),
			media: [{ role: 'keyframe' }],
			audio: []
		}
	};
}

function models(n: number) {
	return Array.from({ length: n }, (_, i) => ({ name: `Model ${i}`, filename: `m${i}.safetensors`, model_type: 'lora' }));
}

let mountedModal: { $destroy: () => void } | undefined;
let target: HTMLElement | undefined;

async function mountModal(gen: unknown, props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	mountedModal = createClassComponent({
		component: GenerationDetailsModal as never,
		target,
		props: { generation: gen as never, isOpen: true, ...props }
	});
	await flush();
}

const sectionIds = () =>
	[...document.body.querySelectorAll('[data-details-section]')].map((el) => el.getAttribute('data-details-section'));
const section = (id: string) => document.body.querySelector(`[data-details-section="${id}"]`) as HTMLElement | null;
const header = (id: string) => section(id)?.querySelector('button[aria-expanded]') as HTMLButtonElement | null;
const showAll = (id: string) =>
	[...(section(id)?.querySelectorAll('button') ?? [])].find((b) => /^Show (all|less)/.test(b.textContent?.trim() ?? ''));
const click = async (el: Element | null | undefined) => {
	(el as HTMLElement).click();
	await flush();
};

beforeEach(() => {
	localStorage.clear();
	forgetDetailsPrefs();
	response.data = { parameters: {}, models: [] };
});

afterEach(() => {
	vi.restoreAllMocks();
	mountedModal?.$destroy();
	target?.remove();
	mountedModal = undefined;
	forgetDetailsPrefs();
});

describe('details column order', () => {
	it('starts the column with Parameters, then Models for a minimal generation', async () => {
		await mountModal(generation());
		expect(sectionIds()).toEqual(['parameters', 'models']);
	});

	it('orders an image with auto tags: Parameters, Models, Auto tags', async () => {
		response.data = { parameters: { steps: 20 }, models: models(2) };
		await mountModal(generation({}, tags(3)));
		expect(sectionIds()).toEqual(['parameters', 'models', 'autotags']);
	});

	it('orders a video with segments and director shots and plugin sections', async () => {
		response.data = { parameters: { steps: 20 }, models: models(2) };
		await mountModal(generation({ segments: segments(3), form_data: director(2) }, tags(2)), {
			extraSections: [{ id: 'send', title: 'Send to', rows: [], actions: [] }]
		});
		expect(sectionIds()).toEqual([
			'parameters',
			'segments',
			'director',
			'models',
			'x-send',
			'autotags'
		]);
	});

	it('omits Segments, Director shots and Auto tags when their data is absent', async () => {
		await mountModal(generation());
		expect(section('segments')).toBeNull();
		expect(section('director')).toBeNull();
		expect(section('autotags')).toBeNull();
	});

	it('keeps Parameters and Models when empty', async () => {
		await mountModal(generation());
		expect(section('parameters')?.textContent).toContain('No parameters');
		expect(section('models')?.textContent).toContain('No models');
	});
});

describe('identity lives in the header', () => {
	it('shows preset with version, one human line and a copyable full id in the header only', async () => {
		await mountModal(generation());
		const header = document.body.querySelector('[data-details-header]') as HTMLElement;
		const text = header.textContent?.replace(/\s+/g, ' ') ?? '';
		expect(text).toContain('v1.4');
		expect(text).toContain('1 min 42 s');
		expect(header.querySelector('button[aria-label="Copy ID"]')).not.toBeNull();
		expect(header.querySelector('button[aria-label="Rate 4 stars"]')).not.toBeNull();
		const column = document.body.querySelector('[data-details-info]') as HTMLElement;
		expect(column.querySelector('button[aria-label="Copy ID"]')).toBeNull();
		expect(column.textContent).not.toContain('took');
		expect(column.querySelector('[data-details-section]')?.getAttribute('data-details-section')).toBe('parameters');
	});

	it('shows the full id up to 17 characters and a middle ellipsis beyond', async () => {
		await mountModal(generation({ id: 'short-id-1' }));
		expect(document.body.querySelector('[data-details-meta] code')?.textContent).toBe('short-id-1');
		mountedModal?.$destroy();
		target?.remove();
		await mountModal(generation({ id: '3f2b8c1e-9d4a-4e7b-a1c2-5d6e7f8a9b0c' }));
		const shown = document.body.querySelector('[data-details-meta] code')?.textContent ?? '';
		expect(shown).toContain('…');
		expect(shown).toHaveLength(17);
	});

	it('keeps the exact times behind a focusable control that carries the tooltip', async () => {
		await mountModal(generation());
		const line = document.body.querySelector('[data-testid="detail-run-line"]') as HTMLButtonElement;
		expect(line.tagName).toBe('BUTTON');
		expect(line.tabIndex).toBe(0);
		expect(line.hasAttribute('data-has-tooltip')).toBe(true);
		line.focus();
		expect(document.activeElement).toBe(line);
	});

	it('offers Auto-file in the action bar instead of the column', async () => {
		await mountModal(generation());
		const ids = Array.from(document.body.querySelectorAll<HTMLElement>('[data-details-action-bar] [data-bar-item]')).map((node) => node.dataset.barItem);
		expect(ids).toContain('auto-file');
		expect(document.body.querySelector('[data-details-info]')?.textContent).not.toContain('Auto-file items like this');
	});

	it('keeps the rating working from the header', async () => {
		const setRating = vi.spyOn(historyStore, 'setRating').mockImplementation(() => undefined as never);
		await mountModal(generation({ id: 'gen-rate' }));
		await click(document.body.querySelector('button[aria-label="Rate 4 stars"]'));
		expect(setRating).toHaveBeenCalledWith('gen-rate', 4);
	});

	it('shows only the date when the run was not timed', async () => {
		await mountModal(generation({ run_seconds: null }));
		expect(document.body.querySelector('[data-testid="detail-generation-time"]')).toBeNull();
	});
});

describe('section headers', () => {
	it('uses a small uppercase eyebrow with a plain count and a chevron, with a divider above', async () => {
		response.data = { parameters: {}, models: models(3) };
		await mountModal(generation({ segments: segments(2) }));
		const seg = section('segments') as HTMLElement;
		const title = seg.querySelector('h3') as HTMLElement;
		expect(title.className).toContain('uppercase');
		expect(title.className).toContain('text-fg-muted');
		expect(title.firstChild?.textContent?.trim()).toBe('Segments');
		expect(title.querySelector('span')?.textContent).toBe('2');
		expect(seg.className).toContain('border-t');
		expect(header('segments')?.querySelector('svg')).not.toBeNull();
		expect(section('models')?.querySelector('h3 span')?.textContent).toBe('3');
	});

	it('gives plugin sections the same header without an icon', async () => {
		await mountModal(generation(), { extraSections: [{ id: 'send', title: 'Send to', icon: 'sparkles', rows: [], actions: [] }] });
		const box = document.body.querySelector('[data-extra-section="send"]') as HTMLElement;
		const title = box.querySelector('h3') as HTMLElement;
		expect(title.className).toContain('uppercase');
		expect(title.textContent).toBe('Send to');
		expect(box.querySelector('h3 svg')).toBeNull();
	});
});

describe('folds and defaults', () => {
	it('starts Segments and Auto tags closed, Models and Director shots open', async () => {
		response.data = { parameters: {}, models: models(2) };
		await mountModal(generation({ segments: segments(2), form_data: director(1) }, tags(2)));
		expect(header('segments')?.getAttribute('aria-expanded')).toBe('false');
		expect(header('autotags')?.getAttribute('aria-expanded')).toBe('false');
		expect(header('models')?.getAttribute('aria-expanded')).toBe('true');
		expect(header('director')?.getAttribute('aria-expanded')).toBe('true');
		expect(section('segments')?.textContent).not.toContain('segment text');
		expect(section('models')?.textContent).toContain('Model 0');
	});

	it('toggles a section and restores the choice on the next mount', async () => {
		await mountModal(generation({ segments: segments(2) }));
		await click(header('segments'));
		expect(header('segments')?.getAttribute('aria-expanded')).toBe('true');
		expect(section('segments')?.textContent).toContain('segment text 1');
		mountedModal?.$destroy();
		target?.remove();
		forgetDetailsPrefs();
		await mountModal(generation({ segments: segments(2) }));
		expect(header('segments')?.getAttribute('aria-expanded')).toBe('true');
		await click(header('segments'));
		expect(header('segments')?.getAttribute('aria-expanded')).toBe('false');
	});

	it('works when localStorage is blocked', async () => {
		vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
			throw new Error('blocked');
		});
		vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new Error('blocked');
		});
		await mountModal(generation({ segments: segments(2) }));
		await click(header('segments'));
		expect(header('segments')?.getAttribute('aria-expanded')).toBe('true');
	});

	it('does not fold 5 models and folds 6 to 3 with Show all (6)', async () => {
		response.data = { parameters: {}, models: models(5) };
		await mountModal(generation());
		expect(showAll('models')).toBeUndefined();
		expect(section('models')?.querySelectorAll('button[type="button"]:not([aria-expanded])').length).toBeGreaterThanOrEqual(5);
		mountedModal?.$destroy();
		target?.remove();
		response.data = { parameters: {}, models: models(6) };
		await mountModal(generation());
		const button = showAll('models') as HTMLButtonElement;
		expect(button.textContent?.trim()).toBe('Show all (6)');
		expect(button.getAttribute('aria-expanded')).toBe('false');
		expect(section('models')?.textContent).toContain('Model 2');
		expect(section('models')?.textContent).not.toContain('Model 3');
		await click(button);
		expect(showAll('models')?.textContent?.trim()).toBe('Show less');
		expect(section('models')?.textContent).toContain('Model 5');
	});

	it('folds auto tags above 8 only, showing 6', async () => {
		await mountModal(generation({}, tags(8)));
		await click(header('autotags'));
		expect(showAll('autotags')).toBeUndefined();
		mountedModal?.$destroy();
		target?.remove();
		forgetDetailsPrefs();
		localStorage.clear();
		await mountModal(generation({}, tags(9)));
		await click(header('autotags'));
		expect(showAll('autotags')?.textContent?.trim()).toBe('Show all (9)');
		expect(section('autotags')?.textContent).toContain('tag 5');
		expect(section('autotags')?.textContent).not.toContain('tag 6');
	});

	it('folds segments and director shots above 5', async () => {
		await mountModal(generation({ segments: segments(5), form_data: director(5) }));
		await click(header('segments'));
		expect(showAll('segments')).toBeUndefined();
		expect(showAll('director')).toBeUndefined();
		mountedModal?.$destroy();
		target?.remove();
		forgetDetailsPrefs();
		await mountModal(generation({ segments: segments(6), form_data: director(6) }));
		expect(showAll('segments')?.textContent?.trim()).toBe('Show all (6)');
		expect(showAll('director')?.textContent?.trim()).toBe('Show all (6)');
		expect(section('director')?.textContent).toContain('Shot 3');
		expect(section('director')?.textContent).not.toContain('Shot 4');
	});
});

describe('plugin sections', () => {
	it('renders title, rows and actions untouched and keeps their handlers', async () => {
		const onToggle = vi.fn();
		const onClick = vi.fn();
		await mountModal(generation(), {
			extraSections: [
				{
					id: 'send',
					title: 'Send to',
					rows: [{ id: 'a', label: 'Upscale', checked: false, onToggle }],
					actions: [{ id: 'run', label: 'Run selected', onClick }]
				}
			]
		});
		const box = document.body.querySelector('[data-extra-section="send"]') as HTMLElement;
		expect(box.querySelector('h3')?.textContent).toBe('Send to');
		expect(box.textContent).toContain('Upscale');
		await click(box.querySelector('input[type="checkbox"]'));
		expect(onToggle).toHaveBeenCalledWith(true);
		await click([...box.querySelectorAll('button')].find((b) => b.textContent?.includes('Run selected')));
		expect(onClick).toHaveBeenCalledTimes(1);
	});
});

describe('failure notice', () => {
	const RAW = 'RuntimeError: failed in /srv/models/private/unet.safetensors';
	const alertText = () => document.body.querySelector('[data-failure-alert]')?.textContent?.replace(/\s+/g, ' ') ?? '';

	it('shows an admin the stored message above everything else', async () => {
		signInAs('ADMIN');
		await mountModal(generation({ status: 'failed', error_message: RAW, error_id: 'E-9' }), {
			extraSections: []
		});
		expect(alertText()).toContain('Failed');
		expect(alertText()).toContain(RAW);
		expect(alertText()).toContain('E-9');
		const alert = document.body.querySelector('[data-failure-alert]') as HTMLElement;
		const panel = document.body.querySelector('[data-details-panel]') as HTMLElement;
		expect(alert.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(alert.querySelector('button[aria-label="Copy error message"]')).not.toBeNull();
	});

	it('shows a regular user only the message the server scoped for them', async () => {
		signInAs('USER');
		await mountModal(
			generation({
				status: 'failed',
				error_message: 'Out of memory.',
				error_user_message: 'Out of memory. Try a smaller size.',
				error_contact_admin: true,
				error_id: 'E-10'
			})
		);
		expect(alertText()).toContain('Out of memory. Try a smaller size.');
		expect(alertText()).toContain('Give this to your admin');
	});

	it('hides an unscoped raw message from a regular user', async () => {
		signInAs('USER');
		await mountModal(generation({ status: 'failed', error_message: RAW }));
		expect(alertText()).toContain('This generation failed.');
		expect(document.body.textContent).not.toContain('/srv/models');
	});

	it('is absent for a completed generation and present for a cancelled one', async () => {
		signInAs('USER');
		await mountModal(generation());
		expect(document.body.querySelector('[data-failure-alert]')).toBeNull();
		mountedModal?.$destroy();
		target?.remove();
		await mountModal(generation({ status: 'cancelled' }));
		expect(alertText()).toContain('Cancelled');
	});

	it('orders a failed generation: alert, then Parameters, Models', async () => {
		signInAs('ADMIN');
		await mountModal(generation({ status: 'failed', error_message: 'x' }));
		expect(sectionIds()).toEqual(['parameters', 'models']);
		expect(document.body.querySelectorAll('[data-failure-alert]')).toHaveLength(1);
	});
});
