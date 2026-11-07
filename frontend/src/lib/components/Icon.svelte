<script lang="ts">
	import { getFilledIconPaths, iconLookup } from '../utils/IconLibrary';

	export let name: string;
	export let className: string = 'w-5 h-5';
	export let strokeWidth: number = 2;

	$: iconPath = $iconLookup(name);
	$: filledPaths = getFilledIconPaths(name);
</script>

{#if iconPath}
	<svg class={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
		{#if Array.isArray(iconPath)}
			{#each iconPath as path}
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width={strokeWidth} d={path} />
			{/each}
		{:else}
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width={strokeWidth} d={iconPath} />
		{/if}
		{#each filledPaths as path}
			<path fill="currentColor" stroke="none" d={path} />
		{/each}
	</svg>
{:else}
	<span class="{className} flex items-center justify-center text-xs font-medium">
		{name.charAt(0).toUpperCase()}
	</span>
{/if}
