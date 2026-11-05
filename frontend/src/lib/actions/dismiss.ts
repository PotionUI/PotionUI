import { browser } from '$app/environment';
import {
	openDismissLayers,
	registerDismissLayer,
	topDismissLayer,
	unregisterDismissLayer,
	type DismissLayer
} from './layerStack';

export type DismissReason = 'outside' | 'escape';

export interface DismissOptions {
	onDismiss: (reason: DismissReason, event: Event) => void;
	ignore?: Array<HTMLElement | null | undefined>;
	escape?: boolean;
	outside?: boolean;
	skipModals?: boolean;
}

interface ActionLayer extends DismissLayer {
	options: DismissOptions;
}

const CHAT_SHELL_LABEL = 'AI chat';

let listening = false;

function isActionLayer(layer: DismissLayer | undefined): layer is ActionLayer {
	return !!layer && 'options' in layer;
}

function inLaterModalContext(layer: ActionLayer, target: Element): boolean {
	const targetModal = target.closest('[aria-modal="true"]');
	if (!targetModal || targetModal.getAttribute('aria-label') === CHAT_SHELL_LABEL) return false;
	return targetModal !== layer.node.closest('[aria-modal="true"]');
}

function onPointerDown(event: PointerEvent) {
	const target = event.target;
	if (!(target instanceof Node)) return;
	const layers = [...openDismissLayers()];
	for (let i = layers.length - 1; i >= 0; i--) {
		const layer = layers[i];
		if (!isActionLayer(layer) || layer.options.outside === false) continue;
		const inside =
			layers.slice(i).some((l) => l.node.contains(target)) ||
			(layer.options.ignore ?? []).some((el) => el?.contains(target));
		if (inside) continue;
		if (layer.options.skipModals && target instanceof Element && inLaterModalContext(layer, target)) continue;
		layer.options.onDismiss('outside', event);
	}
}

function onKeyDown(event: KeyboardEvent) {
	if (event.key !== 'Escape' || event.defaultPrevented) return;
	if (event.target instanceof Element && event.target.closest('[data-keeps-escape]')) return;
	const top = topDismissLayer();
	if (!isActionLayer(top) || top.options.escape === false) return;
	event.preventDefault();
	event.stopPropagation();
	top.options.onDismiss('escape', event);
}

function listen() {
	if (listening) return;
	listening = true;
	document.addEventListener('pointerdown', onPointerDown, true);
	window.addEventListener('keydown', onKeyDown, true);
}

function unlisten() {
	if (!listening || openDismissLayers().length > 0) return;
	listening = false;
	document.removeEventListener('pointerdown', onPointerDown, true);
	window.removeEventListener('keydown', onKeyDown, true);
}

export default function dismiss(node: HTMLElement, options: DismissOptions) {
	if (!browser) return { update() {}, destroy() {} };

	const layer: ActionLayer = { node, options };
	let registered = false;

	function release() {
		if (!registered) return;
		unregisterDismissLayer(layer);
		registered = false;
		unlisten();
	}

	registerDismissLayer(layer);
	registered = true;
	listen();
	node.addEventListener('outrostart', release);

	return {
		update(next: DismissOptions) {
			layer.options = next;
		},
		destroy() {
			node.removeEventListener('outrostart', release);
			release();
		}
	};
}
