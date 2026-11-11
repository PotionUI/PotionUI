<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Menu from '$lib/components/ui/Menu.svelte';
	import type { ApiKey } from '$lib/services/api/apiKeys';

	let {
		keys,
		selectedId,
		onSelect
	}: {
		keys: ApiKey[];
		selectedId: string | null;
		onSelect: (id: string | null) => void;
	} = $props();

	const current = $derived(keys.find((key) => key.id === selectedId) ?? null);
</script>

<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5">
	<span class="text-sm text-fg-subtle">Applies to</span>
	<Menu label="Choose what this list applies to" width={380} align="left" gap={6}>
		{#snippet trigger({ open, toggle })}
			<button
				type="button"
				aria-haspopup="menu"
				aria-expanded={open}
				class="flex min-h-9 min-w-0 max-w-full items-center gap-2 rounded border border-field-border bg-field-bg px-3 text-sm text-fg transition-colors hover:border-line-hover"
				onclick={toggle}
			>
				{#if current}
					<Icon name="key" className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
					<span class="truncate font-medium">{current.name}</span>
					<span class="hidden flex-shrink-0 font-mono tabular-nums text-fg-subtle sm:inline">{current.display_prefix}…</span>
				{:else}
					<span class="font-medium">Default for all keys</span>
				{/if}
				<Icon name="chevron-down" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
			</button>
		{/snippet}
		{#snippet children({ close })}
			<button
				type="button"
				role="menuitemradio"
				aria-checked={!current}
				class="flex w-full items-center gap-2.5 rounded px-2.5 py-2 text-left text-sm hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none"
				onclick={() => {
					close(true);
					onSelect(null);
				}}
			>
				<span class="flex h-4 w-4 flex-shrink-0 items-center justify-center text-signal">
					{#if !current}<Icon name="check" className="h-4 w-4" />{/if}
				</span>
				<span class="min-w-0 flex-1">
					<span class="block font-medium text-fg">Default for all keys</span>
					<span class="block text-fg-subtle">Applies to every key unless it narrows it</span>
				</span>
			</button>
			<div class="my-1 h-px bg-line" role="separator"></div>
			<p class="px-2.5 pb-1 pt-1.5 font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">Keys with MCP</p>
			{#each keys as key (key.id)}
				<button
					type="button"
					role="menuitemradio"
					aria-checked={current?.id === key.id}
					class="flex w-full items-center gap-2.5 rounded px-2.5 py-2 text-left text-sm hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none"
					onclick={() => {
						close(true);
						onSelect(key.id);
					}}
				>
					<span class="flex h-4 w-4 flex-shrink-0 items-center justify-center text-signal">
						{#if current?.id === key.id}<Icon name="check" className="h-4 w-4" />{/if}
					</span>
					<span class="min-w-0 flex-1">
						<span class="block truncate font-medium text-fg">{key.name}</span>
						<span class="block font-mono tabular-nums text-fg-subtle">{key.display_prefix}…{key.last_four ?? ''}</span>
					</span>
				</button>
			{/each}
		{/snippet}
	</Menu>
</div>
