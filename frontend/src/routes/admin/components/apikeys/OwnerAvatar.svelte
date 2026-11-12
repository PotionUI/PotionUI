<script lang="ts">
	import { ownerInitials } from './adminKeysView';

	let { name, size = 'sm' }: { name: string; size?: 'sm' | 'md' } = $props();
</script>

<span
	class="flex flex-shrink-0 items-center justify-center rounded-full bg-surface-3 font-mono text-sm font-semibold text-fg-muted {size === 'md' ? 'h-9 w-9' : 'h-7 w-7'}"
	aria-hidden="true"
>
	{ownerInitials(name)}
</span>
