<script lang="ts">
	import type { Component } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Spinner } from '$lib/components/ui';
	import { reportFailure } from '$lib/editors/guard';
	import type { ComponentRef } from '$lib/editors/types';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';

	let {
		entry,
		componentRef,
		props = {},
		variant = 'panel',
		onclose
	}: {
		entry: string;
		componentRef: ComponentRef;
		props?: Record<string, unknown>;
		variant?: 'panel' | 'row';
		onclose?: () => void;
	} = $props();

	let loaded = $state.raw<Component<any> | null>(null);
	let loadFailed = $state(false);

	let direct = $derived('component' in componentRef ? componentRef.component : null);
	let Resolved = $derived(direct ?? loaded);

	$effect(() => {
		if ('component' in componentRef) return;
		const { pluginId, path } = componentRef;
		let current = true;
		loaded = null;
		loadFailed = false;
		resolvePluginComponent(pluginId, path).then((component) => {
			if (!current) return;
			if (component) loaded = component;
			else loadFailed = true;
		});
		return () => {
			current = false;
		};
	});

	function onfail(error: unknown) {
		reportFailure(entry, 'render', error);
	}
</script>

<svelte:boundary onerror={onfail}>
	{#if Resolved}
		<Resolved {...props} />
	{:else if loadFailed}
		{@render fallback()}
	{:else}
		<span class="inline-flex justify-center py-2"><Spinner size="sm" /></span>
	{/if}
	{#snippet failed()}
		{@render fallback()}
	{/snippet}
</svelte:boundary>

{#snippet fallback()}
	{#if variant === 'row'}
		<span class="truncate text-sm text-fg-subtle" data-editor-entry-failed>Could not load</span>
	{:else}
		<div class="flex items-center gap-2 rounded bg-surface-2 px-2.5 py-2 text-sm text-fg-muted" role="alert" data-editor-entry-failed>
			<Icon name="info" className="h-3.5 w-3.5 flex-none" />
			<span class="min-w-0 flex-1">This tool could not load</span>
			{#if onclose}
				<Button size="xs" variant="ghost" onclick={onclose}>Close</Button>
			{/if}
		</div>
	{/if}
{/snippet}
