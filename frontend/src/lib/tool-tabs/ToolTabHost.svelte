<script lang="ts">
	import type { ToolTab } from '$lib/types/tabs';
	import { EmptyState, Spinner } from '$lib/components/ui';
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
		{#key tab.epoch ?? 0}
			<Tool {host} />
		{/key}
	{:else if failed || (!kind && ($extensionRegistrations > 0 || !tab.tool.includes('.')))}
		<div class="flex flex-1 items-center justify-center p-6" data-testid="tool-tab-unavailable">
			<EmptyState icon="extension" title="This tool isn't available" description="It was turned off or removed. The tab keeps its edits until you close it." />
		</div>
	{:else}
		<div class="flex flex-1 items-center justify-center">
			<Spinner />
		</div>
	{/if}
</section>
