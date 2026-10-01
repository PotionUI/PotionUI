<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { IconButton } from '$lib/components/ui';
	import { PHONE_MAX_WIDTH } from './drawerBehavior';

	let {
		value = $bindable(''),
		el = $bindable(undefined),
		placeholder,
		label
	}: {
		value?: string;
		el?: HTMLInputElement;
		placeholder: string;
		label: string;
	} = $props();

	const desktopFocus = typeof window === 'undefined' ? true : window.innerWidth > PHONE_MAX_WIDTH;
</script>

<div class="flex-shrink-0 px-3 pb-2 pt-3">
	<div class="search flex h-9 items-center gap-2 rounded border border-field-border bg-field-bg px-2.5 text-fg-subtle">
		<Icon name="search" className="h-4 w-4 flex-shrink-0" />
		<input
			bind:this={el}
			bind:value
			type="text"
			class="min-w-0 flex-1 border-0 bg-transparent p-0 text-sm text-fg outline-none placeholder:text-fg-subtle focus:ring-0"
			{placeholder}
			aria-label={label}
			data-autofocus={desktopFocus ? '' : undefined}
		/>
		{#if value}
			<Tooltip text="Clear search" position="bottom" delay={150}>
				<IconButton icon="close" label="Clear search" size="xs" onclick={() => { value = ''; el?.focus(); }} />
			</Tooltip>
		{:else}
			<kbd class="rounded-sm border border-line-strong bg-surface-1 px-1.5 font-mono text-xs text-fg-subtle">/</kbd>
		{/if}
	</div>
</div>

<style>
	.search:focus-within {
		border-color: rgb(var(--accent) / 0.6);
	}
</style>
