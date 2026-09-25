<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import type { TocSection } from './runReportToc';

	let {
		sections,
		activeId,
		onSelect
	}: {
		sections: readonly TocSection[];
		activeId: string;
		onSelect: (id: string) => void;
	} = $props();
</script>

<div class="sticky top-0">
	<DetailSection label="Sections" padded={false}>
		<nav aria-label="Report sections" class="p-2">
			{#each sections as section (section.id)}
				<button
					type="button"
					class="block w-full truncate rounded px-2 py-1.5 text-left text-xs {section.id === activeId
						? 'bg-signal/10 text-signal font-medium'
						: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
					aria-current={section.id === activeId ? 'true' : undefined}
					onclick={() => onSelect(section.id)}
				>
					{section.label}
				</button>
			{/each}
		</nav>
	</DetailSection>
</div>
