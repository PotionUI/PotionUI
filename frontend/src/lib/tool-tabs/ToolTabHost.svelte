<script lang="ts">
	import type { ToolTab } from '$lib/types/tabs';
	import { Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { toolKindStore, resolveToolComponent } from './registry';
	import { createToolTabHost } from './host';
	import { setToolTabActions } from './actions';
	import { extensionRegistrations } from '$lib/plugin-api/extensionRefresh';
	import type { ToolKindDefinition, ToolTabComponent } from './types';

	let { tab, phone = false }: { tab: ToolTab; phone?: boolean } = $props();

	let kindStore = $derived(toolKindStore(tab.tool));
	let kind = $derived($kindStore);
	let host = $derived(createToolTabHost(tab, phone));

	let loaded = $state.raw<{ definition: ToolKindDefinition; component: ToolTabComponent | null } | null>(null);
	let failed = $state(false);

	$effect(() => {
		const definition = kind;
		if (!definition) {
			loaded = null;
			return;
		}
		if (loaded?.definition === definition) return;
		let cancelled = false;
		failed = false;
		resolveToolComponent(definition)
			.then((component) => {
				if (cancelled) return;
				loaded = { definition, component };
				failed = !component;
			})
			.catch(() => {
				if (!cancelled) failed = true;
			});
		return () => {
			cancelled = true;
		};
	});

	let component = $derived(loaded && loaded.definition === kind ? loaded.component : null);

	let tabId = $derived(tab.id);

	$effect(() => {
		const id = tabId;
		return () => setToolTabActions(id, null);
	});
</script>

<section
	class="tool-tab-host relative flex h-full min-h-0 w-full flex-col bg-canvas"
	data-testid="tool-tab-host"
	data-tool={tab.tool}
	aria-label={kind?.label ?? 'Tool'}
>
	{#if component}
		{@const Tool = component}
		<Tool {host} />
	{:else if failed || (!kind && ($extensionRegistrations > 0 || !tab.tool.includes('.')))}
		<div class="dot-grid flex flex-1 items-center justify-center p-6">
			<div class="flex max-w-sm flex-col items-center rounded-lg border border-line bg-surface-1 px-8 py-10 text-center" data-testid="tool-tab-unavailable">
				<Icon name="extension" className="mb-3 h-8 w-8 text-fg-subtle" strokeWidth={1.5} />
				<h2 class="text-sm font-semibold text-fg">This tool isn't available</h2>
				<p class="mt-1 text-sm text-fg-muted">It was turned off or removed. The tab keeps its edits until you close it.</p>
			</div>
		</div>
	{:else}
		<div class="flex flex-1 items-center justify-center">
			<Spinner />
		</div>
	{/if}
</section>
