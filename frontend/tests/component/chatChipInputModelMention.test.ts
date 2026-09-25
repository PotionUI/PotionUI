// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

const suggestChatResources = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: { suggestChatResources }
}));

const { createClassComponent } = await import('svelte/legacy');
const { default: ChatChipInput } = await import('$lib/components/chat/ChatChipInput.svelte');

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
	suggestChatResources.mockReset();
});

function wait(ms: number) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

function typeInto(editor: HTMLElement, text: string) {
	editor.textContent = text;
	const node = editor.firstChild as Text;
	const range = document.createRange();
	range.setStart(node, text.length);
	range.collapse(true);
	const selection = window.getSelection()!;
	selection.removeAllRanges();
	selection.addRange(range);
	editor.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('ChatChipInput model mentions', () => {
	it('searches models for a bare @query and inserts a typed chip carrying the model id', async () => {
		suggestChatResources.mockResolvedValue({
			success: true,
			data: {
				suggestions: [
					{
						uri: 'models.lora.01LORA',
						label: 'Detailer XL',
						kind: 'lora',
						description: 'detailer_v2',
						has_children: false,
						icon: 'box',
						badge: 'lora'
					}
				]
			}
		});

		const target = document.createElement('div');
		document.body.appendChild(target);
		const changes: Array<{ value: string; resources: Record<string, { uri: string; label: string }> }> = [];
		const component = createClassComponent({
			component: ChatChipInput as never,
			target,
			props: { value: '', resources: {}, mode: 'generation', formData: {}, loraSelections: {} }
		});
		component.$on('change', (e: CustomEvent) => changes.push(e.detail));
		cleanup = () => component.$destroy();

		const editor = target.querySelector('.chat-chip-input') as HTMLElement;
		typeInto(editor, 'use @deta');
		await wait(260);

		expect(suggestChatResources).toHaveBeenCalledWith('deta', 'generation');
		const row = document.querySelector('[role="option"]') as HTMLElement;
		expect(row.textContent).toContain('Detailer XL');
		expect(row.textContent).toContain('detailer_v2');
		expect(row.textContent).toContain('lora');
		expect(row.textContent).not.toContain('models.lora.01LORA');

		row.click();
		await wait(0);

		const last = changes[changes.length - 1];
		expect(last.value).toBe('use @models.lora.01LORA');
		expect(Object.values(last.resources)).toEqual([{ uri: 'models.lora.01LORA', label: 'lora:Detailer XL' }]);
		expect(editor.querySelector('.inline-chip')?.textContent).toContain('lora:Detailer XL');
	});

	it('offers the form LoRA row first and inserts a chip for just that row', async () => {
		suggestChatResources.mockResolvedValue({ success: true, data: { suggestions: [] } });

		const target = document.createElement('div');
		document.body.appendChild(target);
		const changes: Array<{ value: string; resources: Record<string, { uri: string; label: string }> }> = [];
		const component = createClassComponent({
			component: ChatChipInput as never,
			target,
			props: {
				value: '',
				resources: {},
				mode: 'generation',
				formData: { loras: [{ model: 'model:l-1', strength: 0.8 }] },
				loraSelections: { loras: [{ id: 'l-1', name: 'Detail LoRA', strength: 0.8 }] }
			}
		});
		component.$on('change', (e: CustomEvent) => changes.push(e.detail));
		cleanup = () => component.$destroy();

		const editor = target.querySelector('.chat-chip-input') as HTMLElement;
		typeInto(editor, 'tune @detail');
		await wait(260);

		const row = document.querySelector('[role="option"]') as HTMLElement;
		expect(row.textContent).toContain('Detail LoRA');
		expect(row.textContent).toContain('strength 0.8');

		row.click();
		await wait(0);

		const last = changes[changes.length - 1];
		expect(last.value).toBe('tune @form.loras.l-1');
		expect(Object.values(last.resources)).toEqual([{ uri: 'form.loras.l-1', label: 'form.loras:Detail LoRA' }]);
	});
});
