import { describe, it, expect, vi, beforeEach, afterEach, onTestFinished } from 'vitest';
import { flushSync, tick } from 'svelte';
import { createClassComponent } from 'svelte/legacy';
import { api } from '$lib/services/api/index';
import { keybindingsStore } from '$lib/stores/keybindings';
import {
	init,
	destroy,
	globalBindingFor,
	suppressKeyboard,
	resumeKeyboard,
	holdKeyboard,
	releaseAllKeyboardHolds
} from '$lib/services/keyboard';
import { page } from '$app/stores';
import type { Writable } from 'svelte/store';
import { isEditableTarget, isTypingInField } from '$lib/utils/editableTarget';
import { handleDrawerKeydown } from '$lib/components/drawer/drawerBehavior';
import portal from '$lib/actions/portal';
import InlineChipEditor from '$lib/components/InlineChipEditor.svelte';

const SINGLE_KEY_BINDINGS: [string, string, string][] = [
	['show_help', '?', 'global'],
	['open_chat', 'c', 'global'],
	['toggle_sidebar', 'b', 'global'],
	['open_quick_actions', 'a', 'global'],
	['start_generation', 'g', 'generate'],
	['new_tab', 't', 'generate'],
	['close_tab', 'x', 'generate'],
	['toggle_left_panel', 'f', 'generate'],
	['toggle_floating_form', 'q', 'generate'],
	['toggle_last_generations', 'h', 'generate'],
	['toggle_floating_workbench', 'w', 'generate'],
	['toggle_workbench_panel', 'e', 'generate'],
	['save_session', 's', 'generate'],
	['go_generate', '1', 'global'],
	['go_history', '2', 'global'],
	['go_library', '3', 'global'],
	['go_models', '4', 'global'],
	['go_phrasebook', '5', 'global'],
	['go_prompts', '6', 'global'],
	['go_inspirations', '7', 'global']
];
const TYPED = SINGLE_KEY_BINDINGS.map(([, key]) => key).join('');

let fired: string[] = [];
let editor: ReturnType<typeof createClassComponent> | undefined;

beforeEach(async () => {
	fired = [];
	keybindingsStore.reset();
	const keybindings = [
		...SINGLE_KEY_BINDINGS.map(([action_id, key, context]) => ({ action_id, key, modifiers: '', context })),
		{ action_id: 'search_everything', key: 'k', modifiers: 'ctrl', context: 'global' }
	].map((binding) => ({ ...binding, label: binding.action_id, category: 'general', enabled: true, is_custom: false }));
	vi.spyOn(api, 'getKeybindings').mockResolvedValue({ success: true, data: { keybindings } });
	await keybindingsStore.loadBindings();
	for (const { action_id } of keybindings) keybindingsStore.registerHandler(action_id, () => fired.push(action_id));
	init();
});

afterEach(() => {
	destroy();
	editor?.$destroy();
	editor = undefined;
	keybindingsStore.reset();
	vi.restoreAllMocks();
	document.body.innerHTML = '';
});

function press(target: EventTarget, key: string, init: KeyboardEventInit = {}): KeyboardEvent {
	const event = new KeyboardEvent('keydown', {
		key,
		shiftKey: key === '?',
		bubbles: true,
		cancelable: true,
		composed: true,
		...init
	});
	target.dispatchEvent(event);
	return event;
}

function typeInto(field: HTMLInputElement | HTMLTextAreaElement, text: string, afterEach: () => void = () => {}) {
	const swallowed: string[] = [];
	for (const char of text) {
		const event = press(document.activeElement ?? document.body, char);
		if (event.defaultPrevented) swallowed.push(char);
		if (document.activeElement !== field || event.defaultPrevented) continue;
		field.value += char;
		field.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		afterEach();
	}
	return swallowed;
}

function mountEditor(value: string, variant: 'default' | 'segment-composer') {
	const target = document.body.appendChild(document.createElement('div'));
	editor = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value, chips: {}, variant, borderless: true, variables: {} }
	});
	const values: string[] = [];
	const handle = editor as unknown as {
		$on: (name: string, cb: (e: CustomEvent) => void) => void;
		$set: (props: Record<string, unknown>) => void;
	};
	handle.$on('change', (e) => {
		values.push(e.detail.value);
		handle.$set({ value: e.detail.value });
	});
	flushSync();
	return { target, values };
}

function el(html: string, selector: string): Element {
	const wrap = document.body.appendChild(document.createElement('div'));
	wrap.innerHTML = html;
	return wrap.querySelector(selector)!;
}

function choicePopover(): HTMLElement | null {
	return document.querySelector<HTMLElement>('[role="dialog"][aria-label="Edit choice group"]');
}

