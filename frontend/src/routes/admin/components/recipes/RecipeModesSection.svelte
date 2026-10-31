<script lang="ts">
	import { Badge, Button } from '$lib/components/ui';
	import type { RecipeModeRow } from '$lib/services/api/recipeModes';
	import { formatBytesShort } from '$lib/utils/format';

	let {
		modes,
		disabled = false,
		onInstall,
		onRemove
	}: {
		modes: RecipeModeRow[];
		disabled?: boolean;
		onInstall: (mode: RecipeModeRow) => void;
		onRemove: (mode: RecipeModeRow) => void;
	} = $props();

	function labelOf(id: string): string {
		return modes.find((mode) => mode.id === id)?.label ?? id;
	}
</script>

<div class="rounded-lg border border-line bg-surface-1" data-recipe-modes>
	<div class="border-b border-line px-5 py-3 font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">
		Modes {modes.length}
	</div>
	<ul class="divide-y divide-line">
		{#each modes as mode (mode.id)}
			<li class="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3 max-sm:items-start" data-recipe-mode={mode.id}>
				<div class="min-w-0 flex-1 basis-48">
					<div class="text-base text-fg">{mode.label}</div>
					{#if mode.short_description}
						<div class="text-sm text-fg-muted">{mode.short_description}</div>
					{/if}
				</div>
				{#if mode.ready && mode.size_bytes}
					<span class="font-mono text-sm tabular-nums text-fg-muted">{formatBytesShort(mode.size_bytes)}</span>
				{/if}
				{#if mode.ready}
					<Badge variant="success" dot>Ready</Badge>
				{:else}
					<Badge variant="warning" dot>Not installed</Badge>
				{/if}
				<div class="flex min-w-[8.5rem] justify-end max-sm:min-w-0">
					{#if !mode.ready}
						<Button
							variant="secondary"
							size="sm"
							icon="download"
							{disabled}
							class="font-mono tabular-nums"
							onclick={() => onInstall(mode)}
						>
							{mode.missing_bytes ? `Install · ${formatBytesShort(mode.missing_bytes)}` : 'Install'}
						</Button>
					{:else if mode.removable}
						<Button variant="ghost" size="sm" icon="trash" {disabled} onclick={() => onRemove(mode)}>Remove files</Button>
					{:else if mode.shares_with.length > 0}
						<span class="text-sm text-fg-muted">Shares {labelOf(mode.shares_with[0])} files</span>
					{/if}
				</div>
			</li>
		{/each}
	</ul>
</div>
