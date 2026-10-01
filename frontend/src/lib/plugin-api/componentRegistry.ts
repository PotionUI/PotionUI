/**
 * Core + plugin-registered mountable Svelte components, keyed by name (e.g.
 * `GenerationHistoryModal`). This is just the components map - the rest of
 * the `window.__potionui` host API surface (form reactions, field/renderer
 * registration, registries) is assembled by `plugin-api/host.ts`.
 */
import { createRawSnippet, unmount } from 'svelte';
import { createClassComponent } from 'svelte/legacy';

export interface MountableComponent {
	mount: (target: HTMLElement, props: Record<string, any>) => any;
	/**
	 * Push new props into a mounted instance.
	 *
	 * Core form fields are CONTROLLED: they derive their display from `value`
	 * and report edits through `onChange`, expecting the parent to write the
	 * new value back. Without an update path a host-mounted field's `value`
	 * is frozen at its initial prop, so its own reactive blocks clear the
	 * selection the user just made and the field looks unresponsive. Mounting
	 * through `createClassComponent` (rather than bare `mount`) is what makes
	 * this possible.
	 */
	update: (instance: any, props: Record<string, any>) => void;
	unmount: (instance: any) => void;
}

export type MountableComponentRegistry = Record<string, MountableComponent>;

const registry: MountableComponentRegistry = {};

export type HostSlotContent = string | ((el: HTMLElement) => void | (() => void));

export interface HostSlot {
	slot: HostSlotContent;
}

function isHostSlot(value: unknown): value is HostSlot {
	return typeof value === 'object' && value !== null && 'slot' in value;
}

function toSnippet(content: HostSlotContent) {
	return createRawSnippet(() => ({
		render: () => '<span style="display:contents"></span>',
		setup: (el: Element) => {
			if (typeof content === 'string') {
				el.textContent = content;
				return;
			}
			return content(el as HTMLElement);
		}
	}));
}

export function toHostProps(props: Record<string, any>): Record<string, any> {
	const out: Record<string, any> = {};
	for (const [key, value] of Object.entries(props)) {
		if (key === 'children' && (typeof value === 'string' || typeof value === 'function')) {
			out[key] = toSnippet(value);
			out.$$slots = { default: true };
		} else if (isHostSlot(value)) {
			out[key] = toSnippet(value.slot);
		} else {
			out[key] = value;
		}
	}
	return out;
}

function entryFor(component: any): MountableComponent {
	return {
		mount: (target, props) => createClassComponent({ component, target, props: toHostProps(props) }),
		update: (instance, props) => instance?.$set?.(toHostProps(props)),
		unmount: (instance) => {
			if (typeof instance?.$destroy === 'function') instance.$destroy();
			else unmount(instance);
		}
	};
}

export function registerComponent(name: string, component: any) {
	registry[name] = entryFor(component);
}

interface LazyHandle {
	inner: any;
	props: Record<string, any>;
	destroyed: boolean;
}

export function registerLazyComponent(name: string, load: () => Promise<{ default: any }>) {
	let real: MountableComponent | null = null;
	let loading: Promise<MountableComponent> | null = null;
	const resolve = () => {
		loading ??= load().then(
			(module) => (real = entryFor(module.default)),
			(error) => {
				loading = null;
				throw error;
			}
		);
		return loading;
	};

	registry[name] = {
		mount: (target, props) => {
			const handle: LazyHandle = { inner: null, props, destroyed: false };
			resolve()
				.then((component) => {
					if (!handle.destroyed) handle.inner = component.mount(target, handle.props);
				})
				.catch((error) => console.error(`Failed to load host component '${name}'`, error));
			return handle;
		},
		update: (handle: LazyHandle, props) => {
			if (!handle) return;
			handle.props = { ...handle.props, ...props };
			if (handle.inner) real?.update(handle.inner, props);
		},
		unmount: (handle: LazyHandle) => {
			if (!handle) return;
			handle.destroyed = true;
			if (handle.inner) real?.unmount(handle.inner);
			handle.inner = null;
		}
	};
}

export function getRegistry(): MountableComponentRegistry {
	return registry;
}
