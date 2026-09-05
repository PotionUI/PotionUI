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

	function splitHighlight(text: string, query: string): Array<{ text: string; match: boolean }> {
		if (!query) return [{ text, match: false }];
		const regex = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
		const segments: Array<{ text: string; match: boolean }> = [];
		let lastIndex = 0;
		let match: RegExpExecArray | null;
		while ((match = regex.exec(text)) !== null) {
			if (match.index > lastIndex) {
				segments.push({ text: text.slice(lastIndex, match.index), match: false });
			}
			segments.push({ text: match[0], match: true });
			lastIndex = match.index + match[0].length;
		}
		if (lastIndex < text.length) {
			segments.push({ text: text.slice(lastIndex), match: false });
		}
		return segments.length ? segments : [{ text, match: false }];
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
				{#if searchQuery}{#each splitHighlight(keyName, searchQuery) as segment}{#if segment.match}<mark class="bg-warning/20 text-white rounded px-0.5">{segment.text}</mark>{:else}{segment.text}{/if}{/each}{:else}{keyName}{/if}
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
				{#if searchQuery && typeof data === 'string'}{#each splitHighlight(formatValue(data), searchQuery) as segment}{#if segment.match}<mark class="bg-warning/20 text-white rounded px-0.5">{segment.text}</mark>{:else}{segment.text}{/if}{/each}{:else}{formatValue(data)}{/if}
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