describe('global shortcuts outside fields', () => {
	it('fires every single-key shortcut when the page body has focus', () => {
		for (const [, key] of SINGLE_KEY_BINDINGS) {
			expect(press(document.body, key).defaultPrevented).toBe(true);
		}
		expect(fired).toEqual(SINGLE_KEY_BINDINGS.map(([id]) => id));
	});

	it('still fires from controls that take no text', () => {
		const checkbox = document.body.appendChild(Object.assign(document.createElement('input'), { type: 'checkbox' }));
		const button = document.body.appendChild(document.createElement('button'));
		press(checkbox, 't');
		press(button, 'q');
		expect(fired).toEqual(['new_tab', 'toggle_floating_form']);
	});

	it('ignores keys that belong to an IME composition', () => {
		press(document.body, 't', { isComposing: true });
		expect(fired).toEqual([]);
	});

	it('globalBindingFor names the binding the dispatcher runs for a key', () => {
		const key = (k: string, init: KeyboardEventInit = {}) => new KeyboardEvent('keydown', { key: k, ...init });
		expect(globalBindingFor(key('c'))?.actionId).toBe('open_chat');
		expect(globalBindingFor(key('g'))?.actionId).toBe('start_generation');
		expect(globalBindingFor(key('k', { ctrlKey: true }))?.actionId).toBe('search_everything');
		expect(globalBindingFor(key('m'))).toBeNull();
		expect(globalBindingFor(key('c', { altKey: true }))).toBeNull();
	});

	it('globalBindingFor leaves page-context letters off other pages and while shortcuts are suppressed', () => {
		const c = new KeyboardEvent('keydown', { key: 'c' });
		const g = new KeyboardEvent('keydown', { key: 'g' });
		const route = page as unknown as Writable<{ url: URL }>;
		route.update((p) => ({ ...p, url: new URL('http://localhost/library') }));
		onTestFinished(() => route.update((p) => ({ ...p, url: new URL('http://localhost/generate') })));
		expect(globalBindingFor(g)).toBeNull();
		expect(globalBindingFor(c)?.actionId).toBe('open_chat');
		suppressKeyboard();
		onTestFinished(resumeKeyboard);
		expect(globalBindingFor(c)).toBeNull();
	});

	it('stays silent while a plugin holds the keyboard, and fires again once every hold is released', () => {
		const release = holdKeyboard();
		onTestFinished(releaseAllKeyboardHolds);
		expect(press(document.body, 't').defaultPrevented).toBe(false);
		expect(globalBindingFor(new KeyboardEvent('keydown', { key: 'c' }))).toBeNull();
		release();
		press(document.body, 't');
		holdKeyboard();
		releaseAllKeyboardHolds();
		press(document.body, 'q');
		expect(fired).toEqual(['new_tab', 'toggle_floating_form']);
	});

	it('globalBindingFor skips a binding whose handler is gone', () => {
		keybindingsStore.unregisterHandler('open_chat');
		expect(globalBindingFor(new KeyboardEvent('keydown', { key: 'c' }))).toBeNull();
	});
});

describe('typing in fields never fires a shortcut', () => {
	for (const variant of ['segment-composer', 'default'] as const) {
		it(`keeps the popover open and focus in the option through every keystroke and Add option (${variant})`, async () => {
			const { target, values } = mountEditor('cat {a|b}', variant);
			const trigger = target.querySelector<HTMLButtonElement>(
				variant === 'default' ? '.choice-group-chip button' : '.chip-main'
			)!;
			trigger.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
			flushSync();
			const popover = choicePopover()!;
			const optionFields = () => popover.querySelectorAll<HTMLInputElement>('input[placeholder="option text"]');
			const stillEditing = (field: HTMLInputElement) => () => {
				expect(choicePopover()).toBe(popover);
				expect(field.isConnected).toBe(true);
				expect(document.activeElement).toBe(field);
			};

			const first = optionFields()[0];
			first.focus();
			expect(typeInto(first, 'tqwf', stillEditing(first))).toEqual([]);
			await tick();
			expect(values.at(-1)).toBe('cat {atqwf|b}');

			const addOption = [...popover.querySelectorAll('button')].find((b) => b.textContent?.includes('Add option'))!;
			addOption.focus();
			addOption.click();
			flushSync();
			await tick();
			expect(choicePopover()).toBe(popover);
			expect(document.activeElement).toBe(addOption);
			expect(optionFields()).toHaveLength(3);
			expect(values.at(-1)).toBe('cat {atqwf|b|}');

			const added = optionFields()[2];
			added.focus();
			expect(typeInto(added, TYPED, stillEditing(added))).toEqual([]);
			await tick();

			expect(fired).toEqual([]);
			expect(optionFields()[0]).toBe(first);
			expect(values.at(-1)).toBe(`cat {atqwf|b|${TYPED}}`);
		});
	}

	it('textarea', () => {
		const textarea = document.body.appendChild(document.createElement('textarea'));
		textarea.focus();
		expect(typeInto(textarea, TYPED)).toEqual([]);
		expect(fired).toEqual([]);
		expect(textarea.value).toBe(TYPED);
	});

	it('contenteditable prompt editor and the text inside it', () => {
		const { target } = mountEditor('cat', 'segment-composer');
		const host = target.querySelector<HTMLElement>('[contenteditable="true"]')!;
		const inner = host.appendChild(document.createElement('span'));
		for (const key of TYPED) {
			expect(press(host, key).defaultPrevented).toBe(false);
			expect(press(inner, key).defaultPrevented).toBe(false);
		}
		expect(fired).toEqual([]);
	});

	it('input inside a portaled dialog', () => {
		const owner = document.body.appendChild(document.createElement('div'));
		const dialog = owner.appendChild(document.createElement('div'));
		dialog.setAttribute('role', 'dialog');
		const input = dialog.appendChild(document.createElement('input'));
		const action = portal(dialog);
		expect(dialog.parentElement).toBe(document.body);
		input.focus();
		expect(typeInto(input, TYPED)).toEqual([]);
		expect(fired).toEqual([]);
		expect(input.value).toBe(TYPED);
		action.destroy();
	});

	it('input inside a shadow root', () => {
		const host = document.body.appendChild(document.createElement('div'));
		const input = host.attachShadow({ mode: 'open' }).appendChild(document.createElement('input'));
		for (const key of TYPED) press(input, key);
		expect(fired).toEqual([]);
	});

	it('modifier shortcuts still fire from a text field', () => {
		const textarea = document.body.appendChild(document.createElement('textarea'));
		textarea.focus();
		press(textarea, 'k', { ctrlKey: true });
		expect(fired).toEqual(['search_everything']);
	});
});

