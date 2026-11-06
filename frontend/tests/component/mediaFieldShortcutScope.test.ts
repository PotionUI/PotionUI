// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { CORE_TOOLS } from './fixtures/mediaToolCatalog';

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);

vi.mock('$lib/services/api/mediaTools', () => ({
	listMediaTools: vi.fn().mockResolvedValue(CORE_TOOLS),
	startMediaToolRun: vi.fn(),
	getMediaToolRun: vi.fn(),
	cancelMediaToolRun: vi.fn(),
	previewMediaTool: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({
	api: {
		getKeybindings: vi.fn().mockResolvedValue({
			success: true,
			data: {
				keybindings: [
					{
						action_id: 'open_chat',
						key: 'c',
						modifiers: '',
						label: 'Open AI Chat',
						category: 'general',
						context: 'global',
						enabled: true,
						is_custom: false
					}
				]
			}
		}),
		listGenerationMedia: vi.fn().mockResolvedValue({ success: false }),
		getUploadInfo: vi.fn().mockResolvedValue({ success: false }),
		listUploads: vi
			.fn()
			.mockResolvedValue({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } }),
		uploadMedia: vi.fn()
	}
}));

vi.mock('$lib/utils/storage', () => ({
	storage: { get: vi.fn().mockReturnValue(null), set: vi.fn(), remove: vi.fn() }
}));

const { default: MediaLoaderField } = await import(
	'$lib/components/form-fields/MediaLoaderField.svelte'
);
const { keybindingsStore } = await import('$lib/stores/keybindings');
const keyboard = await import('$lib/services/keyboard');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

async function settle() {
	for (let i = 0; i < 6; i += 1) {
		await tick();
		await Promise.resolve();
	}
}

function pressC(target: EventTarget = document.body): KeyboardEvent {
	const event = new KeyboardEvent('keydown', { key: 'c', bubbles: true, cancelable: true });
	target.dispatchEvent(event);
	return event;
}

function cropOpen(): boolean {
	return Array.from(document.body.querySelectorAll('button')).some(
		(button) => (button.textContent || '').trim() === 'Apply'
	);
}

const chat = vi.fn();
let field: HTMLElement;
let component: { $destroy: () => void };

beforeEach(async () => {
	document.body.innerHTML = '';
	chat.mockReset();
	keybindingsStore.reset();
	await keybindingsStore.loadBindings();
	keybindingsStore.registerHandler('open_chat', chat);
	keyboard.init();
	const target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: MediaLoaderField as any,
		target,
		props: {
			name: 'reference_image',
			config: { title: 'Reference', accept: 'image/*' },
			value: {
				path: 'uploads/portrait.png',
				relative_path: 'uploads/portrait.png',
				url: '/api/media/uploads/portrait.png',
				name: 'portrait.png',
				type: 'image',
				metadata: { width: 1024, height: 1536, size: 2411724 }
			},
			onChange: vi.fn()
		}
	});
	await settle();
	field = target.querySelector<HTMLElement>('[data-media-field]')!;
});

afterEach(() => {
	component.$destroy();
	keyboard.destroy();
});

describe('media field shortcuts vs global shortcuts', () => {
	it('opens the chat and not the crop tool when C is pressed over the field', async () => {
		field.dispatchEvent(new MouseEvent('mouseenter'));
		pressC();
		await settle();
		expect(chat).toHaveBeenCalledTimes(1);
		expect(cropOpen()).toBe(false);
	});

	it('runs the crop tool over the field when no global shortcut claims C', async () => {
		keybindingsStore.unregisterHandler('open_chat');
		field.dispatchEvent(new MouseEvent('mouseenter'));
		pressC();
		await settle();
		expect(cropOpen()).toBe(true);
		expect(chat).not.toHaveBeenCalled();
	});

	it('runs only the crop tool when C is pressed with focus inside the field', async () => {
		const button = field.querySelector('button')!;
		pressC(button);
		await settle();
		expect(cropOpen()).toBe(true);
		expect(chat).not.toHaveBeenCalled();
	});

	it('opens the chat when C is pressed elsewhere', async () => {
		pressC();
		await settle();
		expect(chat).toHaveBeenCalledTimes(1);
		expect(cropOpen()).toBe(false);
	});

	it('does neither when C is typed into a text input', async () => {
		field.dispatchEvent(new MouseEvent('mouseenter'));
		const input = document.createElement('input');
		input.type = 'text';
		document.body.appendChild(input);
		pressC(input);
		await settle();
		expect(chat).not.toHaveBeenCalled();
		expect(cropOpen()).toBe(false);
	});

	it('skips an event another handler already consumed', async () => {
		const consume = (e: Event) => e.preventDefault();
		document.body.addEventListener('keydown', consume, { once: true });
		pressC();
		expect(chat).not.toHaveBeenCalled();
	});
});
