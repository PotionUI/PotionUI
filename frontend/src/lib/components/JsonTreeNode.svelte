<script lang="ts">
	import { createEventDispatcher } from 'svelte';

	export let data: any;
	export let path: string;
	export let keyName: string;
	export let expandedPaths: Set<string>;
	export let matchingPaths: Set<string>;
	export let searchQuery: string;
	export let togglePath: (path: string) => void;
	export let isRoot: boolean = false;

	function getValueColor(value: any): string {
		if (value === null) return 'text-fg-subtle';
		if (value === undefined) return 'text-fg-subtle';
		if (typeof value === 'string') return 'text-green-400';
		if (typeof value === 'number') return 'text-info';
		if (typeof value === 'boolean') return 'text-fg-muted';
		return 'text-fg-muted';
	}

	function formatValue(value: any): string {
		if (value === null) return 'null';
		if (value === undefined) return 'undefined';
		if (typeof value === 'string') return `"${value}"`;
		return String(value);
	}

	function isExpandable(value: any): boolean {
		return value !== null && typeof value === 'object';
	}

	function getPreview(value: any): string {
		if (Array.isArray(value)) {
			return `Array(${value.length})`;
		}
		if (typeof value === 'object' && value !== null) {
			const keys = Object.keys(value);
			if (keys.length <= 3) {
				return `{ ${keys.join(', ')} }`;
			}
			return `{ ${keys.slice(0, 3).join(', ')}, ... }`;
		}
		return '';
	}

	function highlightMatch(text: string, query: string): string {
		if (!query) return text;
		const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
		return text.replace(regex, '<mark class="bg-warning/20 text-white rounded px-0.5">$1</mark>');
	}

	$: isExpanded = expandedPaths.has(path);
	$: isMatch = matchingPaths.has(path);
	$: expandable = isExpandable(data);
</script>

<div class="json-node {isMatch ? 'bg-warning/10 -mx-1 px-1 rounded' : ''}">
	<div
		class="flex items-start gap-1 hover:bg-surface-2/50 rounded py-0.5 {expandable ? 'cursor-pointer' : ''}"
		on:click={() => expandable && togglePath(path)}
		on:keydown={(e) => e.key === 'Enter' && expandable && togglePath(path)}
		role={expandable ? 'button' : 'none'}
		tabindex={expandable ? 0 : -1}
	>
		<!-- Expand/Collapse Icon -->
		{#if expandable}
			<span class="text-fg-subtle w-4 flex-shrink-0 select-none">
				{isExpanded ? '▼' : '▶'}
			</span>
		{:else}
			<span class="w-4 flex-shrink-0"></span>
		{/if}

		<!-- Key -->
		{#if keyName && !isRoot}
			<span class="text-cyan-400">
				{#if searchQuery}
					{@html highlightMatch(keyName, searchQuery)}
				{:else}
					{keyName}
				{/if}
			</span>
			<span class="text-fg-subtle">:</span>
		{/if}

		<!-- Value or Preview -->
		{#if expandable}
			<span class="text-fg-subtle text-xs ml-1">
				{getPreview(data)}
			</span>
		{:else}
			<span class={getValueColor(data)}>
				{#if searchQuery && typeof data === 'string'}
					{@html highlightMatch(formatValue(data), searchQuery)}
				{:else}
					{formatValue(data)}
				{/if}
			</span>
		{/if}
	</div>

	<!-- Children -->
	{#if expandable && isExpanded}
		<div class="ml-4 border-l border-line/50 pl-2">
			{#if Array.isArray(data)}
				{#each data as item, index}
					<svelte:self
						data={item}
						path={`${path}[${index}]`}
						keyName={String(index)}
						{expandedPaths}
						{matchingPaths}
						{searchQuery}
						{togglePath}
					/>
				{/each}
			{:else}
				{#each Object.entries(data) as [key, value]}
					<svelte:self
						data={value}
						path={path === 'root' ? key : `${path}.${key}`}
						keyName={key}
						{expandedPaths}
						{matchingPaths}
						{searchQuery}
						{togglePath}
					/>
				{/each}
			{/if}
		</div>
	{/if}
</div>

<style>
	.json-node {
		line-height: 1.4;
	}
</style>