describe('isEditableTarget', () => {
	it('treats text-taking controls, editable hosts and text roles as fields', () => {
		expect(isEditableTarget(el('<input type="search">', 'input'))).toBe(true);
		expect(isEditableTarget(el('<input type="number">', 'input'))).toBe(true);
		expect(isEditableTarget(el('<select><option>a</option></select>', 'select'))).toBe(true);
		expect(isEditableTarget(el('<div contenteditable="plaintext-only"><b>x</b></div>', 'b'))).toBe(true);
		expect(isEditableTarget(el('<div role="combobox"><span>x</span></div>', 'span'))).toBe(true);
		expect(isEditableTarget(el('<div role="searchbox">x</div>', 'div'))).toBe(true);
	});

	it('leaves non-text controls and non-editable islands alone', () => {
		expect(isEditableTarget(el('<input type="range">', 'input'))).toBe(false);
		expect(isEditableTarget(el('<input type="radio">', 'input'))).toBe(false);
		expect(isEditableTarget(el('<div contenteditable="true"><span contenteditable="false"><button>x</button></span></div>', 'button'))).toBe(false);
		expect(isEditableTarget(document.body)).toBe(false);
		expect(isEditableTarget(null)).toBe(false);
	});
});

describe('isTypingInField', () => {
	function typingOn(target: Element, key: string): boolean {
		let typing = false;
		const listener = (event: KeyboardEvent) => (typing = isTypingInField(event));
		window.addEventListener('keydown', listener);
		press(target, key);
		window.removeEventListener('keydown', listener);
		return typing;
	}

	it('lets sliders and radios keep their arrow keys but not single-key shortcuts', () => {
		const range = el('<input type="range">', 'input');
		expect(typingOn(range, 'ArrowRight')).toBe(true);
		expect(typingOn(range, 'End')).toBe(true);
		expect(typingOn(range, 't')).toBe(false);
		expect(typingOn(el('<div role="slider" tabindex="0"></div>', 'div'), 'ArrowLeft')).toBe(true);
		expect(typingOn(el('<div role="radiogroup"><button role="radio">1</button></div>', 'button'), 'ArrowDown')).toBe(true);
		expect(typingOn(el('<button>go</button>', 'button'), 'ArrowRight')).toBe(false);
		expect(typingOn(document.body, 'ArrowRight')).toBe(false);
	});
});

describe('drawer `/` search shortcut', () => {
	function drawer() {
		const root = document.body.appendChild(document.createElement('div'));
		const searchEl = root.appendChild(document.createElement('input'));
		const textarea = root.appendChild(document.createElement('textarea'));
		const textbox = root.appendChild(document.createElement('div'));
		textbox.setAttribute('role', 'textbox');
		textbox.tabIndex = 0;
		const listener = (event: KeyboardEvent) =>
			handleDrawerKeydown(event, { root, searchEl, keepOpen: false, onClose: vi.fn() });
		window.addEventListener('keydown', listener);
		onTestFinished(() => window.removeEventListener('keydown', listener));
		return { searchEl, textarea, textbox };
	}

	it('leaves `/` to a textarea and to a role=textbox field', () => {
		const { textarea, textbox } = drawer();
		for (const field of [textarea, textbox]) {
			field.focus();
			expect(press(field, '/').defaultPrevented).toBe(false);
			expect(document.activeElement).toBe(field);
		}
	});

	it('focuses the drawer search from the page body', () => {
		const { searchEl } = drawer();
		expect(press(document.body, '/').defaultPrevented).toBe(true);
		expect(document.activeElement).toBe(searchEl);
	});
});
