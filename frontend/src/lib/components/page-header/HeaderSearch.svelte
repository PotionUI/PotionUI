<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import tooltip from '$lib/actions/tooltip';

	let {
		value,
		placeholder,
		label,
		oninput,
		modifiers,
		inputEl = $bindable(),
		error = null,
		onclear,
		onkeydown,
		inputAttrs,
		narrow = false
	}: {
		value: string;
		placeholder: string;
		label?: string;
		oninput: (value: string) => void;
		modifiers?: Snippet;
		inputEl?: HTMLInputElement;
		error?: string | null;
		onclear?: () => void;
		onkeydown?: (event: KeyboardEvent) => void;
		inputAttrs?: Record<string, string | number | boolean | undefined>;
		narrow?: boolean;
	} = $props();
</script>

<div
	class="flex h-8 min-w-[160px] items-center gap-2 rounded border {error ? 'border-danger' : 'border-line-strong'} bg-surface-2/50 pl-2.5 pr-1 transition-colors focus-within:ring-2 focus-within:ring-signal {narrow
		? 'flex-1'
		: 'max-w-[420px] flex-[1_1_360px]'}"
	data-header-search
>
	<Icon name="search" className="h-3.5 w-3.5 flex-none text-fg-subtle" />
	<input
		bind:this={inputEl}
		type="text"
		class="min-w-0 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-fg-subtle"
		{placeholder}
		aria-invalid={error ? 'true' : undefined}
		aria-label={label ?? placeholder}
		{value}
		oninput={(event) => oninput(event.currentTarget.value)}
		{onkeydown}
		{...inputAttrs}
	/>
	{#if error}
		<span class="inline-flex h-6 w-6 flex-none items-center justify-center text-danger" use:tooltip={error} aria-label={error}>
			<Icon name="warning" className="h-3.5 w-3.5" />
		</span>
	{/if}
	{#if value}
		<button
			type="button"
			class="inline-flex h-6 w-6 flex-none items-center justify-center rounded-sm text-fg-subtle transition-colors hover:bg-surface-3/50 hover:text-fg"
			aria-label="Clear search"
			use:tooltip={'Clear search'}
			onclick={() => {
				if (onclear) onclear();
				else oninput('');
				inputEl?.focus();
			}}
		>
			<Icon name="close" className="h-3.5 w-3.5" />
		</button>
	{/if}
	{#if modifiers}
		<div class="flex flex-none items-center gap-0.5">
			{@render modifiers()}
		</div>
	{/if}
</div>
