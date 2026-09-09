<script lang="ts">
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import { parseComponentRef } from '$lib/plugin-api/componentRef';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
	import { toasts } from '$lib/stores/toast';
	import type { HistoryTool, HistoryToolContext } from '$lib/history/tools';

	// The single mount point for whichever tool the Tools menu picked. The
	// context is a snapshot taken when the tool was picked, so `onDone`
	// clearing the selection cannot empty it underneath a live modal.
	let {
		tool,
		context,
		onClose,
		onDone
	}: {
		tool: HistoryTool | null;
		context: HistoryToolContext | null;
		onClose: () => void;
		onDone: () => void;
	} = $props();

	let CoreModal = $derived(tool?.component ?? null);

	let pluginModal = $derived.by(() => {
		if (!tool || tool.component) return null;
		const ref = parseComponentRef(tool.componentRef);
		if (!ref) return null;
		const label = tool.label;
		return resolvePluginComponent(ref.pluginId, ref.asset).then((component) => {
			if (!component) toasts.error(`Could not load the plugin component for ${label}`);
			return component;
		});
	});
</script>

{#if tool && context}
	{#if CoreModal}
		<CoreModal {context} {onClose} {onDone} />
	{:else if pluginModal}
		<BaseModal isOpen={true} title={tool.label} size="lg" on:close={onClose}>
			<div class="p-4 md:p-6">
				{#await pluginModal}
					<div class="flex items-center justify-center py-8"><Spinner size="sm" /></div>
				{:then PluginModal}
					{#if PluginModal}
						<PluginModal
							generationIds={context.generationIds}
							generations={context.generations}
							{onClose}
							{onDone}
						/>
					{:else}
						<p class="text-sm text-danger">
							Could not load the plugin component for {tool.label}.
						</p>
					{/if}
				{/await}
			</div>
		</BaseModal>
	{/if}
{/if}
