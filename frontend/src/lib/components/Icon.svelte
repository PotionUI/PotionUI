<script lang="ts">
	import { getIconPath } from '../utils/IconLibrary';

	export let name: string;
	export let className: string = 'w-5 h-5';
	export let strokeWidth: number = 2;

	$: iconPath = getIconPath(name);
</script>

{#if iconPath}
	<svg class={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
		{#if Array.isArray(iconPath)}
			<!-- Multiple paths -->
			{#each iconPath as path}
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width={strokeWidth} d={path} />
			{/each}
		{:else}
			<!-- Single path -->
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width={strokeWidth} d={iconPath} />
		{/if}
	</svg>
{:else}
	<!-- Fallback: show first letter if icon not found -->
	<span class="{className} flex items-center justify-center text-xs font-medium">
		{name.charAt(0).toUpperCase()}
	</span>
{/if}
