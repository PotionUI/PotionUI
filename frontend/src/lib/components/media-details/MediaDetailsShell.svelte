<script lang="ts">
	import type { Snippet } from 'svelte';
	import portal from '$lib/actions/portal';
	import { restoreScroll } from '$lib/utils/restoreScroll';
	import { Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';

	let {
		isOpen,
		title,
		headerIconName = 'image',
		onClose,
		onKeyDown,
		customHeader = false,
		iconBox,
		loading = false,
		ready = true,
		scrollRestore,
		root = $bindable(),
		column = $bindable(),
		headerRight,
		toolbar,
		stage,
		columnContent
	}: {
		isOpen: boolean;
		title: string;
		headerIconName?: string;
		onClose: () => void;
		onKeyDown?: (event: KeyboardEvent) => void;
		customHeader?: boolean;
		iconBox?: Snippet;
		loading?: boolean;
		ready?: boolean;
		scrollRestore?: () => number | null;
		root?: HTMLElement | undefined;
		column?: HTMLElement | undefined;
		headerRight?: Snippet;
		toolbar?: Snippet;
		stage?: Snippet;
		columnContent?: Snippet;
	} = $props();
</script>

<svelte:window onkeydowncapture={onKeyDown} />

<div use:portal>
	<BaseModal {isOpen} {title} hideTitle={customHeader} closeClass="max-md:!min-h-10 max-md:!min-w-10" sizeClass="md:w-[85vw] md:h-[85vh]" on:close={onClose}>
		<svelte:fragment slot="headerIcon">
			{#if iconBox}
				{@render iconBox()}
			{:else}
				<div class="w-[30px] h-[30px] rounded bg-surface-2 border border-line flex items-center justify-center flex-shrink-0">
					<Icon name={headerIconName} className="w-4 h-4 text-fg-muted" />
				</div>
			{/if}
		</svelte:fragment>
		<svelte:fragment slot="header">
			{@render headerRight?.()}
		</svelte:fragment>

		{#if loading}
			<div class="flex-1 flex items-center justify-center h-full">
				<div class="flex flex-col items-center gap-3">
					<Spinner size="lg" />
					<p class="text-sm text-fg-muted">Loading...</p>
				</div>
			</div>
		{:else if ready}
			<div class="flex flex-col md:flex-row h-full min-h-0 outline-none" bind:this={root} data-details-window tabindex="0" data-autofocus>
				<div class="group relative flex min-h-0 min-w-0 flex-1 flex-col">
					{@render toolbar?.()}
					<div data-details-stage class="relative min-h-0 flex-1 overflow-hidden bg-black">
						{@render stage?.()}
					</div>
				</div>
				<div bind:this={column} data-details-info class="w-full md:w-[400px] shrink-0 max-h-[45vh] md:max-h-none border-t md:border-t-0 md:border-l border-line bg-surface-1 overflow-y-auto min-h-0" use:restoreScroll={scrollRestore?.() ?? null}>
					{@render columnContent?.()}
				</div>
			</div>
		{/if}
	</BaseModal>
</div>
