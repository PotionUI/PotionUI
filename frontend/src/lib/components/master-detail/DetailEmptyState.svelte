<script lang="ts">
	import { EmptyState, Kbd } from '$lib/components/ui';

	export let message: string = 'Select an item to view details';
	export let icon: 'document' | 'folder' | 'command' | 'style' = 'document';

	const iconNames = { document: 'document', folder: 'folder', command: 'code', style: 'layers' } as const;
</script>

<EmptyState icon={iconNames[icon]} title={message}>
	{#snippet extra()}
		<span class="inline-flex items-center gap-1">Use <Kbd keys="arrow keys" /> to navigate</span>
	{/snippet}
</EmptyState>
