<script lang="ts" generics="T extends { id: string; name: string; color: string }">
	import { onMount } from 'svelte';

	// The "+N" trigger and searchable popover for the tags that don't fit in a
	// `QuickTagFilterBar` row. Extracted from the history tags bar so the
	// library's tags bar is the same control rather than a second copy of it.
	export let tags: T[];
	export let selectedIds: string[];
	export let onToggle: (tagId: string) => void;
	export let title = 'More tags';

	let isOpen = false;
	let searchValue = '';
	let container: HTMLDivElement;
	let trigger: HTMLButtonElement;
	let searchInput: HTMLInputElement;
	let popoverPos = { top: 0, left: 0 };

	onMount(() => {
		document.addEventListener('click', handleClickOutside);
		document.addEventListener('keydown', handleKeydown);
		return () => {
			document.removeEventListener('click', handleClickOutside);
			document.removeEventListener('keydown', handleKeydown);
		};
	});

	$: filteredTags = tags.filter((tag) =>
		tag.name.toLowerCase().includes(searchValue.toLowerCase())
	);
	$: selectedCount = tags.filter((tag) => selectedIds.includes(tag.id)).length;

	function toggleDropdown() {
		if (!isOpen && trigger) {
			const rect = trigger.getBoundingClientRect();
			popoverPos = { top: rect.bottom + 4, left: rect.left };
		}
		isOpen = !isOpen;
		if (isOpen) {
			setTimeout(() => searchInput?.focus(), 50);
		} else {
			searchValue = '';
		}
	}

	function close() {
		if (isOpen) {
			isOpen = false;
			searchValue = '';
		}
	}

	function handleClickOutside(event: MouseEvent) {
		if (isOpen && container && !container.contains(event.target as Node)) close();
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && isOpen) close();
	}
</script>

{#if tags.length > 0}
	<div bind:this={container} class="flex-shrink-0">
		<button
			bind:this={trigger}
			type="button"
			class="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-all {selectedCount > 0
				? 'bg-signal/10 text-signal ring-1 ring-signal/30'
				: 'text-fg-subtle hover:text-fg-muted hover:bg-surface-3/40'}"
			on:click|stopPropagation={toggleDropdown}
			aria-expanded={isOpen}
			aria-haspopup="listbox"
			{title}
		>
			+{tags.length}{#if selectedCount > 0}&nbsp;·&nbsp;{selectedCount}{/if}
		</button>
		{#if isOpen}
			<div
				class="fixed z-50 w-64 max-h-80 bg-surface-1 border border-line-strong rounded-lg shadow-overlay flex flex-col"
				style="top: {popoverPos.top}px; left: {popoverPos.left}px;"
				role="dialog"
			>
				<div class="p-2 border-b border-line-strong/70">
					<input
						bind:this={searchInput}
						bind:value={searchValue}
						type="text"
						placeholder="Search tags..."
						class="w-full px-2 py-1.5 text-xs bg-surface-2 border border-line-strong text-fg placeholder-fg-subtle rounded-md focus:outline-none focus:ring-1 focus:ring-signal"
					/>
				</div>
				<div class="overflow-y-auto flex-1 p-2 flex flex-wrap gap-1.5" role="listbox">
					{#if filteredTags.length === 0}
						<div class="w-full px-2 py-4 text-center text-xs text-fg-subtle">No tags found</div>
					{:else}
						{#each filteredTags as tag (tag.id)}
							<button
								type="button"
								class="inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs font-medium transition-all"
								style="{selectedIds.includes(tag.id)
									? `background-color: ${tag.color}20; color: ${tag.color}; box-shadow: 0 0 0 1px ${tag.color}40;`
									: 'color: rgb(var(--fg-subtle));'}"
								on:click={() => onToggle(tag.id)}
								on:mouseenter={(e) => {
									if (!selectedIds.includes(tag.id)) e.currentTarget.style.color = tag.color;
								}}
								on:mouseleave={(e) => {
									if (!selectedIds.includes(tag.id))
										e.currentTarget.style.color = 'rgb(var(--fg-subtle))';
								}}
								role="option"
								aria-selected={selectedIds.includes(tag.id)}
							>
								<span class="w-2 h-2 rounded-full flex-shrink-0" style="background-color: {tag.color}"></span>
								{tag.name}
							</button>
						{/each}
					{/if}
				</div>
			</div>
		{/if}
	</div>
{/if}
