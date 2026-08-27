<script lang="ts" generics="T extends { id: string; name: string; color: string }">
	import { onMount } from 'svelte';
	import portal from '$lib/actions/portal';
	import { computeAnchoredMenuPosition } from '$lib/utils/menuPosition';

	// The "+N" trigger and searchable popover for the tags that don't fit in a
	// `QuickTagFilterBar` row. Extracted from the history tags bar so the
	// library's tags bar is the same control rather than a second copy of it.
	export let tags: T[];
	export let selectedIds: string[];
	export let onToggle: (tagId: string) => void;
	export let title = 'More tags';

	// A tag without a color (the API doesn't guarantee one) would otherwise
	// splice an alpha suffix onto `undefined`, or leave `background-color: `
	// empty — both invalid CSS the browser drops silently, leaving a fully
	// transparent dot that still reserves its box: a "weird empty space" to
	// the left of the tag name with no visible cause.
	const FALLBACK_TAG_COLOR = 'rgb(var(--fg-subtle))';

	let isOpen = false;
	let searchValue = '';
	let container: HTMLDivElement;
	let trigger: HTMLButtonElement;
	let popoverEl: HTMLDivElement;
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

	// Both tags bars render this inside their page's `sticky` header, which
	// caps any z-index inside it below the generation grid's per-card overlays
	// (GenerationCard.svelte, z-40) — `portal` renders the popover at body
	// level to escape that ancestor stacking context instead.
	function updatePopoverPosition() {
		if (!trigger) return;
		const width = popoverEl?.getBoundingClientRect().width ?? 256;
		popoverPos = computeAnchoredMenuPosition(trigger, { width });
	}

	function toggleDropdown() {
		isOpen = !isOpen;
		if (isOpen) {
			updatePopoverPosition();
			requestAnimationFrame(updatePopoverPosition);
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
		const target = event.target as Node;
		// The popover lives at body level (portalled) once open — only the
		// trigger is still inside `container`.
		if (isOpen && container && !container.contains(target) && !(popoverEl && popoverEl.contains(target))) {
			close();
		}
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && isOpen) close();
	}

	function tagChipStyle(color: string | undefined, selected: boolean): string {
		if (!selected) return 'color: rgb(var(--fg-subtle));';
		if (!color) return 'background-color: rgb(var(--surface-3)); color: rgb(var(--fg)); box-shadow: 0 0 0 1px rgb(var(--line-hover));';
		return `background-color: ${color}20; color: ${color}; box-shadow: 0 0 0 1px ${color}40;`;
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
				use:portal
				bind:this={popoverEl}
				class="fixed z-[9999] w-64 max-h-80 bg-surface-1 border border-line-strong rounded-lg shadow-overlay flex flex-col"
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
								style={tagChipStyle(tag.color, selectedIds.includes(tag.id))}
								on:click={() => onToggle(tag.id)}
								on:mouseenter={(e) => {
									if (!selectedIds.includes(tag.id)) e.currentTarget.style.color = tag.color || FALLBACK_TAG_COLOR;
								}}
								on:mouseleave={(e) => {
									if (!selectedIds.includes(tag.id))
										e.currentTarget.style.color = 'rgb(var(--fg-subtle))';
								}}
								role="option"
								aria-selected={selectedIds.includes(tag.id)}
							>
								<span class="w-2 h-2 rounded-full flex-shrink-0" style="background-color: {tag.color || FALLBACK_TAG_COLOR}"></span>
								{tag.name}
							</button>
						{/each}
					{/if}
				</div>
			</div>
		{/if}
	</div>
{/if}
