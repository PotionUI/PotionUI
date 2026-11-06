import { describe, it, expect, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { ReasoningBlock } from '../../src/lib/types/chat';

const { default: ChatReasoningBlock } = await import('../../src/lib/components/chat/ChatReasoningBlock.svelte');
const { createClassComponent } = await import('svelte/legacy');

const block = (text: string): ReasoningBlock => ({ kind: 'raw', text, open: true, started_at: Date.now() }) as ReasoningBlock;

function geometry(el: HTMLElement) {
	Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => 1000 + el.textContent!.length });
	Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 240 });
}

async function settle() {
	for (let i = 0; i < 3; i++) await Promise.resolve();
	flushSync();
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('streamed reasoning body scrolling', () => {
	function mountOpen() {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({
			component: ChatReasoningBlock as never,
			target,
			props: { blocks: [block('a')], isStreaming: true }
		});
		flushSync();
		const body = target.querySelector('[data-testid="reasoning-body"]') as HTMLElement;
		geometry(body);
		return { component, body };
	}

	it('follows the stream while the reader is at the bottom', async () => {
		const { component, body } = mountOpen();
		await settle();
		body.scrollTop = body.scrollHeight - body.clientHeight;
		body.dispatchEvent(new Event('scroll'));
		component.$set({ blocks: [block('a'.repeat(200))] });
		await settle();
		expect(body.scrollTop).toBe(body.scrollHeight);
	});

	it('leaves the position alone once the reader scrolled up', async () => {
		const { component, body } = mountOpen();
		await settle();
		body.scrollTop = 100;
		body.dispatchEvent(new Event('scroll'));
		for (const n of [50, 100, 200]) {
			component.$set({ blocks: [block('a'.repeat(n))] });
			await settle();
			expect(body.scrollTop).toBe(100);
		}
	});

	it('resumes following after the reader returns to the bottom', async () => {
		const { component, body } = mountOpen();
		await settle();
		body.scrollTop = 100;
		body.dispatchEvent(new Event('scroll'));
		body.scrollTop = body.scrollHeight - body.clientHeight;
		body.dispatchEvent(new Event('scroll'));
		component.$set({ blocks: [block('a'.repeat(300))] });
		await settle();
		expect(body.scrollTop).toBe(body.scrollHeight);
	});
});
