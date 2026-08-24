<script lang="ts">
	import IconButton from './IconButton.svelte';
	import { pageWindow, PAGE_WINDOW_SLOTS } from '../../utils/pagination';

	type Size = 'sm' | 'md';

	let {
		currentPage,
		totalPages,
		size = 'md',
		slots = PAGE_WINDOW_SLOTS,
		onPageChange,
		class: className = ''
	}: {
		currentPage: number;
		totalPages: number;
		size?: Size;
		slots?: number;
		onPageChange: (page: number) => void;
		class?: string;
	} = $props();

	let pages = $derived(pageWindow(currentPage, totalPages, slots));
	let atFirst = $derived(currentPage <= 1);
	let atLast = $derived(currentPage >= totalPages);

	const numberClasses: Record<Size, string> = {
		sm: 'min-w-8 min-h-8 px-1.5 text-2xs',
		md: 'min-w-10 min-h-10 px-2 text-xs'
	};

	function go(page: number) {
		if (page < 1 || page > totalPages || page === currentPage) return;
		onPageChange(page);
	}
</script>

{#if totalPages > 1}
	<nav aria-label="Pagination" class="flex items-center gap-1 {className}">
		<IconButton
			icon="chevrons-left"
			label="First page"
			{size}
			variant="secondary"
			disabled={atFirst}
			onclick={() => go(1)}
		/>
		<IconButton
			icon="chevron-left"
			label="Previous page"
			{size}
			variant="secondary"
			disabled={atFirst}
			onclick={() => go(currentPage - 1)}
		/>

		{#each pages as slot, i (i)}
			{#if slot === 'ellipsis'}
				<span
					aria-hidden="true"
					class="{numberClasses[
						size
					]} inline-flex items-center justify-center font-mono text-fg-subtle select-none"
				>
					…
				</span>
			{:else}
				<button
					type="button"
					aria-label="Page {slot}"
					aria-current={slot === currentPage ? 'page' : undefined}
					class="{numberClasses[
						size
					]} inline-flex items-center justify-center rounded font-mono tabular-nums transition-colors duration-100 touch-manipulation {slot ===
					currentPage
						? 'bg-signal/10 text-signal'
						: 'text-fg-muted hover:text-fg hover:bg-surface-3/50'}"
					onclick={() => go(slot)}
				>
					{slot}
				</button>
			{/if}
		{/each}

		<IconButton
			icon="chevron-right"
			label="Next page"
			{size}
			variant="secondary"
			disabled={atLast}
			onclick={() => go(currentPage + 1)}
		/>
		<IconButton
			icon="chevrons-right"
			label="Last page"
			{size}
			variant="secondary"
			disabled={atLast}
			onclick={() => go(totalPages)}
		/>
	</nav>
{/if}
