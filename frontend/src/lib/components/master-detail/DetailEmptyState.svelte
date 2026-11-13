<script lang="ts">
	import { EmptyState } from '$lib/components/ui';

	export let message: string = 'Select an item to view details';
	export let icon: 'document' | 'folder' | 'command' | 'style' = 'document';

	const iconNames = { document: 'document', folder: 'folder', command: 'code', style: 'layers' } as const;
</script>

<div class="flex items-center justify-center h-full">
	<EmptyState icon={iconNames[icon]} title={message} description="Use the arrow keys to navigate." />
</div>
