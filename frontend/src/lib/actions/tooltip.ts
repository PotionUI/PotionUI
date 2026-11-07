import { mount, unmount } from 'svelte';
import TooltipBubble from '$lib/components/TooltipBubble.svelte';
import { watchTooltipTrigger } from './tooltipTrigger';

export interface TooltipOptions {
	text?: string | null;
	position?: 'top' | 'bottom' | 'left' | 'right';
	delay?: number;
	kbd?: string | string[];
	truncateOnly?: boolean;
	wrap?: boolean;
	focusSelf?: boolean;
}

export type TooltipParam = string | null | undefined | TooltipOptions;

const OVERFLOW_SELECTOR = '.truncate, [class*="line-clamp"], [class*="text-ellipsis"]';

let sequence = 0;

function normalize(param: TooltipParam): TooltipOptions {
	return typeof param === 'string' || param == null ? { text: param } : param;
}

export function isOverflowing(node: HTMLElement): boolean {
	const cut = (el: Element) => el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight;
	return cut(node) || Array.from(node.querySelectorAll(OVERFLOW_SELECTOR)).some(cut);
}

export default function tooltip(node: HTMLElement, param: TooltipParam) {
	let options = normalize(param);
	let bubble: Record<string, unknown> | undefined;
	let pending = false;
	let previousDescribedBy: string | null = null;
	const id = `tooltip-${++sequence}`;

	function hide() {
		pending = false;
		if (!bubble) return;
		unmount(bubble);
		bubble = undefined;
		if (previousDescribedBy === null) node.removeAttribute('aria-describedby');
		else node.setAttribute('aria-describedby', previousDescribedBy);
	}

	function mountBubble() {
		if (!pending) return;
		pending = false;
		if (bubble || !options.text || !node.isConnected) return;
		previousDescribedBy = node.getAttribute('aria-describedby');
		node.setAttribute('aria-describedby', id);
		bubble = mount(TooltipBubble, {
			target: document.body,
			props: {
				text: options.text,
				anchor: node,
				position: options.position,
				kbd: options.kbd,
				wrap: options.wrap,
				id
			}
		});
	}

	function reveal() {
		if (bubble || pending || !options.text) return;
		if (options.truncateOnly && !isOverflowing(node)) return;
		pending = true;
		queueMicrotask(mountBubble);
	}

	const unwatch = watchTooltipTrigger(node, {
		delay: () => options.delay ?? 200,
		open: reveal,
		close: hide,
		focusSelf: () => options.focusSelf === true
	});

	return {
		update(next: TooltipParam) {
			const wasVisible = bubble !== undefined;
			hide();
			options = normalize(next);
			if (wasVisible) reveal();
		},
		destroy() {
			unwatch();
			hide();
		}
	};
}
