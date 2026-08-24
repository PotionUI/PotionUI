<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import type { GenerationLayoutMode } from '$lib/stores/generationLayout';

	export let value: GenerationLayoutMode = 'two';
	export let onChange: (mode: GenerationLayoutMode) => void;
	// When true, render just the option grid (no trigger button, no
	// floating chrome) - used by TabsOverflowMenu, which supplies its own
	// trigger and panel and hosts this inline.
	export let embedded = false;

	let open = false;
	let root: HTMLDivElement;
	const options: Array<{
		value: GenerationLayoutMode;
		label: string;
		description: string;
	}> = [
		{
			value: 'two',
			label: 'Two panes',
			description: 'Media and controls share a balanced workspace.'
		},
		{
			value: 'three',
			label: 'Three panes',
			description: 'Keep prompts visible beside media and settings.'
		}
	];

	$: selected = options.find((option) => option.value === value) ?? options[0];

	function choose(mode: GenerationLayoutMode) {
		onChange(mode);
		open = false;
	}

	function handleWindowClick(event: MouseEvent) {
		if (open && root && !root.contains(event.target as Node)) open = false;
	}
</script>

{#snippet layoutOptions()}
	<div class="px-1 pb-3">
		<p class="label mb-1">View layout</p>
		<p class="text-xs text-fg-subtle">This choice is saved with the current session.</p>
	</div>
	<div class="grid gap-2 sm:grid-cols-2">
		{#each options as option}
			<button
				type="button"
				class="rounded-lg border p-3 text-left transition-colors {value === option.value ? 'border-signal bg-signal/10' : 'border-line-strong bg-surface-2 hover:border-line-hover hover:bg-surface-3'}"
				on:click={() => choose(option.value)}
				role="option"
				aria-selected={value === option.value}
			>
				<div class="mb-3 flex h-16 gap-1 rounded border border-line bg-surface-1 p-1.5" aria-hidden="true">
					{#if option.value === 'two'}
						<span class="flex-[1.5] rounded-sm bg-signal/20"></span>
						<span class="flex-1 rounded-sm bg-surface-3"></span>
					{:else}
						<span class="flex-1 rounded-sm bg-surface-3"></span>
						<span class="flex-[1.35] rounded-sm bg-signal/20"></span>
						<span class="flex-1 rounded-sm bg-surface-3"></span>
					{/if}
				</div>
				<span class="flex items-center justify-between gap-2 text-sm font-medium {value === option.value ? 'text-signal' : 'text-fg'}">
					{option.label}
					{#if value === option.value}<Icon name="check" className="w-4 h-4" />{/if}
				</span>
				<span class="mt-1 block text-xs leading-relaxed text-fg-subtle">{option.description}</span>
			</button>
		{/each}
	</div>
{/snippet}

<svelte:window on:click={handleWindowClick} />

{#if embedded}
	{@render layoutOptions()}
{:else}
	<div class="relative" bind:this={root}>
		<button
			type="button"
			class="inline-flex h-10 items-center gap-2 rounded border border-line-strong bg-surface-2 px-3 text-sm text-fg-muted transition-colors hover:border-line-hover hover:bg-surface-3 hover:text-fg"
			on:click={() => (open = !open)}
			aria-haspopup="listbox"
			aria-expanded={open}
			aria-label="Choose workspace layout"
		>
			<Icon name="layout-template" className="w-4 h-4 text-fg-subtle" />
			<span class="hidden sm:inline">{selected.label}</span>
			<span class="sm:hidden">{value === 'two' ? '2 panes' : '3 panes'}</span>
			<Icon name="chevron-down" className="w-3.5 h-3.5 text-fg-subtle transition-transform {open ? 'rotate-180' : ''}" />
		</button>

		{#if open}
			<div
				class="absolute right-0 top-full z-50 mt-1 w-[min(24rem,calc(100vw-2rem))] rounded-xl border border-line-strong bg-surface-1 p-3 shadow-floating"
				role="listbox"
				aria-label="Workspace layout"
			>
				{@render layoutOptions()}
			</div>
		{/if}
	</div>
{/if}
