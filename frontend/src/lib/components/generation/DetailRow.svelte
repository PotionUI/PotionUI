<script lang="ts">
	import type { Snippet } from 'svelte';
	import { CopyButton } from '$lib/components/ui';

	let {
		label,
		copy,
		copyLabel,
		mono = true,
		muted = false,
		labelExtra,
		children
	}: {
		label: string;
		copy?: string;
		copyLabel?: string;
		mono?: boolean;
		muted?: boolean;
		labelExtra?: Snippet;
		children?: Snippet;
	} = $props();
</script>

<div class="kv group">
	<dt class="flex min-w-0 items-center text-sm text-fg-muted">
		<span>{label}</span>
		{@render labelExtra?.()}
	</dt>
	<dd
		class="m-0 min-w-0 [overflow-wrap:anywhere] {mono ? 'font-mono text-base tabular-nums' : 'text-md'} {muted
			? 'text-fg-muted'
			: 'text-fg'}"
	>
		{@render children?.()}
	</dd>
	{#if copy !== undefined}
		<CopyButton
			text={copy}
			ariaLabel={copyLabel ?? `Copy ${label}`}
			size="xs"
			class="opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
		/>
	{/if}
</div>

<style>
	.kv {
		display: grid;
		grid-template-columns: 7rem 1fr auto;
		align-items: center;
		column-gap: 8px;
		min-height: 32px;
		break-inside: avoid;
	}
</style>
