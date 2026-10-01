<script lang="ts">
	import { IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';

	export let label: string;
	export let onClose: () => void;
</script>

<div class="mb-4 rounded-lg border border-line-strong bg-surface-2 p-3" data-testid="picker-preview">
	<div class="flex items-center justify-between gap-2 mb-2">
		<Tooltip text={label} wrapperClass="block min-w-0 max-w-full">
			<p class="text-sm font-medium text-fg truncate">{label}</p>
		</Tooltip>
		<IconButton icon="close" label="Close preview" size="sm" onclick={onClose} />
	</div>
	<div class="flex items-center justify-center max-h-64 overflow-hidden">
		<slot />
	</div>
</div>
