<script lang="ts">
	import { onMount } from 'svelte';
	import JsonTreeNode from './JsonTreeNode.svelte';

	export let data: any;
	export let initialExpandLevel: number = 2;
	export let maxHeight: string = '500px';

	let searchQuery: string = '';
	let expandedPaths: Set<string> = new Set();
	let matchingPaths: Set<string> = new Set();
	let allPaths: string[] = [];

	// Collect all paths at initialization
	function collectPaths(obj: any, path: string = ''): void {
		if (obj === null || obj === undefined) return;

		if (typeof obj === 'object') {
			if (Array.isArray(obj)) {
				obj.forEach((item, index) => {
					const newPath = path ? `${path}[${index}]` : `[${index}]`;
					allPaths.push(newPath);
					collectPaths(item, newPath);
				});
			} else {
				Object.keys(obj).forEach(key => {
					const newPath = path ? `${path}.${key}` : key;
					allPaths.push(newPath);
					collectPaths(obj[key], newPath);
				});
			}
		}
	}

	// Initialize expansion state based on level
	function initializeExpansion(obj: any, path: string = '', level: number = 0): void {
		if (obj === null || obj === undefined) return;

		if (typeof obj === 'object' && level < initialExpandLevel) {
			if (Array.isArray(obj)) {
				expandedPaths.add(path || 'root');
				obj.forEach((_, index) => {
					const newPath = path ? `${path}[${index}]` : `[${index}]`;
					initializeExpansion(obj[index], newPath, level + 1);
				});
			} else {
				expandedPaths.add(path || 'root');
				Object.keys(obj).forEach(key => {
					const newPath = path ? `${path}.${key}` : key;
					initializeExpansion(obj[key], newPath, level + 1);
				});
			}
		}
	}

	// Search functionality
	function searchInObject(obj: any, query: string, path: string = ''): boolean {
		if (!query) return false;

		const lowerQuery = query.toLowerCase();
		let found = false;

		if (obj === null || obj === undefined) {
			return String(obj).toLowerCase().includes(lowerQuery);
		}

		if (typeof obj === 'object') {
			if (Array.isArray(obj)) {
				obj.forEach((item, index) => {
					const newPath = path ? `${path}[${index}]` : `[${index}]`;
					if (searchInObject(item, query, newPath)) {
						matchingPaths.add(newPath);
						found = true;
					}
				});
			} else {
				Object.entries(obj).forEach(([key, value]) => {
					const newPath = path ? `${path}.${key}` : key;
					// Check if key matches
					if (key.toLowerCase().includes(lowerQuery)) {
						matchingPaths.add(newPath);
						found = true;
					}
					// Check if value matches
					if (searchInObject(value, query, newPath)) {
						matchingPaths.add(newPath);
						found = true;
					}
				});
			}
		} else {
			if (String(obj).toLowerCase().includes(lowerQuery)) {
				return true;
			}
		}

		return found;
	}

	function handleSearch() {
		matchingPaths.clear();
		if (searchQuery) {
			searchInObject(data, searchQuery);
			// Expand all paths that contain matches
			matchingPaths.forEach(path => {
				// Expand parent paths
				const parts = path.split(/\.|\[/);
				let currentPath = '';
				parts.forEach((part, index) => {
					if (index === 0) {
						currentPath = part.replace(']', '');
					} else {
						currentPath += (part.startsWith('[') || part.includes(']'))
							? `[${part.replace(']', '')}]`
							: `.${part}`;
					}
					expandedPaths.add(currentPath);
				});
			});
			expandedPaths.add('root');
		}
		expandedPaths = expandedPaths;
		matchingPaths = matchingPaths;
	}

	function expandAll() {
		allPaths.forEach(p => expandedPaths.add(p));
		expandedPaths.add('root');
		expandedPaths = expandedPaths;
	}

	function collapseAll() {
		expandedPaths.clear();
		expandedPaths = expandedPaths;
	}

	function togglePath(path: string) {
		if (expandedPaths.has(path)) {
			expandedPaths.delete(path);
		} else {
			expandedPaths.add(path);
		}
		expandedPaths = expandedPaths;
	}

	onMount(() => {
		allPaths = [];
		collectPaths(data);
		initializeExpansion(data);
		expandedPaths = expandedPaths;
	});

	$: if (data) {
		allPaths = [];
		collectPaths(data);
		initializeExpansion(data);
		expandedPaths = expandedPaths;
	}
</script>

<div class="json-tree-view bg-surface-1 rounded-lg border border-line">
	<!-- Toolbar -->
	<div class="flex items-center gap-2 p-2 border-b border-line bg-surface-2/50">
		<div class="relative flex-1">
			<input
				type="text"
				bind:value={searchQuery}
				on:input={handleSearch}
				placeholder="Search keys or values..."
				class="w-full px-3 py-1.5 text-sm bg-surface-2 border border-line-strong rounded text-fg placeholder-fg-subtle focus:outline-none focus:border-accent"
			/>
			{#if searchQuery}
				<button
					on:click={() => { searchQuery = ''; matchingPaths.clear(); matchingPaths = matchingPaths; }}
					class="absolute right-2 top-1/2 -translate-y-1/2 text-fg-muted hover:text-fg"
				>
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			{/if}
		</div>
		<button
			on:click={expandAll}
			class="px-2 py-1 text-xs bg-surface-3 hover:bg-line-hover text-fg-muted rounded transition-colors"
		>
			Expand All
		</button>
		<button
			on:click={collapseAll}
			class="px-2 py-1 text-xs bg-surface-3 hover:bg-line-hover text-fg-muted rounded transition-colors"
		>
			Collapse All
		</button>
	</div>

	<!-- Tree Content -->
	<div class="overflow-auto p-3 font-mono text-sm" style="max-height: {maxHeight};">
		<JsonTreeNode
			data={data}
			path="root"
			keyName=""
			{expandedPaths}
			{matchingPaths}
			{searchQuery}
			{togglePath}
			isRoot={true}
		/>
	</div>
</div>

<style>
	.json-tree-view {
		font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
	}

	:global(.json-tree-view mark) {
		background-color: rgba(234, 179, 8, 0.5);
		color: white;
		border-radius: 2px;
		padding: 0 2px;
	}
</style>
