<script lang="ts">
	import { sidebarWidgets, type SidebarWidget } from '$lib/stores/plugins';
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy } from 'svelte';
	import { getWsUrl } from '$lib/services/wsUrl';

	// Props
	export let position: string;

	// Loaded widget modules keyed by widget_id
	let loadedModules: Record<string, { mountPlugin: (target: HTMLElement, props: any) => any; unmountPlugin: (instance: any) => void }> = {};
	// Active mounted instances keyed by widget_id
	let mountedInstances: Record<string, any> = {};
	// Container DOM refs keyed by widget_id
	let containerRefs: Record<string, HTMLElement> = {};

	// Filter and sort widgets matching the requested position
	$: filteredWidgets = $sidebarWidgets
		.filter((w) => w.position === position)
		.sort((a, b) => a.order - b.order);

	// Reactive: when filteredWidgets changes, reconcile mounts
	$: if (filteredWidgets) {
		reconcileWidgets(filteredWidgets);
	}

	async function loadWidgetModule(widget: SidebarWidget) {
		const moduleUrl = `${api.getBaseURL()}/api/plugins/${widget.plugin_id}/assets/${widget.component}`;

		try {
			const module = await import(/* @vite-ignore */ moduleUrl);

			if (typeof module.mountPlugin !== 'function' || typeof module.unmountPlugin !== 'function') {
				logger.error(
					`[SidebarWidgets] Widget module for ${widget.widget_id} is missing mountPlugin or unmountPlugin exports`
				);
				return null;
			}

			return {
				mountPlugin: module.mountPlugin as (target: HTMLElement, props: any) => any,
				unmountPlugin: module.unmountPlugin as (instance: any) => void
			};
		} catch (err) {
			logger.error(`[SidebarWidgets] Failed to load module for widget ${widget.widget_id}:`, err);
			return null;
		}
	}

	function mountWidget(widget: SidebarWidget, mod: { mountPlugin: (target: HTMLElement, props: any) => any; unmountPlugin: (instance: any) => void }) {
		const container = containerRefs[widget.widget_id];
		if (!container) {
			return;
		}

		try {
			const instance = mod.mountPlugin(container, {
				context: {
					apiBaseUrl: api.getBaseURL(),
					token: api.getToken(),
					wsUrl: getWsUrl
				}
			});
			mountedInstances[widget.widget_id] = instance;
		} catch (err) {
			logger.error(`[SidebarWidgets] Failed to mount widget ${widget.widget_id}:`, err);
		}
	}

	function unmountWidget(widgetId: string) {
		const instance = mountedInstances[widgetId];
		const mod = loadedModules[widgetId];

		if (instance && mod) {
			try {
				mod.unmountPlugin(instance);
			} catch (err) {
				logger.error(`[SidebarWidgets] Failed to unmount widget ${widgetId}:`, err);
			}
		}

		delete mountedInstances[widgetId];
	}

	async function reconcileWidgets(widgets: SidebarWidget[]) {
		const currentIds = new Set(widgets.map((w) => w.widget_id));

		// Unmount and remove widgets that are no longer in the filtered list
		for (const widgetId of Object.keys(mountedInstances)) {
			if (!currentIds.has(widgetId)) {
				unmountWidget(widgetId);
				delete loadedModules[widgetId];
			}
		}

		// Load and mount new widgets
		for (const widget of widgets) {
			if (!loadedModules[widget.widget_id]) {
				const mod = await loadWidgetModule(widget);
				if (mod) {
					loadedModules[widget.widget_id] = mod;
					// Mount after the DOM has updated with the new container
					// Use a tick-like approach: schedule after reactive DOM update
					scheduleMount(widget, mod);
				}
			}
		}
	}

	// Schedule a mount after the DOM updates for a newly added widget
	function scheduleMount(widget: SidebarWidget, mod: { mountPlugin: (target: HTMLElement, props: any) => any; unmountPlugin: (instance: any) => void }) {
		// Use requestAnimationFrame to wait for Svelte to commit the DOM
		requestAnimationFrame(() => {
			if (!mountedInstances[widget.widget_id]) {
				mountWidget(widget, mod);
			}
		});
	}

	// Svelte action: binds and mounts a widget when its container element is added to the DOM
	function widgetMount(node: HTMLElement, widget: SidebarWidget) {
		containerRefs[widget.widget_id] = node;

		const mod = loadedModules[widget.widget_id];
		if (mod && !mountedInstances[widget.widget_id]) {
			mountWidget(widget, mod);
		}

		return {
			update(newWidget: SidebarWidget) {
				containerRefs[newWidget.widget_id] = node;
			},
			destroy() {
				unmountWidget(widget.widget_id);
				delete containerRefs[widget.widget_id];
			}
		};
	}

	onMount(async () => {
		// Initial load is handled reactively via the $: filteredWidgets block
	});

	onDestroy(() => {
		// Unmount all active widget instances
		for (const widgetId of Object.keys(mountedInstances)) {
			unmountWidget(widgetId);
		}
	});
</script>

{#if filteredWidgets.length > 0}
	<div class="flex flex-col items-center gap-1.5">
		{#each filteredWidgets as widget (widget.widget_id)}
			<div class="widget-container" use:widgetMount={widget}></div>
		{/each}
	</div>
{/if}
