import { flushSync } from 'svelte';
import { createClassComponent } from 'svelte/legacy';
import { settle } from './settle';

export type SchemaFormHandle = {
	target: HTMLElement;
	form: any;
	set: (props: Record<string, unknown>) => void;
	field: (name: string) => HTMLElement | null;
	destroy: () => void;
};

export async function mountSchemaForm(
	DynamicForm: unknown,
	props: Record<string, unknown>
): Promise<SchemaFormHandle> {
	const target = document.body.appendChild(document.createElement('div'));
	const form: any = createClassComponent({ component: DynamicForm as never, target, props });
	flushSync();
	await settle(4);
	return {
		target,
		form,
		set(next) {
			form.$set(next);
			flushSync();
		},
		field: (name) => target.querySelector<HTMLElement>(`[data-field-name="${name}"]`),
		destroy() {
			form.$destroy();
			target.remove();
		}
	};
}

export function fire(element: Element, type: string, init: EventInit = {}): void {
	element.dispatchEvent(new Event(type, { bubbles: true, ...init }));
	flushSync();
}

export function press(element: Element, type: 'keydown' | 'keyup', key: string): KeyboardEvent {
	const event = new KeyboardEvent(type, { key, bubbles: true, cancelable: true });
	element.dispatchEvent(event);
	flushSync();
	return event;
}

export function setRange(input: HTMLInputElement, value: number): void {
	input.value = String(value);
	fire(input, 'input');
}

export function typeInto(input: HTMLInputElement | HTMLTextAreaElement, text: string): void {
	input.focus();
	input.value = text;
	fire(input, 'input');
}

export async function pickOption(fieldEl: HTMLElement, label: string): Promise<void> {
	fieldEl.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]')!.click();
	await settle(10);
	Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'))
		.find((el) => el.textContent?.trim() === label)!
		.click();
	await settle(10);
}
